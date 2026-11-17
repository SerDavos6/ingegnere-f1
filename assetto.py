"""Consigli d'assetto dalla telemetria (a regole, senza AI).

Idea di base: in curva le gomme lavorano con un angolo di deriva (slip angle).
Se le anteriori scivolano più delle posteriori l'auto sottosterza, se scivolano di più
le posteriori sovrasterza. Il gioco manda l'angolo di deriva di ogni ruota nel pacchetto
"Motion Ex" (solo per la tua auto), quindi possiamo misurare il bilanciamento in ogni fase:

  entrata  (in frenata)            -> bilanciamento freni, differenziale in rilascio
  centro   (né freno né gas pieno) -> barre antirollio (curve lente), ali (curve veloci)
  uscita   (sul gas)               -> differenziale in accelerazione

Si aggiungono bloccaggi in frenata, pattinamento in uscita e temperature delle gomme.

I dati si azzerano quando torni ai box (garage), così dopo un cambio d'assetto
l'analisi riparte da capo. Le soglie sono indicative: vanno confermate in pista.
"""

SLOW_KMH = 140
FAST_KMH = 200
MIN_LAT_G = 0.8         # sotto questo valore non si è davvero in curva
UNDER_DEG = 1.5         # anteriori che scivolano più delle posteriori di oltre 1,5° = sottosterzo
OVER_DEG = -1.0         # posteriori che scivolano più delle anteriori di oltre 1° = sovrasterzo
MIN_SAMPLES = 90        # circa 1,5 secondi di curva in quella fase (a 60 Hz)
LOCK_SLIP = -0.20       # slip ratio in frenata: ruota quasi bloccata
SPIN_SLIP = 0.12        # slip ratio in accelerazione: ruota che pattina
MIN_LAPS = 2            # giri completi prima di dare consigli


