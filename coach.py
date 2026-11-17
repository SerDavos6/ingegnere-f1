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
import time

import curve
from frasi import Frasi
import ideale
from assetto import SetupAdvisor
from energia import Energy
from allenamento import Training
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

    def __init__(self, lap_ms, grid, owner=None):
        self.lap_ms = lap_ms
        self.g = grid
        self.owner = owner          # None = giro tuo; altrimenti il nome dell'amico
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

    @classmethod
    def from_trace(cls, tr, lap_ms, owner=None):
        """Da un giro salvato (pagina Analisi o file di un amico): campi d, t, v, thr, brk."""
        D = tr["d"]

        def at(arr, d):
            i = bisect.bisect_left(D, d)
            if i <= 0:
                return arr[0]
            if i >= len(D):
                return arr[-1]
            d0, d1 = D[i - 1], D[i]
            f = 0 if d1 == d0 else (d - d0) / (d1 - d0)
            return arr[i - 1] + (arr[i] - arr[i - 1]) * f

        g = {k: [] for k in ("dist", "t", "speed", "thr", "brk")}
        for n in range(int(D[-1] // GRID_STEP) + 1):
            d = n * GRID_STEP
            g["dist"].append(d)
            g["t"].append(at(tr["t"], d))
            g["speed"].append(at(tr["v"], d))
            g["thr"].append(at(tr["thr"], d))
            g["brk"].append(at(tr["brk"], d))
        return cls(lap_ms, g, owner)

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
        return {"lap_ms": self.lap_ms, "grid": self.g, "owner": self.owner}


class Coach:
    def __init__(self, emit, data_dir=None, ref_dir=None):
        self.emit = emit            # emit(tipo, testo, parla: bool, priorità)
        self.ref_dir = ref_dir or REF_DIR
        self.verbosity = 2
        self.pit_loss = 22.0        # secondi persi con un pit stop (stima, modificabile in config.json)
        self.recorder = Recorder(data_dir) if data_dir else Recorder()
        self.race = RaceEngineer(self)
        self.setup = SetupAdvisor()
        self.energy = Energy(self)
        self.training = Training(self)
        self.frasi = Frasi()
        self.muted = False          # "Radio, silenzio": parla solo per le cose urgenti
        self.last_spoken = None     # per "Radio, ripeti"
        self.targets = {}           # tempo obiettivo per pista e categoria (ms), dalle impostazioni
        self.teammates = lambda: []  # amici del server nella stessa gara: [(nome, indice auto)]
        self.setups_dir = None      # assetti salvati (uno per pista e categoria, quello del giro migliore)
        self.reset()

    # ------------------------------------------------------------ stato
    def reset(self):
        self.session_uid = None
        self.player_idx = 0
        self.track_id = None
        self.track_name = "-"
        self.category = None
        self.ref = None
        self.lap = Lap()
        self.lap_num = None
        self.lap_invalid = False
        self.last_lapdata = None
        self.telemetry, self.status, self.damage, self.session, self.motion = {}, {}, {}, {}, {}
        self.analyzed = set()
        self.lap_zone_results = []
        self.tip_mem = {}            # staccata -> (tipo di consiglio, giro, quante volte di fila)
        self.history = []
        self.last_speak_t = -99.0
        self.now = 0.0
        self.warned = {}
        self.outline, self.outline_v = None, 0
        self.session_laps = []       # giri validi e completi della sessione, per il giro ideale
        self.ideal = None
        self.ideal_said = None
        self.flag_pending = None     # bandiera a scacchi vista, aspetto il tempo dell'ultimo giro
        self.race_done = False       # gara finita: il giro di rientro non si registra
        self.intro_pending = False   # annuncio "Gara di N giri…": lo dico solo se la gara deve ancora partire
        self.quali_pos_at = None     # qualifica: quando dire la posizione provvisoria dopo il giro
        self.outlap_said = None      # giro di uscita in cui ho già detto le temperature delle gomme
        self.target_said = None      # obiettivo già annunciato come raggiunto
        self.car_setup = None        # assetto attuale (pacchetto Car Setups)
        self.setup_said = False
        self.race.reset()
        self.setup.reset()
        self.energy.reset()
        self.training.reset()

    def say(self, text, kind="info", level=2, urgent=False):
        """Mostra sempre il messaggio; lo legge a voce solo se il livello scelto lo prevede.
        In qualifica, durante il giro lanciato, parla solo per le cose urgenti (come una radio vera)."""
        spoken = level <= self.verbosity
        if spoken and not urgent and self.is_quali and self.on_flying_lap():
            spoken = False
        if spoken and self.muted and not (urgent and level == 1):
            spoken = False
        if spoken and not urgent and self.now - self.last_speak_t < SPEAK_COOLDOWN_S:
            spoken = False
        if spoken:
            self.last_speak_t = self.now
            self.last_spoken = text
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

    @property
    def is_quali(self):
        return 5 <= (self.session.get("session_type") or 0) <= 14

    def target_key(self):
        return f"{self.track_id}|{self.category or 'f1_2025'}" if self.track_id is not None else None

    def target_ms(self):
        k = self.target_key()
        return self.targets.get(k) if k else None

    def on_flying_lap(self):
        """Stato del pilota dal gioco: 0 garage, 1 giro veloce, 2 rientro, 3 uscita, 4 in pista."""
        return (self.last_lapdata or {}).get("driver_status") == 1

    # --------------------------------------------------------- ingressi
    def on_header(self, h):
        self.now = h["session_time"]
        self.player_idx = h["player_idx"]
        if h["session_uid"] != self.session_uid:
            keep = self.session_uid is not None
            if keep:
                self._save_partial("fine sessione")     # il giro a metà quando la sessione finisce
            self.reset()
            self.session_uid = h["session_uid"]
            self.player_idx = h["player_idx"]
            if keep:
                self.emit("sistema", "Nuova sessione rilevata.", False, 0)

    def on_session(self, s):
        first = not self.session
        self.session = s
        cat = s.get("category") or "f1_2025"
        if s["track_id"] != self.track_id or cat != self.category:
            self.track_id = s["track_id"]
            self.track_name = s["track_name"]
            self.category = cat
            self.ref = self._load_ref()
            self._load_outline()
            msg = f"Pista: {self.track_name}, {s['session_name']}, {s.get('category_name') or 'F1'}."
            if self.ref:
                who = f" (giro di {self.ref.owner})" if self.ref.owner else ""
                msg += f" Riferimento: {fmt_lap(self.ref.lap_ms)}{who}, {len(self.ref.zones)} staccate."
            self.emit("sistema", msg, False, 0)
        self._quali_clock(s)
        if first and s["is_race"] and s["total_laps"]:
            # non subito: dopo la bandiera a scacchi il gioco apre una "nuova sessione" (podio, risultati)
            # con gli stessi dati della gara, e l'annuncio partirebbe a gara finita. Aspetto i dati del giro.
            self.intro_pending = self.now
        self.race.on_session(s)

    def _quali_clock(self, s):
        """Qualifica: avviso per uscire in tempo per l'ultimo giro, e la bandiera a scacchi."""
        left = s.get("time_left")
        if not self.is_quali or left is None:
            return
        ld = self.last_lapdata or {}
        status = ld.get("driver_status")
        in_box = status in (0, None) or bool(ld.get("pit_status"))
        if 60 <= left <= 240 and in_box and "q_esci" not in self.warned:
            self.warned["q_esci"] = self.now
            mins = max(1, round(left / 60))
            self.say(f"Mancano {mins} minuti alla fine della qualifica: esci adesso per avere il tempo di un ultimo giro.",
                     "qualifica", level=1, urgent=True)
        if left == 0 and self.warned.get("q_live") and "q_fine" not in self.warned:
            self.warned["q_fine"] = self.now
            if status == 1:
                self.say("Bandiera a scacchi della qualifica: questo giro vale ancora, chiudilo!", "qualifica",
                         level=1, urgent=True)
        if left > 0:
            self.warned["q_live"] = True

    def on_lap(self, ld):
        prev = self.last_lapdata
        self.last_lapdata = ld
        if self.quali_pos_at is not None and self.now >= self.quali_pos_at:
            self.quali_pos_at = None
            pos = ld.get("position")
            if pos:
                self.say("Sei in pole provvisoria!" if pos == 1 else f"Provvisoriamente sei P{pos}.",
                         "qualifica", level=1, urgent=True)
        # briefing prima del via: aspetto i nomi dei piloti (al massimo 6 secondi)
        if self.intro_pending is not False and (self.race.names or self.now - self.intro_pending >= 6):
            self.intro_pending = False
            # solo al primo giro con la gara in corso (non a gara finita, non se entri a gara iniziata)
            if self.in_race and ld.get("lap_num", 0) <= 1 and ld.get("result_status") in (None, 2) \
                    and not ld.get("total_distance", 0) > 1500:
                self.say(self.race.briefing(), "gara", level=1, urgent=True)
        if self.lap_num is None:
            self.lap_num = ld["lap_num"]
        # tornato nel garage (anche dal menu) a metà giro: salvo quello che c'è come giro parziale
        if prev and ld.get("driver_status") == 0 and prev.get("driver_status") != 0:
            self._save_partial("garage")
        # bandiera a scacchi: il gioco segna la gara finita ma non passa al giro successivo,
        # quindi l'ultimo giro va chiuso qui. Aspetto un attimo che arrivi il suo tempo.
        if (prev and not self.race_done and not self.flag_pending and ld["lap_num"] == self.lap_num
                and ld.get("result_status") == 3 and prev.get("result_status") != 3):
            self.flag_pending = (prev, self.now + 1.5, prev["last_lap_ms"])
        if self.flag_pending:
            p, deadline, old_last = self.flag_pending
            if ld["last_lap_ms"] != old_last or self.now >= deadline:
                self.flag_pending = None
                self.race_done = True
                ms = ld["last_lap_ms"] if ld["last_lap_ms"] != old_last else (
                    int(round(self.lap.t[-1] * 1000)) if len(self.lap) else 0)
                if ms:
                    self._finish_lap(ms, p)
                self.lap = Lap()
        if ld["lap_num"] != self.lap_num:
            if prev and ld["lap_num"] == self.lap_num + 1 and not self.race_done:
                self.flag_pending = None
                if ld.get("result_status") == 3:
                    self.race_done = True
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

    def on_motion_ex(self, ex):
        self.setup.on_ex(ex)

    def on_telemetry(self, tel):
        self.telemetry = tel
        ld = self.last_lapdata
        if ld:
            self.setup.on_sample(tel, ld, self.motion, self.status)
        if not ld or ld["pit_status"] != 0 or self.flag_pending or self.race_done:
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
        self._outlap_tyres(ld, tel)

    # ------------------------------------------------ assetti salvati
    def on_setup(self, setup):
        self.car_setup = setup
        # prova e qualifica: se su questa pista hai già un assetto salvato, lo ricordo una volta
        if not self.setup_said and not self.in_race and self.track_id is not None and self.setups_dir:
            self.setup_said = True
            saved = self.saved_setup()
            if saved:
                self.say(f"Su {self.track_name} hai un assetto salvato, quello del {fmt_lap_voice(saved['ms'])}: "
                         f"lo trovi in Dettagli, Assetti, insieme a quelli degli amici.", "assetto", level=2)

    def _setup_path(self):
        key = f"{self.track_id}_{self.category or 'f1_2025'}"
        return os.path.join(self.setups_dir, key + ".json") if self.setups_dir else None

    def saved_setup(self):
        p = self._setup_path()
        try:
            with open(p) as f:
                return json.load(f)
        except (OSError, ValueError, TypeError):
            return None

    def _save_setup(self, lap_ms):
        """L'assetto del giro migliore valido, per pista e categoria."""
        if not self.car_setup or not self.setups_dir:
            return
        old = self.saved_setup()
        if old and old.get("ms") and old["ms"] <= lap_ms:
            return
        try:
            os.makedirs(self.setups_dir, exist_ok=True)
            with open(self._setup_path(), "w") as f:
                json.dump({"ms": lap_ms, "date": time.time(), "track": self.track_name, "track_id": self.track_id,
                           "category": self.category, "category_name": self.session.get("category_name"),
                           "session": self.session.get("session_name"), "setup": self.car_setup}, f, ensure_ascii=False)
        except OSError as e:
            print("Impossibile salvare l'assetto:", e, flush=True)

    def on_status(self, st):
        self.status = st
        self.energy.on_status(st)
        if st.get("fia_flag") is not None:
            self.race.on_fia_flag(st["fia_flag"])
        fl = st.get("fuel_laps")
        if fl is not None and fl < -0.3 and self.in_race and self._once("fuel", 90):
            self.fuel_saving = True
            self.say(self.fuel_advice(fl), "avviso", level=1, urgent=True)
        elif fl is not None and fl >= 0.1 and getattr(self, "fuel_saving", False) and self.in_race:
            self.fuel_saving = False
            self.say("Carburante di nuovo a posto: puoi smettere di risparmiare e spingere.", "gara", level=1, urgent=True)

    def fuel_advice(self, fl):
        """Lift and coast guidato: le staccate dove arrivi più veloce sono quelle dove risparmi di più."""
        unit = "giro" if num(fl) == "1" else "giri"
        if not self.ref or len(self.ref.zones) < 2:
            return f"Carburante: sei sotto di {num(fl)} {unit}. Alza il piede e lascia scorrere prima delle frenate."
        g = self.ref.g

        def entry_speed(z):
            i = min(len(g["speed"]) - 1, int(z["brake"] // GRID_STEP))
            return g["speed"][i]
        best = sorted(self.ref.zones, key=entry_speed, reverse=True)[:2]
        best.sort(key=lambda z: z["n"])
        deficit = -fl
        lift = 50 if deficit < 0.5 else 100 if deficit < 1.0 else 150
        cost = 0.06 * len(best) * lift / 50          # stima: circa 6 centesimi ogni 50 metri di rilascio
        return (f"Carburante: sei sotto di {num(fl)} {unit}. Lift and coast: alza il piede {lift} metri prima "
                f"in {self.zone_label(best[0])} e in {self.zone_label(best[1])}, dove arrivi più veloce. "
                f"Perdi circa {tenths(cost)} al giro.")

    def zone_label(self, z, capital=False):
        """Il nome della curva, se qualcuno l'ha scritto in Analisi; altrimenti "staccata N".
        z: una staccata del riferimento (brake) o un risultato del giro (at)."""
        m = z.get("brake", z.get("at"))
        name = curve.name_at(self.track_id, m) if m is not None else None
        if name:
            return name
        return ("Staccata " if capital else "staccata ") + str(z["n"])

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
        focus = self.training.focus_zone()      # allenamento mirato: a voce solo la staccata su cui lavori
        if focus is not None:
            tip_level = 1 if z["n"] == focus else 4
        tag, tag_l, f = self.zone_label(z, capital=True), self.zone_label(z), self.frasi
        if loss >= 0.08:
            bd, vd, fd = res["brake_delta"], res["vmin_delta"], res["full_delta"]
            if bd is not None and bd <= -12:
                kind, tip = "freni_prima", f("freni_prima", m=f"{abs(bd):.0f}")
            elif bd is not None and bd >= 12 and vd is not None and vd <= -3:
                kind, tip = "freni_tardi", f("freni_tardi")
            elif vd is not None and vd <= -4:
                kind, tip = "vmin", f("vmin", kmh=f"{abs(vd):.0f}")
            elif fd is not None and fd >= 20:
                kind, tip = "gas", f("gas", m=f"{fd:.0f}")
            else:
                kind, tip = "trazione", f("trazione")
            # stesso consiglio sulla stessa curva nei giri appena fatti: lo dico in un altro modo,
            # e dalla terza volta di fila solo con "Tanto" (resta comunque scritto)
            prev = self.tip_mem.get(z["n"])
            lap = self.lap_num or 0
            times = prev[2] + 1 if prev and prev[0] == kind and lap - prev[1] <= 2 else 1
            self.tip_mem[z["n"]] = (kind, lap, times)
            perdi = f("perdi", loss=tenths(loss))
            text = f("ancora" if times >= 2 else "consiglio", tag=tag, tag_l=tag_l, tip=tip, perdi=perdi)
            self.say(text[0].upper() + text[1:], "consiglio",
                     level=tip_level if focus is not None else 3 if times >= 3 else tip_level)
        elif loss <= -0.12:
            self.tip_mem.pop(z["n"], None)
            text = f("bene_zona", tag=tag, tag_l=tag_l, gain=tenths(loss))
            self.say(text[0].upper() + text[1:], "consiglio", level=1 if focus == z["n"] else 3)

    def _outlap_tyres(self, ld, tel):
        """Prova e qualifica, giro di uscita: verso la fine dico se le gomme sono pronte per il giro."""
        length = self.session.get("track_length") or 0
        if (self.in_race or not length or ld.get("driver_status") != 3 or self.outlap_said == self.lap_num
                or ld["lap_distance"] < length * 0.75 or (self.session.get("session_type") == 18)):
            return
        self.outlap_said = self.lap_num
        t = tel.get("tyre_inner") or tel.get("tyre_surface")
        if not t:
            return
        front, rear = round((t[2] + t[3]) / 2), round((t[0] + t[1]) / 2)
        cold = [n for n, v in (("anteriori", front), ("posteriori", rear)) if v < 80]
        hot = [n for n, v in (("anteriori", front), ("posteriori", rear)) if v > 108]
        if cold:
            self.say(f"Gomme {' e '.join(cold)} ancora fredde: anteriori {front}, posteriori {rear} gradi. "
                     f"Scaldale nelle ultime curve: frenate decise e trazione.", "qualifica", level=1, urgent=True)
        elif hot:
            self.say(f"Gomme {' e '.join(hot)} già calde: anteriori {front}, posteriori {rear} gradi. "
                     f"Vai piano nelle ultime curve.", "qualifica", level=1, urgent=True)
        else:
            self.say(f"Gomme pronte: anteriori {front}, posteriori {rear} gradi. Lancia il giro.",
                     "qualifica", level=1, urgent=True)

    def _check_tyres(self, tel):
        temps = tel["tyre_surface"]
        hot = [i for i, x in enumerate(temps) if x >= 110]
        if hot and self._once("tyrehot", 45):
            names = ["posteriore sinistra", "posteriore destra", "anteriore sinistra", "anteriore destra"]
            g = names[hot[0]]
            self.say(self.frasi("gomma_calda", g=g, G=g[0].upper() + g[1:], t=temps[hot[0]]),
                     "avviso", level=2)

    # --------------------------------------------------- fine giro
    def _lap_kind(self):
        """'completo', 'uscita' (partito dalla corsia box), 'rientro' (finito ai box) o 'parziale'.
        None se il giro registrato è troppo corto per servire a qualcosa."""
        track_len = self.session.get("track_length") or 0
        if len(self.lap) <= 50:
            return None
        start_ok = self.lap.dist[0] < 100
        end_ok = not track_len or self.lap.dist[-1] > track_len * 0.9
        if start_ok and end_ok:
            return "completo"
        span = self.lap.dist[-1] - self.lap.dist[0]
        if span < (track_len * 0.3 if track_len else 1000):
            return None
        return "rientro" if start_ok else ("uscita" if end_ok else "parziale")

    def _record(self, kind, lap_ms, prev_ld, entry):
        s1 = prev_ld.get("s1_ms") or 0
        s2 = prev_ld.get("s2_ms") or 0
        info = dict(entry, s1=s1, s2=s2,
                    s3=lap_ms - s1 - s2 if s1 and s2 and kind == "completo" else 0,
                    compound=self.status.get("compound"), tyre_age=self.status.get("tyre_age"),
                    position=prev_ld.get("position"), grid=prev_ld.get("grid_position") or None,
                    pit_stops=prev_ld.get("pit_stops"),
                    wear=round(max(self.damage["tyre_wear"]), 1) if self.damage.get("tyre_wear") else None,
                    race=bool(self.in_race))
        if kind != "completo":
            info.update(partial=True, kind=kind)
        try:
            self.recorder.save_lap(self.session_uid, self.session, self.lap, info)
        except OSError as e:
            print("Impossibile salvare il giro:", e, flush=True)

    def _update_ideal(self, lap_ms):
        self.session_laps.append({"lap": self.lap_num, "ms": lap_ms, "d": list(self.lap.dist), "t": list(self.lap.t)})
        self.session_laps = self.session_laps[-40:]
        res = ideale.compute(self.session_laps, self.session.get("track_length") or None)
        if not res:
            return
        self.ideal = res
        # lo dico in prova e qualifica, quando il giro ideale migliora di almeno mezzo decimo
        if self.in_race or res["laps"] < 3 or res["gap_s"] < 0.15:
            return
        if self.ideal_said is not None and res["ideal_ms"] > self.ideal_said - 50:
            return
        self.ideal_said = res["ideal_ms"]
        where = ""
        if res["spots"]:
            sp = res["spots"][0]
            z = next((z for z in (self.ref.zones if self.ref else []) if sp["da_m"] - 150 <= z["brake"] <= sp["a_m"]), None)
            where = (f", soprattutto in {self.zone_label(z)}" if z else
                     f", soprattutto tra {num(sp['da_m'] / 1000)} e {num(sp['a_m'] / 1000)} chilometri")
        self.say(f"Giro ideale {fmt_lap_voice(res['ideal_ms'])}: hai ancora {tenths(res['gap_s'])} da trovare{where}.",
                 "giro", level=2)

    def _save_partial(self, why):
        """Salva il giro in corso, se è abbastanza lungo, quando non si chiuderà più da solo."""
        ld = self.last_lapdata
        if not ld or not len(self.lap) or self.race_done:
            return
        kind = self._lap_kind()
        if kind is None:
            self.lap = Lap()
            return
        if kind == "completo":
            kind = "parziale"          # coperto quasi tutto ma senza passare sul traguardo
        ms = int(round(self.lap.t[-1] * 1000))
        entry = {"lap": self.lap_num, "ms": ms, "valid": False, "zones": self.lap_zone_results}
        self._record(kind, ms, ld, entry)
        print(f"Giro {self.lap_num} salvato come parziale ({why}): "
              f"da {self.lap.dist[0]:.0f} a {self.lap.dist[-1]:.0f} m", flush=True)
        self.lap = Lap()

    def _finish_lap(self, lap_ms, prev_ld):
        valid = not self.lap_invalid
        kind = self._lap_kind()
        complete = kind == "completo"
        entry = {"lap": self.lap_num, "ms": lap_ms, "valid": valid, "zones": self.lap_zone_results}
        self.history.append(entry)
        self.history = self.history[-30:]

        if kind:
            self._record(kind, lap_ms, prev_ld, entry)
        if kind and not complete:
            print(f"Giro {self.lap_num} salvato come '{kind}': da {self.lap.dist[0]:.0f} "
                  f"a {self.lap.dist[-1]:.0f} m", flush=True)

        best_before = self.ref.lap_ms if self.ref else None
        owner = self.ref.owner if self.ref else None
        f = self.frasi
        parts = [f("giro", t=fmt_lap_voice(lap_ms))]
        if not valid:
            parts.append("non valido")
        elif best_before:
            delta = (lap_ms - best_before) / 1000.0
            key = ("piu" if delta >= 0 else "meno") + ("_di" if owner else "")
            parts.append(f(key, d=tenths(delta), chi=owner))
        target = self.target_ms()
        if target and valid and complete and lap_ms > target:
            parts.append(f"a {tenths((lap_ms - target) / 1000)} dall'obiettivo")
        worst = sorted([r for r in self.lap_zone_results if r["loss"] > 0.05], key=lambda r: -r["loss"])[:2]
        if worst:
            parts.append(f("perdi_in", dove=" e ".join(self.zone_label(r) for r in worst)))

        lap_level = 3 if self.in_race else 1   # in gara il riepilogo lo fa l'ingegnere di gara
        if valid and complete:
            self._save_setup(lap_ms)
        if valid and complete and (best_before is None or lap_ms < best_before):
            self.ref = Reference.from_lap(self.lap, lap_ms)
            self._save_ref()
            t = fmt_lap_voice(lap_ms)
            text = f("primo_rif", t=t, n=len(self.ref.zones))
            if best_before and owner:
                text = f("battuto", chi=owner, t=t, d=tenths((best_before - lap_ms) / 1000))
            elif best_before:
                text = f("giro_migliore", t=t, d=tenths((best_before - lap_ms) / 1000))
            self.say(text, "giro", level=2 if self.in_race else 1, urgent=True)
        else:
            # giro di uscita o di rientro: il tempo non dice niente, lo leggo solo con "Tanto"
            self.say(", ".join(parts) + ".", "giro", level=lap_level if complete else 3, urgent=True)
        self.emit("fine_giro", json.dumps(entry), False, 0)
        if complete and valid:
            self._update_ideal(lap_ms)
        if target and valid and complete and lap_ms <= target and self.target_said != target:
            self.target_said = target
            self.say(f"Obiettivo raggiunto! {fmt_lap_voice(lap_ms)}, sotto il {fmt_lap_voice(target)} che volevi. "
                     f"Se vuoi alzare l'asticella, metti un nuovo tempo nei Dettagli.", "giro", level=1, urgent=True)
        ttip = self.training.on_lap_complete(entry, complete, valid)
        if ttip:
            self.say(ttip, "allenamento", level=1, urgent=True)
        if complete and valid and self.is_quali:
            self.quali_pos_at = self.now + 3.0     # la classifica si aggiorna un attimo dopo il traguardo
        etip = self.energy.on_lap_complete(valid, complete)
        if etip:
            self.say(etip, "energia", level=2, urgent=not self.in_race)
        if complete:
            if self.outline is None:
                self._make_outline()
            self.setup.on_lap()
            tip = None if self.in_race else self.setup.new_advice_to_say()
            if tip:
                self.say(tip, "assetto", level=2)
        self.race.on_lap_complete()

    # ------------------------------------------------ persistenza
    def _ref_path(self, track_id=None, category=None):
        """Un riferimento per pista e per categoria di macchine (F1 2025, F1 2026, F2…)."""
        tid = self.track_id if track_id is None else track_id
        cat = category or self.category or "f1_2025"
        return os.path.join(self.ref_dir, f"pista_{tid}_{cat}.json")

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
            return Reference(j["lap_ms"], j["grid"], j.get("owner"))
        except (OSError, ValueError, KeyError):
            return None

    # forma della pista per la mini mappa live (dal primo giro completo, salvata per pista)
    def _outline_path(self):
        return os.path.join(self.ref_dir, f"mappa_{self.track_id}.json")

    def _make_outline(self):
        pts, last = [], -1e9
        for d, x, z in zip(self.lap.dist, self.lap.x, self.lap.z):
            if d - last >= 12 and (x or z):
                pts.append([round(x, 1), round(z, 1)])
                last = d
        if len(pts) < 30:
            return
        self.outline = pts
        self.outline_v += 1
        try:
            os.makedirs(self.ref_dir, exist_ok=True)
            with open(self._outline_path(), "w") as f:
                json.dump(pts, f)
        except OSError:
            pass

    def _load_outline(self):
        try:
            with open(self._outline_path()) as f:
                self.outline = json.load(f)
            self.outline_v += 1
        except (OSError, ValueError):
            self.outline = None

    def set_reference(self, track_id, trace, lap_ms, owner=None, category=None):
        """Usa un giro salvato (tuo o di un amico) come riferimento del coach per quella pista e categoria."""
        ref = Reference.from_trace(trace, lap_ms, owner)
        category = category or self.category or "f1_2025"
        os.makedirs(self.ref_dir, exist_ok=True)
        with open(self._ref_path(track_id, category), "w") as f:
            json.dump(ref.to_json(), f)
        if track_id == self.track_id and category == (self.category or "f1_2025"):
            self.ref = ref
            self.analyzed = set()
        who = f"il giro di {owner}" if owner else "il tuo giro"
        self.emit("sistema", f"Riferimento del coach: {who}, {fmt_lap(lap_ms)}, {len(ref.zones)} staccate.", False, 0)
        return ref

    # ----------------------------------------------- per la dashboard
    def corner_state(self):
        """(in curva adesso?, secondi alla prossima frenata) per far parlare l'ingegnere solo sul dritto.

        In curva = freni, oppure la forza laterale supera 1 g (curve veloci in pieno).
        I secondi al prossimo punto di frenata vengono dalle staccate del giro di riferimento;
        None se non c'è un riferimento o se sei fermo o ai box.
        """
        ld, tel = self.last_lapdata or {}, self.telemetry
        v = tel.get("speed") or 0
        if not ld or ld.get("pit_status", 0) != 0 or v < 40:
            return False, None
        corner = (tel.get("brake") or 0) > 0.05 or abs(self.motion.get("glat", 0.0)) > 1.0
        straight = None
        d = ld.get("lap_distance", -1)
        if self.ref and self.ref.zones and d >= 0:
            if any(z["brake"] - 20 <= d <= z["exit"] for z in self.ref.zones):
                corner = True
            # tempo dal giro di riferimento: tiene conto che sul dritto accelererai
            t_now = self.ref.t_at(d)
            nxt = next((z["brake"] for z in self.ref.zones if z["brake"] > d), None)
            if nxt is not None:
                straight = self.ref.t_at(nxt) - t_now
            else:                                # dopo l'ultima curva: la prossima è la prima del giro dopo
                straight = self.ref.lap_ms / 1000 - t_now + self.ref.t_at(self.ref.zones[0]["brake"])
            straight = round(max(0.0, straight), 1)
        return corner, straight

    def snapshot(self):
        ld, tel, st = self.last_lapdata or {}, self.telemetry, self.status
        corner, straight = self.corner_state()
        delta = None
        target = self.target_ms()
        if self.ref and ld and len(self.lap) > 5 and ld.get("lap_distance", -1) > 0:
            # con un tempo obiettivo il riferimento è "stirato" fino a quel tempo
            scale = target / self.ref.lap_ms if target and self.ref.lap_ms else 1.0
            delta = round(ld["current_lap_ms"] / 1000.0 - self.ref.t_at(ld["lap_distance"]) * scale, 2)
        last = self.history[-1] if self.history else None
        return {
            "track": self.track_name, "lap": ld.get("lap_num"),
            "category": self.category, "category_name": self.session.get("category_name"),
            "position": ld.get("position"), "current_ms": ld.get("current_lap_ms"),
            "last_ms": last["ms"] if last else None,
            "best_ms": self.ref.lap_ms if self.ref else None,
            "ref_owner": self.ref.owner if self.ref else None,
            "ideal_ms": self.ideal["ideal_ms"] if self.ideal else None,
            "delta": delta, "invalid": self.lap_invalid,
            "speed": tel.get("speed"), "gear": tel.get("gear"), "rpm": tel.get("rpm"),
            "in_curva": corner, "rettilineo_s": straight,
            "throttle": tel.get("throttle"), "brake": tel.get("brake"),
            "tyre_temp": tel.get("tyre_surface"), "tyre_wear": self.damage.get("tyre_wear"),
            "front_wing": self.damage.get("front_wing"),
            "fuel_laps": st.get("fuel_laps"), "compound": st.get("compound"),
            "tyre_age": st.get("tyre_age"),
            "ers": self.energy.now_pct, "ers_mode": self.energy.snapshot()["mode"],
            "is_quali": self.is_quali, "muted": self.muted, "target_ms": target,
            "training": self.training.snapshot(), "session_name": self.session.get("session_name"),
            "time_left": self.session.get("time_left"),
            "verbosity": self.verbosity,
            "setup": self.setup.advice(), "setup_laps": self.setup.laps,
            "outline_v": self.outline_v if self.outline else 0, "dots": self.race.dots(),
            "race": self.race.snapshot(),
        }

    def context_for_ai(self):
        """Riassunto compatto per l'ingegnere AI."""
        laps = [{"giro": h["lap"], "tempo": fmt_lap(h["ms"]), "valido": h["valid"],
                 "staccate": h["zones"]} for h in self.history[-5:]]
        zones = [{"n": z["n"], "nome": self.zone_label(z), "inizio_frenata_m": z["brake"], "vmin_kmh": round(z["vmin"])}
                 for z in (self.ref.zones if self.ref else [])]
        snap = self.snapshot()
        snap.pop("race", None)
        return {
            "pista": self.track_name, "sessione": {k: v for k, v in self.session.items() if k != "forecast"},
            "previsioni_meteo": self.session.get("forecast", [])[:8],
            "giro_riferimento": fmt_lap(self.ref.lap_ms) if self.ref else None,
            "staccate_riferimento": zones, "ultimi_giri": laps,
            "stato_ora": snap, "gara": self.race.context(), "assetto": self.setup.summary(),
            "note": "brake_delta in metri (negativo = frena prima del riferimento); "
                    "vmin_delta km/h a centro curva; full_delta metri (positivo = gas pieno più tardi); "
                    "loss secondi persi nella staccata. Gomme in ordine RL, RR, FL, FR. "
                    "Meteo: 0 sereno, 1 poco nuvoloso, 2 coperto, 3 pioggia leggera, 4 pioggia forte, 5 temporale. "
                    "safety_car: 0 no, 1 safety car, 2 virtual.",
        }
