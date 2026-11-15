"""Logica dell'ingegnere di pista: giro di riferimento, analisi per staccata, avvisi.

Tutto ciò che va detto DURANTE la guida è calcolato con regole deterministiche
(latenza zero). L'AI (engineer_ai.py) interviene solo per debrief e domande.

Livelli di parlato (scelti dalla dashboard):
  1 = poco     solo cose importanti (safety car, meteo, box, giro invalidato, danni)
  2 = normale  + distacchi a ogni giro, consigli di guida in prova/qualifica
  3 = tanto    + consigli di guida anche in gara, complimenti, dettagli
"""
import bisect
import json
import os

from parlato import fmt_lap, fmt_lap_voice, num, tenths
from race import RaceEngineer
from recorder import Recorder

GRID_STEP = 5.0           # metri tra un punto e l'altro del giro di riferimento
BRAKE_ON = 0.15           # soglia pedale freno per considerare "in frenata"
MIN_GAP_BETWEEN_ZONES = 120.0
SPEAK_COOLDOWN_S = 5.0    # pausa minima tra due avvisi vocali "non urgenti"

REF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "riferimenti")
LAP_FIELDS = ("dist", "t", "speed", "thr", "brk", "gear", "x", "z")


class Lap:
    """Campioni di un giro, ordinati per distanza."""

    def __init__(self):
        for k in LAP_FIELDS:
            setattr(self, k, [])

    def add(self, d, t, speed, thr, brk, gear, x=0.0, z=0.0):
        if self.dist and d <= self.dist[-1]:
            if d < self.dist[-1] - 50:      # flashback: torno indietro nel giro
                self.truncate(d)
            else:
                return
        for k, v in zip(LAP_FIELDS, (d, t, speed, thr, brk, gear, x, z)):
            getattr(self, k).append(v)

    def truncate(self, d):
        i = bisect.bisect_left(self.dist, d)
        for k in LAP_FIELDS:
            del getattr(self, k)[i:]

    def interp(self, arr, d):
        i = bisect.bisect_left(self.dist, d)
        if i <= 0:
            return arr[0]
        if i >= len(self.dist):
            return arr[-1]
        d0, d1 = self.dist[i - 1], self.dist[i]
        f = 0 if d1 == d0 else (d - d0) / (d1 - d0)
        return arr[i - 1] + (arr[i] - arr[i - 1]) * f

    def __len__(self):
        return len(self.dist)