class SetupAdvisor:
    def __init__(self):
        self.reset()

    def reset(self):
        self.ex = None
        self.bins = {}                  # (fase, tipo curva) -> [campioni, somma bilanciamento grezzo]
        self.abs_sum, self.abs_n = 0.0, 0
        self.lock_front = self.lock_rear = self.spins = 0
        self.brake_events = self.exit_events = 0
        self._in_lock_f = self._in_lock_r = self._in_spin = False
        self._braking = self._exiting = False
        self.temp = {"front": [0.0, 0], "rear": [0.0, 0]}
        self.laps = 0
        self.brake_bias = None
        self.last_said = None
        self.last_said_lap = 0

    # ------------------------------------------------------------- dati in ingresso
    def on_ex(self, ex):
        self.ex = ex

    def on_sample(self, tel, ld, motion, status):
        """Un campione di telemetria della tua auto (circa 60 al secondo)."""
        if ld.get("driver_status") == 0:          # nel garage: probabilmente stai cambiando assetto
            if self.laps or self.abs_n:
                self.reset()
            return
        if self.ex is None or ld.get("pit_status", 0) != 0:
            return
        v, thr, brk = tel["speed"], tel["throttle"], tel["brake"]
        if v < 60:
            return
        self.brake_bias = status.get("brake_bias", self.brake_bias)
        sr, sa = self.ex["slip_ratio"], self.ex["slip_angle"]   # RL, RR, FL, FR

        # bloccaggi in frenata e pattinamento in uscita: contiamo gli episodi, non i campioni
        braking = brk > 0.5 and v > 80
        if braking and not self._braking:
            self.brake_events += 1
        self._braking = braking
        lock_f = braking and min(sr[2], sr[3]) < LOCK_SLIP
        lock_r = braking and min(sr[0], sr[1]) < LOCK_SLIP
        if lock_f and not self._in_lock_f:
            self.lock_front += 1
        if lock_r and not self._in_lock_r:
            self.lock_rear += 1
        self._in_lock_f, self._in_lock_r = lock_f, lock_r

        exiting = thr > 0.7 and v < FAST_KMH and brk < 0.05
        if exiting and not self._exiting:
            self.exit_events += 1
        self._exiting = exiting
        spin = exiting and max(sr[0], sr[1]) > SPIN_SLIP
        if spin and not self._in_spin:
            self.spins += 1
        self._in_spin = spin

        # bilanciamento in curva
        if abs(motion.get("glat", 0.0)) >= MIN_LAT_G:
            front = (abs(sa[2]) + abs(sa[3])) / 2
            rear = (abs(sa[0]) + abs(sa[1])) / 2
            self.abs_sum += (front + rear) / 2
            self.abs_n += 1
            phase = "entrata" if brk > 0.15 else ("uscita" if thr > 0.6 else "centro")
            kind = "lente" if v < SLOW_KMH else ("veloci" if v > FAST_KMH else "medie")
            b = self.bins.setdefault((phase, kind), [0, 0.0])
            b[0] += 1
            b[1] += front - rear

        # temperature superficiali delle gomme
        t = tel.get("tyre_surface")
        if t:
            self.temp["rear"][0] += (t[0] + t[1]) / 2
            self.temp["rear"][1] += 1
            self.temp["front"][0] += (t[2] + t[3]) / 2
            self.temp["front"][1] += 1

    def on_lap(self):
        self.laps += 1

    # -------------------------------------------------------------------- analisi
    def _to_deg(self, x):
        # il gioco manda gli angoli in radianti; se i valori sembrano già in gradi non convertiamo
        mean = self.abs_sum / self.abs_n if self.abs_n else 0
        return x if mean > 0.6 else x * 57.2958

    def balance(self, phase, kind):
        b = self.bins.get((phase, kind))
        if not b or b[0] < MIN_SAMPLES:
            return None
        return self._to_deg(b[1] / b[0])

    def _merge(self, phase, kinds):
        n = sum(self.bins.get((phase, k), [0, 0])[0] for k in kinds)
        if n < MIN_SAMPLES:
            return None
        s = sum(self.bins.get((phase, k), [0, 0.0])[1] for k in kinds)
        return self._to_deg(s / n)

    def advice(self):
        """Lista di consigli: dict con area, problema, consiglio. Vuota se non ci sono abbastanza dati."""
        if self.laps < MIN_LAPS:
            return []
        out = []
        bias = f" (ora è al {self.brake_bias}%)" if self.brake_bias else ""

        # bloccaggi: almeno 3 episodi e più del 15% delle frenate
        if self.brake_events >= 6:
            if self.lock_front >= 3 and self.lock_front / self.brake_events > 0.15:
                out.append({"area": "Freni", "problema": f"blocchi le anteriori in {self.lock_front} frenate su {self.brake_events}",
                            "consiglio": f"sposta il bilanciamento freni indietro di 1 o 2 punti{bias}, oppure abbassa la pressione dei freni"})
            elif self.lock_rear >= 3 and self.lock_rear / self.brake_events > 0.15:
                out.append({"area": "Freni", "problema": f"blocchi le posteriori in {self.lock_rear} frenate su {self.brake_events}",
                            "consiglio": f"sposta il bilanciamento freni avanti di 1 o 2 punti{bias}"})

        slow = self._merge("centro", ("lente", "medie"))
        fast = self.balance("centro", "veloci")
        if slow is not None:
            if slow > UNDER_DEG:
                out.append({"area": "Curve lente", "problema": "sottosterzo a centro curva",
                            "consiglio": "barra antirollio anteriore più morbida di 1 o 2 punti (o posteriore più rigida)"})
            elif slow < OVER_DEG:
                out.append({"area": "Curve lente", "problema": "sovrasterzo a centro curva",
                            "consiglio": "barra antirollio posteriore più morbida di 1 o 2 punti (o anteriore più rigida)"})
        if fast is not None:
            if fast > UNDER_DEG:
                out.append({"area": "Curve veloci", "problema": "sottosterzo",
                            "consiglio": "ala anteriore più carica di 1 o 2 punti"})
            elif fast < OVER_DEG:
                out.append({"area": "Curve veloci", "problema": "sovrasterzo",
                            "consiglio": "ala posteriore più carica di 1 punto (o anteriore meno carica)"})
            elif slow is not None and fast - slow > 1.2:
                out.append({"area": "Curve veloci", "problema": "più sottosterzo nelle veloci che nelle lente",
                            "consiglio": "ala anteriore più carica di 1 punto"})

        entry = self._merge("entrata", ("lente", "medie", "veloci"))
        if entry is not None:
            if entry > UNDER_DEG and not any(a["area"] == "Freni" and "posteriori" in a["problema"] for a in out):
                out.append({"area": "Ingresso curva", "problema": "sottosterzo in frenata",
                            "consiglio": f"differenziale in rilascio più aperto e bilanciamento freni indietro di 1 punto{bias}"})
            elif entry < OVER_DEG:
                out.append({"area": "Ingresso curva", "problema": "sovrasterzo in frenata",
                            "consiglio": f"differenziale in rilascio più chiuso e bilanciamento freni avanti di 1 punto{bias}"})

        exit_ = self._merge("uscita", ("lente", "medie"))
        spin_rate = self.spins / self.exit_events if self.exit_events else 0
        if self.exit_events >= 6 and self.spins >= 3 and spin_rate > 0.25:
            out.append({"area": "Uscita curva", "problema": f"pattini in uscita ({self.spins} volte su {self.exit_events})",
                        "consiglio": "differenziale in accelerazione più aperto di 5–10 punti e gas più progressivo"})
        elif exit_ is not None and exit_ < OVER_DEG:
            out.append({"area": "Uscita curva", "problema": "sovrasterzo in trazione",
                        "consiglio": "differenziale in accelerazione più aperto di 5 punti"})
        elif exit_ is not None and exit_ > UNDER_DEG:
            out.append({"area": "Uscita curva", "problema": "sottosterzo in trazione",
                        "consiglio": "differenziale in accelerazione più chiuso di 5 punti"})

        tf = self.temp["front"][0] / self.temp["front"][1] if self.temp["front"][1] else None
        tr = self.temp["rear"][0] / self.temp["rear"][1] if self.temp["rear"][1] else None
        if tf and tr:
            for name, t in (("anteriori", tf), ("posteriori", tr)):
                if t > 105:
                    out.append({"area": "Gomme", "problema": f"gomme {name} calde, in media {t:.0f} gradi",
                                "consiglio": f"pressioni {name} più basse di 0,2–0,4 psi"})
                elif t < 80:
                    out.append({"area": "Gomme", "problema": f"gomme {name} fredde, in media {t:.0f} gradi",
                                "consiglio": f"pressioni {name} più alte di 0,2–0,4 psi"})
        return out

    def summary(self):
        """Dati grezzi per l'AI e la dashboard."""
        bal = {}
        for (phase, kind) in self.bins:
            v = self.balance(phase, kind)
            if v is not None:
                bal[f"{phase}_{kind}"] = round(v, 2)
        return {"giri_analizzati": self.laps,
                "bilanciamento_gradi": bal,
                "nota": "bilanciamento = deriva media anteriori meno posteriori: positivo sottosterzo, negativo sovrasterzo",
                "bloccaggi_anteriori": self.lock_front, "bloccaggi_posteriori": self.lock_rear,
                "frenate": self.brake_events, "pattinamenti_uscita": self.spins, "uscite": self.exit_events,
                "consigli": self.advice()}

    def text(self, limit=3):
        """Frase per la voce."""
        adv = self.advice()
        if self.laps < MIN_LAPS:
            return f"Per i consigli d'assetto mi servono almeno {MIN_LAPS} giri completi con lo stesso assetto."
        if not adv:
            return "L'assetto mi sembra equilibrato: niente sottosterzo o sovrasterzo evidente, né bloccaggi."
        parts = [f"{a['area']}: {a['problema']}, {a['consiglio']}" for a in adv[:limit]]
        return "Consigli d'assetto. " + ". ".join(parts) + "."

    def new_advice_to_say(self):
        """Il consiglio più importante, solo se è nuovo e sono passati almeno 3 giri dall'ultimo."""
        adv = self.advice()
        if not adv or self.laps - self.last_said_lap < 3 and self.last_said_lap:
            return None
        key = (adv[0]["area"], adv[0]["problema"].split(" in ")[0])
        if key == self.last_said:
            return None
        self.last_said, self.last_said_lap = key, self.laps
        a = adv[0]
        more = " Gli altri consigli li trovi in Dettagli, Assetto." if len(adv) > 1 else ""
        return f"Assetto, {a['area'].lower()}: {a['problema']}. Consiglio: {a['consiglio']}.{more}"
