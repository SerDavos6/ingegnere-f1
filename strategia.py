"""Strategia di gara: quando fermarsi, che gomma montare, safety car.

Le stime partono dall'usura reale delle gomme (quanto sale a ogni giro) e da quanto
durano in proporzione le altre mescole. Prima di avere 3 giri di dati usa la finestra
pit del gioco. Sono stime da ingegnere, non certezze: per questo l'ingegnere dice "circa".
"""
from parlato import plural

LIMIT = 70.0                       # usura oltre la quale le gomme calano molto
SLICKS = ("supersoft", "soft", "medie", "dure")      # supersoft: solo F2
# durata relativa delle mescole (soft = 1)
LIFE = {"supersoft": 0.75, "soft": 1.0, "medie": 1.45, "dure": 1.9}
SC_FACTOR = {1: 0.55, 2: 0.7}      # quanto costa il pit con safety car / VSC rispetto al normale


class Strategy:
    def __init__(self, race):
        self.r = race
        self.reset()

    def reset(self):
        self.used = set()              # mescole slick usate in gara
        self.last_plan = None
        self.last_plan_lap = -99
        self.box_called = set()
        self.box_pending = None       # chiamata box in attesa della tua risposta (sì / allunghiamo)
        self.extend_until = 0         # "allunghiamo": non richiamo prima di questo giro
        self.team_lap = 0             # soste divise con il compagno: il giro deciso per noi
        self.rain_hold = 0            # pioggia in arrivo: niente sosta per le slick prima di questo giro

    # ------------------------------------------------------------- dati
    @property
    def c(self):
        return self.r.c

    def note_compound(self):
        comp = self.c.status.get("compound")
        if comp in SLICKS:
            self.used.add(comp)

    def wear_rate(self):
        h = self.r.wear_hist
        if len(h) < 3:
            return None
        (l0, w0), (l1, w1) = h[0], h[-1]
        rate = (w1 - w0) / max(1, l1 - l0)
        return rate if rate > 0.2 else None

    def prior_rate(self):
        """Prima di avere 3 giri di gara: l'usura misurata nella prova lunga delle prove libere."""
        try:
            return self.c.practice.rate_for(self.c.status.get("compound"))
        except Exception:
            return None

    def remaining(self, me):
        total = self.c.session.get("total_laps", 0)
        return max(0, total - me["lap_num"] + 1) if total else None

    def dry(self):
        return self.c.session.get("weather", 0) < 3 and self.c.status.get("compound") in SLICKS

    def must_change_compound(self, me):
        """Regola delle due mescole: in gara asciutta serve almeno una sosta con una mescola diversa."""
        total = self.c.session.get("total_laps", 0)
        return self.dry() and total >= 10 and me["pit_stops"] == 0 and len(self.used) < 2

    def life(self, compound, rate_now, comp_now):
        """Giri che dura una mescola nuova, stimati dall'usura di quella che hai."""
        if not rate_now or comp_now not in LIFE or compound not in LIFE:
            return None
        return LIMIT / rate_now * LIFE[compound] / LIFE[comp_now]

    def slicks(self):
        """Le mescole che esistono per questa categoria: la supersoft c'è solo in F2
        (in una gara vera con le F1 2026 l'ingegnere proponeva la supersoft)."""
        if (self.c.category or "").startswith("f2"):
            return SLICKS
        return tuple(c for c in SLICKS if c != "supersoft")

    def pick_compound(self, laps_needed, rate, comp_now, exclude=()):
        """La mescola più veloce (più morbida) che arriva fino in fondo."""
        options = [c for c in self.slicks() if c not in exclude]
        for comp in options:
            life = self.life(comp, rate, comp_now)
            if life is not None and life >= laps_needed * 1.05:
                return comp, True
        return (options[-1] if options else "dure"), False

    # ------------------------------------------------------------- piano
    def plan(self):
        """Piano attuale: {'stop': bool, 'lap': giro del pit, 'compound': mescola, 'two_stops': bool, 'why': ...}"""
        me = self.r.me()
        if not me or not self.r.is_race or not self.dry():
            return None
        rem = self.remaining(me)
        if rem is None:
            return None
        lap, wear = me["lap_num"], self.r._wear() or 0.0
        comp_now = self.c.status.get("compound")
        rate = self.wear_rate()
        practice = False
        if rate is None and me["pit_stops"] == 0 and lap <= 5:
            rate = self.prior_rate()             # stima dalle prove libere
            practice = rate is not None
        must = self.must_change_compound(me)
        s = self.c.session
        ideal, latest = s.get("pit_ideal", 0), s.get("pit_latest", 0)

        if rate is None:                               # pochi dati: finestra del gioco
            if ideal and me["pit_stops"] == 0 and latest >= lap:
                return {"stop": True, "lap": max(ideal, lap), "lap_to": latest, "compound": None,
                        "two_stops": False, "why": "finestra"}
            return None

        laps_left_now = max(0.0, (LIMIT - wear) / rate)
        if laps_left_now >= rem and not must:
            return {"stop": False, "why": "arrivi in fondo", "laps_left": laps_left_now, "practice": practice}

        if laps_left_now >= rem:                       # serve solo per la regola delle mescole
            # nella finestra del gioco; se è già passata, entro il penultimo giro
            pit_lap = latest if latest and latest >= lap else lap + rem - 2
        else:
            pit_lap = lap + int(laps_left_now)
            if ideal and me["pit_stops"] == 0 and ideal <= pit_lap and latest:
                pit_lap = min(pit_lap, latest)
        pit_lap = max(lap, min(pit_lap, lap + rem - 2))
        after = max(1, rem - (pit_lap - lap))
        exclude = (comp_now,) if must else ()
        comp, ok = self.pick_compound(after, rate, comp_now, exclude)
        return {"stop": True, "lap": pit_lap, "lap_to": None, "compound": comp, "two_stops": not ok,
                "why": "usura" if laps_left_now < rem else "regola", "after": after, "practice": practice}

    def describe(self, p=None):
        p = p or self.plan()
        me = self.r.me()
        if not self.r.is_race:
            return "La strategia vale solo in gara."
        if not self.dry():
            return "Con il bagnato decidiamo giro per giro: ti avviso io quando cambiare gomme."
        if not p:
            return "Mi servono ancora un paio di giri per capire l'usura delle gomme."
        if not p["stop"]:
            if me and me.get("pit_stops"):
                return "Strategia: nessun'altra sosta, con questo ritmo le gomme arrivano fino in fondo."
            return "Strategia: nessuna sosta, con questo ritmo le gomme arrivano fino in fondo."
        if p["why"] == "finestra":
            return f"Strategia: una sosta tra il giro {p['lap']} e il {p['lap_to']}. La gomma te la dico più avanti."
        when = ("in questo giro" if me and p["lap"] <= me["lap_num"]
                else f"entro il giro {p['lap']}" if p["why"] == "regola" else f"verso il giro {p['lap']}")
        txt = f"Strategia: sosta {when}, poi {p['compound']}"
        txt += " fino alla fine." if not p["two_stops"] else ", ma servirà probabilmente una seconda sosta."
        if p["why"] == "regola":
            txt += " Le gomme reggono, ci fermiamo per la regola delle due mescole."
        if p.get("practice"):
            txt += " È una stima dalle prove libere: la aggiorno dopo i primi giri."
        return txt

    # ------------------------------------------------------- ogni giro
    def on_lap(self, me, remaining):
        """Annuncia il piano quando cambia e chiama il box quando è il momento."""
        self.note_compound()
        say, lap = self.c.say, me["lap_num"]
        if remaining is None or remaining <= 1 or me["pit_status"]:
            return
        p = self.plan()
        if not p:
            return
        key = (p["stop"], p.get("lap"), p.get("compound"), p.get("two_stops"))
        changed = (self.last_plan is None or key[0] != self.last_plan[0] or key[2] != self.last_plan[2]
                   or (key[1] and self.last_plan[1] and abs(key[1] - self.last_plan[1]) >= 3))
        if changed and lap - self.last_plan_lap >= 4:
            first = self.last_plan is None or self.last_plan[2] is None
            self.last_plan, self.last_plan_lap = key, lap
            say(("" if first else "Cambio di piano. ") + self.describe(p), "strategia", level=1 if first else 2)

        # box adesso (o di nuovo, dopo un "allunghiamo")
        plap = p.get("lap")
        due = plap is not None and (plap == lap or (self.extend_until and lap >= self.extend_until and plap <= lap))
        if self.team_lap and not self.extend_until:
            due = plap is not None and lap == self.team_lap
        if self.rain_hold and lap < self.rain_hold:
            due = False
        if self.box_pending and self.box_pending["stops"] != me["pit_stops"]:
            self.box_pending = None                  # la sosta è fatta
        if p["stop"] and p.get("compound") and due and (lap, me["pit_stops"]) not in self.box_called:
            self.box_called.add((lap, me["pit_stops"]))
            mate = self.r.team.hold_box(me)
            if mate:
                # il compagno davanti si ferma adesso: noi il giro dopo, per non aspettare dietro di lui ai box
                self.extend_until = lap + 1
                say(f"{mate} si ferma questo giro: noi entriamo al prossimo, così non facciamo la fila ai box.",
                    "strategia", level=1, urgent=True)
                return
            self.extend_until = 0
            self.team_lap = 0
            est = self.r.rejoin_estimate(self.c.pit_loss)
            txt = f"Box, box questo giro. Montiamo le {p['compound']}."
            if est:
                txt += f" Rientri circa P{est['pos']}"
                if est.get("ahead"):
                    txt += f", dietro {est['ahead']['name']}"
                txt += "."
            txt += " Confermi?"
            self.box_pending = {"lap": lap, "stops": me["pit_stops"], "t": self.c.now, "comp": p["compound"],
                                "state": "chiesto", "reminded": False}
            say(txt, "strategia", level=1, urgent=True)

    # ---------------------------------------------------- la tua risposta alla chiamata box
    def pending_box(self):
        b = self.box_pending
        return b if b and b["state"] in ("chiesto", "confermato") and self.c.now - b["t"] < 90 else None

    def answer_box(self, yes):
        b = self.pending_box()
        if not b:
            return None
        me = self.r.me()
        lap = me["lap_num"] if me else b["lap"]
        if yes:
            b["state"] = "confermato"
            name = self.c.driver_name
            return (f"Ricevuto{', ' + name if name else ''}. Box questo giro, prepariamo le {b['comp']}. "
                    "Ti chiamo io all'ingresso.")
        self.box_pending = None
        self.extend_until = lap + 2
        return "Ok, allunghiamo. Tieni d'occhio le gomme: te lo richiedo tra due giri."

    def on_live(self, me):
        """All'ultimo settore del giro di sosta: "box, box, entra ai box"."""
        b = self.pending_box()
        if not b or b["reminded"] or me["lap_num"] != b["lap"] or me["pit_status"]:
            return
        length = self.c.session.get("track_length") or 0
        d = me.get("lap_distance") or 0
        if length and d > length - 700:
            b["reminded"] = True
            self.c.say("Box, box. Entra ai box.", "strategia", level=1, urgent=True)

    # ---------------------------------------------------- safety car
    def sc_advice(self, sc):
        """Conviene fermarsi con safety car (1) o virtual safety car (2)?"""
        me = self.r.me()
        name = "safety car" if sc == 1 else "virtual safety car"
        if not me:
            return ""
        rem = self.remaining(me) or 99
        wear = self.r._wear() or 0.0
        loss = self.c.pit_loss * SC_FACTOR[sc]
        est = self.r.rejoin_estimate(loss)
        now_pos = me["position"]
        lost = (est["pos"] - now_pos) if est else 0
        where = f"rientreresti circa P{est['pos']}" if est else ""

        if me["pit_status"]:
            return ""
        if not self.dry():
            if self.c.session.get("weather", 0) < 3 and self.c.status.get("compound") not in SLICKS:
                return f"Con la {name} la pista è asciutta: box per le slick, {where}."
            return ""
        if rem <= 2:
            return f"Mancano {plural(rem, 'giro', 'giri')}: resta fuori."
        rain = next((f for f in self.c.session.get("forecast", [])
                     if 0 < f["min"] <= 10 and (f["weather"] >= 3 or f["rain"] >= 50)), None)
        if rain:
            return (f"Pioggia prevista tra {rain['min']} minuti: se ti fermi adesso rischi di dover rientrare. "
                    "Resta fuori e ci fermiamo direttamente per le intermedie.")
        p = self.plan()
        must = self.must_change_compound(me)
        need = must or bool(p and p.get("stop"))
        rate = self.wear_rate()
        comp_now = self.c.status.get("compound")
        comp, ok = self.pick_compound(rem, rate, comp_now, (comp_now,) if must else ())
        if rate is None:
            ok = True                                  # senza dati di usura non sconsiglio la sosta
        saved = round(self.c.pit_loss - loss)
        tail = f", {where}." if where else "."
        if need and ok:
            return f"Con la {name} la sosta costa circa {saved} secondi in meno. Box, box: montiamo le {comp}{tail}"
        if need and p and p.get("lap", 99) - me["lap_num"] <= 5:
            return (f"Con la {name} conviene anticipare la sosta: box per le {comp}{tail} "
                    "Servirà probabilmente un'altra sosta più avanti.")
        if need:
            return f"Resta fuori: è presto, le gomme nuove non arriverebbero in fondo. Ci fermiamo più avanti."
        if wear >= 40 and rem >= 8 and lost <= 1:
            return (f"Possiamo approfittare della {name}: perdi al massimo una posizione e riparti con gomme nuove. "
                    f"Box per le {comp}{tail}")
        if est:
            extra = f", perderesti {plural(lost, 'posizione', 'posizioni')}" if lost > 0 else ""
            return f"Resta fuori: le gomme arrivano in fondo e {where}{extra}."
        return "Resta fuori, le gomme arrivano in fondo."