class Reference:
    """Giro di riferimento ricampionato ogni GRID_STEP metri + staccate individuate."""

    def __init__(self, lap_ms, grid):
        self.lap_ms = lap_ms
        self.g = grid
        self.zones = self._find_zones()

    @classmethod
    def from_lap(cls, lap, lap_ms):
        n = int(lap.dist[-1] // GRID_STEP)
        g = {k: [] for k in ("dist", "t", "speed", "thr", "brk")}
        for i in range(n + 1):
            d = i * GRID_STEP
            g["dist"].append(d)
            g["t"].append(lap.interp(lap.t, d))
            g["speed"].append(lap.interp(lap.speed, d))
            g["thr"].append(lap.interp(lap.thr, d))
            g["brk"].append(lap.interp(lap.brk, d))
        return cls(lap_ms, g)

    def _find_zones(self):
        g, zones = self.g, []
        n, i = len(g["dist"]), 1
        while i < n:
            if g["brk"][i] >= BRAKE_ON and g["brk"][i - 1] < BRAKE_ON:
                start = g["dist"][i]
                if zones and start - zones[-1]["exit"] < MIN_GAP_BETWEEN_ZONES:
                    i += 1
                    continue
                j, vmin, apex = i, g["speed"][i], start
                while j < n and g["dist"][j] - start < 600:
                    if g["speed"][j] < vmin:
                        vmin, apex = g["speed"][j], g["dist"][j]
                    if g["dist"][j] > apex + 10 and g["thr"][j] > 0.9 and g["brk"][j] < 0.05:
                        break
                    j += 1
                full = g["dist"][min(j, n - 1)]
                zones.append({"n": len(zones) + 1, "brake": start, "apex": apex,
                              "vmin": vmin, "full": full, "exit": full + 30})
                i = j
            i += 1
        return zones

    def t_at(self, d):
        if d <= 0:
            return 0.0
        k = d / GRID_STEP
        i = int(k)
        if i >= len(self.g["t"]) - 1:
            return self.g["t"][-1]
        f = k - i
        return self.g["t"][i] + (self.g["t"][i + 1] - self.g["t"][i]) * f

    def to_json(self):
        return {"lap_ms": self.lap_ms, "grid": self.g}


class Coach:
    def __init__(self, emit, data_dir=None, ref_dir=None):
        self.emit = emit            # emit(tipo, testo, parla: bool, priorità)
        self.ref_dir = ref_dir or REF_DIR
        self.verbosity = 2
        self.pit_loss = 22.0        # secondi persi con un pit stop (stima, modificabile in config.json)
        self.recorder = Recorder(data_dir) if data_dir else Recorder()
        self.race = RaceEngineer(self)
        self.reset()

    # ------------------------------------------------------------ stato
    def reset(self):
        self.session_uid = None
        self.player_idx = 0
        self.track_id = None
        self.track_name = "-"
        self.ref = None
        self.lap = Lap()
        self.lap_num = None
        self.lap_invalid = False
        self.last_lapdata = None
        self.telemetry, self.status, self.damage, self.session, self.motion = {}, {}, {}, {}, {}
        self.analyzed = set()
        self.lap_zone_results = []
        self.history = []
        self.last_speak_t = -99.0
        self.now = 0.0
        self.warned = {}
        self.race.reset()

    def say(self, text, kind="info", level=2, urgent=False):
        """Mostra sempre il messaggio; lo legge a voce solo se il livello scelto lo prevede."""
        spoken = level <= self.verbosity
        if spoken and not urgent and self.now - self.last_speak_t < SPEAK_COOLDOWN_S:
            spoken = False
        if spoken:
            self.last_speak_t = self.now
        self.emit(kind, text, spoken, 2 if urgent else 1)

    def _once(self, key, interval):
        last = self.warned.get(key, -1e9)
        if self.now - last >= interval:
            self.warned[key] = self.now
            return True
        return False

    @property
    def in_race(self):
        return bool(self.session.get("is_race"))

    # --------------------------------------------------------- ingressi
    def on_header(self, h):
        self.now = h["session_time"]
        self.player_idx = h["player_idx"]
        if h["session_uid"] != self.session_uid:
            keep = self.session_uid is not None
            self.reset()
            self.session_uid = h["session_uid"]
            self.player_idx = h["player_idx"]
            if keep:
                self.emit("sistema", "Nuova sessione rilevata.", False, 0)

    def on_session(self, s):
        first = not self.session
        self.session = s
        if s["track_id"] != self.track_id:
            self.track_id = s["track_id"]
            self.track_name = s["track_name"]
            self.ref = self._load_ref()
            msg = f"Pista: {self.track_name}, {s['session_name']}."
            if self.ref:
                msg += f" Riferimento: {fmt_lap(self.ref.lap_ms)}, {len(self.ref.zones)} staccate."
            self.emit("sistema", msg, False, 0)
        if first and s["is_race"] and s["total_laps"]:
            self.say(f"Gara di {s['total_laps']} giri a {self.track_name}. Pista a {s['track_temp']} gradi. Buona gara!",
                     "gara", level=1, urgent=True)
        self.race.on_session(s)

    def on_lap(self, ld):
        prev = self.last_lapdata
        self.last_lapdata = ld
        if self.lap_num is None:
            self.lap_num = ld["lap_num"]
        if ld["lap_num"] != self.lap_num:
            if prev and ld["lap_num"] == self.lap_num + 1:
                self._finish_lap(ld["last_lap_ms"], prev)
            self.lap = Lap()
            self.analyzed = set()
            self.lap_zone_results = []
            self.lap_invalid = False
            self.lap_num = ld["lap_num"]
        if ld["invalid"] and not self.lap_invalid:
            self.lap_invalid = True
            self.say("Giro invalidato, limiti della pista.", "avviso", level=1, urgent=True)

    def on_laps_all(self, cars):
        self.race.on_laps(cars)

    def on_motion(self, m):
        self.motion = m

    def on_telemetry(self, tel):
        self.telemetry = tel
        ld = self.last_lapdata
        if not ld or ld["pit_status"] != 0:
            return
        d = ld["lap_distance"]
        if d < 0:
            return
        before = len(self.lap)
        self.lap.add(d, ld["current_lap_ms"] / 1000.0, tel["speed"], tel["throttle"],
                     tel["brake"], tel["gear"], self.motion.get("x", 0.0), self.motion.get("z", 0.0))
        if len(self.lap) < before and self.ref:   # flashback
            self.analyzed = {n for n in self.analyzed if self.ref.zones[n - 1]["exit"] < d}
        self._check_zones(d)
        self._check_tyres(tel)

    def on_status(self, st):
        self.status = st
        fl = st.get("fuel_laps")
        if fl is not None and fl < -0.3 and self.in_race and self._once("fuel", 90):
            self.say(f"Carburante: sei sotto di {num(fl)} {'giro' if num(fl) == '1' else 'giri'}. Alza e lascia scorrere prima delle frenate.",
                     "avviso", level=1, urgent=True)

    def on_damage(self, dm):
        self.damage = dm
        worst = max(dm["tyre_wear"])
        if worst >= 70 and self._once("wear70", 120):
            self.say(f"Usura gomme al {worst:.0f} per cento. Valuta il pit.", "avviso", level=1, urgent=True)
        wing = dm.get("front_wing", 0)
        if wing >= 25 and wing > self.warned.get("wing_level", 0) + 10:
            self.warned["wing_level"] = wing
            self.say(f"Ala anteriore danneggiata al {wing} per cento. Valuta di cambiarla al pit.",
                     "avviso", level=1, urgent=True)

    # ------------------------------------------------------- analisi
    def _check_zones(self, d):
        if not self.ref or len(self.lap) < 10:
            return
        for z in self.ref.zones:
            if z["n"] in self.analyzed or d < z["exit"]:
                continue
            self.analyzed.add(z["n"])
            if self.lap.dist[0] > z["brake"] - 150:   # giro iniziato a metà: niente confronto
                continue
            self._analyze_zone(z)

    def _analyze_zone(self, z):
        lap, ref = self.lap, self.ref
        a, b = z["brake"] - 150, z["exit"]
        loss = (lap.interp(lap.t, b) - lap.interp(lap.t, a)) - (ref.t_at(b) - ref.t_at(a))
        i0 = bisect.bisect_left(lap.dist, a)
        i1 = bisect.bisect_right(lap.dist, b)
        brake_pt, vmin, full_pt = None, 999, None
        for i in range(i0, i1):
            if brake_pt is None and lap.brk[i] >= BRAKE_ON:
                brake_pt = lap.dist[i]
            if lap.dist[i] >= z["brake"] - 50:
                vmin = min(vmin, lap.speed[i])
        if brake_pt is not None:
            for i in range(i0, i1):
                if lap.dist[i] > brake_pt + 30 and lap.thr[i] > 0.9 and lap.brk[i] < 0.05:
                    full_pt = lap.dist[i]
                    break
        res = {"n": z["n"], "loss": round(loss, 3), "brake_delta": None,
               "vmin_delta": round(vmin - z["vmin"], 1) if vmin < 999 else None,
               "full_delta": None, "at": z["brake"]}
        if brake_pt is not None:
            res["brake_delta"] = round(brake_pt - z["brake"], 1)   # negativo = frena prima
        if full_pt is not None:
            res["full_delta"] = round(full_pt - z["full"], 1)      # positivo = gas dopo
        self.lap_zone_results.append(res)

        tip_level = 3 if self.in_race else 2   # in gara i consigli di guida solo con "tanto"
        tag = f"Staccata {z['n']}"
        if loss >= 0.08:
            bd, vd, fd = res["brake_delta"], res["vmin_delta"], res["full_delta"]
            if bd is not None and bd <= -12:
                tip = f"freni {abs(bd):.0f} metri prima, puoi ritardare"
            elif bd is not None and bd >= 12 and vd is not None and vd <= -3:
                tip = "freni troppo tardi e perdi velocità a centro curva"
            elif vd is not None and vd <= -4:
                tip = f"porti {abs(vd):.0f} chilometri orari in meno a centro curva, rilascia il freno prima"
            elif fd is not None and fd >= 20:
                tip = f"torni sul gas pieno {fd:.0f} metri dopo"
            else:
                tip = "trazione in uscita da migliorare"
            self.say(f"{tag}: {tip}. Perdi {tenths(loss)}.", "consiglio", level=tip_level)
        elif loss <= -0.12:
            self.say(f"{tag}: bene, guadagni {tenths(loss)}.", "consiglio", level=3)

    def _check_tyres(self, tel):
        temps = tel["tyre_surface"]
        hot = [i for i, x in enumerate(temps) if x >= 110]
        if hot and self._once("tyrehot", 45):
            names = ["posteriore sinistra", "posteriore destra", "anteriore sinistra", "anteriore destra"]
            self.say(f"Gomma {names[hot[0]]} surriscaldata, {temps[hot[0]]} gradi. Meno slittamento in uscita.",
                     "avviso", level=2)

    # --------------------------------------------------- fine giro
    def _finish_lap(self, lap_ms, prev_ld):
        valid = not self.lap_invalid
        track_len = self.session.get("track_length") or 0
        complete = (len(self.lap) > 50 and self.lap.dist[0] < 100
                    and (not track_len or self.lap.dist[-1] > track_len * 0.9))
        entry = {"lap": self.lap_num, "ms": lap_ms, "valid": valid, "zones": self.lap_zone_results}
        self.history.append(entry)
        self.history = self.history[-30:]

        if complete:
            s1 = prev_ld.get("s1_ms") or 0
            s2 = prev_ld.get("s2_ms") or 0
            info = dict(entry, s1=s1, s2=s2, s3=lap_ms - s1 - s2 if s1 and s2 else 0,
                        compound=self.status.get("compound"), tyre_age=self.status.get("tyre_age"),
                        position=prev_ld.get("position"))
            try:
                self.recorder.save_lap(self.session_uid, self.session, self.lap, info)
            except OSError as e:
                print("Impossibile salvare il giro:", e, flush=True)

        best_before = self.ref.lap_ms if self.ref else None
        parts = [f"Giro {fmt_lap_voice(lap_ms)}"]
        if not valid:
            parts.append("non valido")
        elif best_before:
            delta = (lap_ms - best_before) / 1000.0
            parts.append(("più " if delta >= 0 else "meno ") + tenths(delta) + " dal migliore")
        worst = sorted([r for r in self.lap_zone_results if r["loss"] > 0.05], key=lambda r: -r["loss"])[:2]
        if worst:
            parts.append("perdi di più alla staccata " + " e ".join(str(r["n"]) for r in worst))

        lap_level = 3 if self.in_race else 1   # in gara il riepilogo lo fa l'ingegnere di gara
        if valid and complete and (best_before is None or lap_ms < best_before):
            self.ref = Reference.from_lap(self.lap, lap_ms)
            self._save_ref()
            text = f"Nuovo riferimento: {fmt_lap_voice(lap_ms)}. Ho trovato {len(self.ref.zones)} staccate."
            if best_before:
                text = f"Giro migliore! {fmt_lap_voice(lap_ms)}, meno {tenths((best_before - lap_ms) / 1000)}."
            self.say(text, "giro", level=2 if self.in_race else 1, urgent=True)
        else:
            self.say(", ".join(parts) + ".", "giro", level=lap_level, urgent=True)
        self.emit("fine_giro", json.dumps(entry), False, 0)
        self.race.on_lap_complete()

    # ------------------------------------------------ persistenza
    def _ref_path(self):
        return os.path.join(self.ref_dir, f"pista_{self.track_id}.json")

    def _save_ref(self):
        if self.track_id is None:
            return
        os.makedirs(self.ref_dir, exist_ok=True)
        with open(self._ref_path(), "w") as f:
            json.dump(self.ref.to_json(), f)

    def _load_ref(self):
        try:
            with open(self._ref_path()) as f:
                j = json.load(f)
            return Reference(j["lap_ms"], j["grid"])
        except (OSError, ValueError, KeyError):
            return None

    # ----------------------------------------------- per la dashboard
    def snapshot(self):
        ld, tel, st = self.last_lapdata or {}, self.telemetry, self.status
        delta = None
        if self.ref and ld and len(self.lap) > 5 and ld.get("lap_distance", -1) > 0:
            delta = round(ld["current_lap_ms"] / 1000.0 - self.ref.t_at(ld["lap_distance"]), 2)
        last = self.history[-1] if self.history else None
        return {
            "track": self.track_name, "lap": ld.get("lap_num"),
            "position": ld.get("position"), "current_ms": ld.get("current_lap_ms"),
            "last_ms": last["ms"] if last else None,
            "best_ms": self.ref.lap_ms if self.ref else None,
            "delta": delta, "invalid": self.lap_invalid,
            "speed": tel.get("speed"), "gear": tel.get("gear"),
            "throttle": tel.get("throttle"), "brake": tel.get("brake"),
            "tyre_temp": tel.get("tyre_surface"), "tyre_wear": self.damage.get("tyre_wear"),
            "front_wing": self.damage.get("front_wing"),
            "fuel_laps": st.get("fuel_laps"), "compound": st.get("compound"),
            "tyre_age": st.get("tyre_age"),
            "ers": round(st["ers_store"] / 4e6 * 100) if st.get("ers_store") is not None else None,
            "verbosity": self.verbosity,
            "race": self.race.snapshot(),
        }

    def context_for_ai(self):
        """Riassunto compatto per l'ingegnere AI."""
        laps = [{"giro": h["lap"], "tempo": fmt_lap(h["ms"]), "valido": h["valid"],
                 "staccate": h["zones"]} for h in self.history[-5:]]
        zones = [{"n": z["n"], "inizio_frenata_m": z["brake"], "vmin_kmh": round(z["vmin"])}
                 for z in (self.ref.zones if self.ref else [])]
        snap = self.snapshot()
        snap.pop("race", None)
        return {
            "pista": self.track_name, "sessione": {k: v for k, v in self.session.items() if k != "forecast"},
            "previsioni_meteo": self.session.get("forecast", [])[:8],
            "giro_riferimento": fmt_lap(self.ref.lap_ms) if self.ref else None,
            "staccate_riferimento": zones, "ultimi_giri": laps,
            "stato_ora": snap, "gara": self.race.context(),
            "note": "brake_delta in metri (negativo = frena prima del riferimento); "
                    "vmin_delta km/h a centro curva; full_delta metri (positivo = gas pieno più tardi); "
                    "loss secondi persi nella staccata. Gomme in ordine RL, RR, FL, FR. "
                    "Meteo: 0 sereno, 1 poco nuvoloso, 2 coperto, 3 pioggia leggera, 4 pioggia forte, 5 temporale. "
                    "safety_car: 0 no, 1 safety car, 2 virtual.",
        }
