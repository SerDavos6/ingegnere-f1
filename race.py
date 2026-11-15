"""Ingegnere di gara: distacchi, avversari, box, safety car, meteo, strategia gomme.

Funziona anche online: se un avversario nasconde i dati (telemetria "Ristretta"),
nome e gomme mancano e l'ingegnere parla di "la P4" invece del nome.
"""
from parlato import num, plural, tenths

SLICKS = {16, 17, 18}


class RaceEngineer:
    def __init__(self, coach):
        self.c = coach
        self.reset()

    def reset(self):
        self.names = {}
        self.by_idx = {}
        self.cars = []
        self.tyres = {}
        self.prev_pit = {}
        self.last_sc = 0
        self.last_weather = None
        self.rain_warn = (None, -1e9)
        self.announced = set()
        self.gap_hist = {}            # giro -> {"ahead": (idx, gap), "behind": (idx, gap)}
        self.wear_hist = []           # (giro, usura massima)
        self.my_stops = None
        self.pit_exit_pending = False
        self.drs_warned_lap = -10
        self.wear_warned_lap = -10
        self.pos_candidate = (None, 0.0)
        self.pos_announced = None

    # ---------------------------------------------------------- utilità
    @property
    def is_race(self):
        return bool(self.c.session.get("is_race"))

    def me(self):
        return self.by_idx.get(self.c.player_idx)

    def rel(self, offset):
        me = self.me()
        if not me:
            return None
        want = me["position"] + offset
        return next((c for c in self.cars if c["position"] == want), None)

    def name(self, idx):
        n = self.names.get(idx, "")
        if n and n.lower() not in ("player", "giocatore"):
            parts = n.split()
            return parts[-1].title()
        car = self.by_idx.get(idx)
        return f"la P{car['position']}" if car else "un avversario"

    def _in_window(self, me):
        s = self.c.session
        return s.get("pit_ideal", 0) > 0 and s["pit_ideal"] - 1 <= me["lap_num"] <= max(s["pit_latest"], s["pit_ideal"])

    def _once(self, key):
        if key in self.announced:
            return False
        self.announced.add(key)
        return True

    def _wear(self):
        w = self.c.damage.get("tyre_wear")
        return max(w) if w else None

    # ----------------------------------------------------------- ingressi
    def on_participants(self, names):
        self.names = names

    def on_status_all(self, tyres):
        self.tyres = tyres

    def on_laps(self, cars):
        self.cars = cars
        self.by_idx = {c["idx"]: c for c in cars}
        me = self.me()
        if not me or not self.is_race:
            self.prev_pit = {c["idx"]: c["pit_status"] for c in cars}
            return
        say = self.c.say

        # avversari vicini che rientrano ai box
        for off in (-1, 1):
            car = self.rel(off)
            if not car:
                continue
            if car["pit_status"] == 1 and self.prev_pit.get(car["idx"], 1) == 0 and me["pit_status"] == 0:
                where = "davanti" if off < 0 else "dietro"
                msg = f"{self.name(car['idx'])}, {where} a te, è entrato ai box."
                if self._in_window(me) and me["pit_stops"] == 0:
                    msg += " Possiamo fermarci al prossimo giro per coprirlo."
                say(msg, "gara", level=1, urgent=True)
        self.prev_pit = {c["idx"]: c["pit_status"] for c in cars}

        # chi ti segue entro un secondo
        behind = self.rel(1)
        if behind and me["pit_status"] == 0 and behind["pit_status"] == 0 and me["lap_num"] > 1:
            gap = behind["delta_front_ms"] / 1000
            if 0 < gap < 1.0 and me["lap_num"] - self.drs_warned_lap >= 2:
                self.drs_warned_lap = me["lap_num"]
                say(f"{self.name(behind['idx'])} è a meno di un secondo. Difendi in uscita dalle curve lente.",
                    "gara", level=1, urgent=True)

        # il tuo pit stop
        if self.my_stops is None:
            self.my_stops = me["pit_stops"]
        if me["pit_stops"] > self.my_stops:
            self.my_stops = me["pit_stops"]
            self.pit_exit_pending = True
        if self.pit_exit_pending and me["pit_status"] == 0:
            self.pit_exit_pending = False
            self.wear_hist = []
            say(f"Pit fatto. Rientri in posizione {me['position']}.", "gara", level=1, urgent=True)

        # cambi di posizione (stabili da 3 secondi, per ignorare i sorpassi a metà)
        pos = me["position"]
        cand, since = self.pos_candidate
        if pos != cand:
            self.pos_candidate = (pos, self.c.now)
        elif self.c.now - since >= 3 and pos != self.pos_announced:
            if self.pos_announced is not None and me["pit_status"] == 0:
                if pos < self.pos_announced:
                    say(f"Bel sorpasso, sei P{pos}.", "gara", level=2)
                else:
                    say(f"Hai perso una posizione, sei P{pos}.", "gara", level=2)
            self.pos_announced = pos

    def on_session(self, s):
        say = self.c.say
        me = self.me()
        # safety car
        sc = s.get("safety_car", 0)
        if sc != self.last_sc and self.is_race:
            if sc == 1:
                msg = "Safety car in pista!"
                wear = self._wear() or 0
                if me and me["pit_stops"] == 0 and (self._in_window(me) or wear > 35):
                    msg += " È il momento giusto per la sosta: box, box."
                else:
                    msg += " Rispetta il delta e tieni calde le gomme."
                say(msg, "gara", level=1, urgent=True)
            elif sc == 2:
                say("Virtual safety car. Rispetta il delta.", "gara", level=1, urgent=True)
            elif sc == 0 and self.last_sc == 1:
                say("La safety car rientra a fine giro. Preparati alla ripartenza.", "gara", level=1, urgent=True)
            elif sc == 0 and self.last_sc == 2:
                say("Fine virtual safety car, via!", "gara", level=1, urgent=True)
        self.last_sc = sc

        # meteo
        w = s.get("weather", 0)
        comp = self.c.status.get("compound_id")
        slick = comp in SLICKS if comp is not None else True
        if w < 3:
            nxt = next((f for f in s.get("forecast", []) if f["min"] > 0 and (f["weather"] >= 3 or f["rain"] >= 50)), None)
            last_min, last_t = self.rain_warn
            if nxt and (last_min is None or abs(nxt["min"] - last_min) >= 10 or self.c.now - last_t > 600):
                self.rain_warn = (nxt["min"], self.c.now)
                say(f"Meteo: pioggia prevista tra {nxt['min']} minuti, probabilità {nxt['rain']} per cento.",
                    "meteo", level=1, urgent=True)
        if self.last_weather is not None:
            if w >= 3 and self.last_weather < 3 and slick:
                say("Ha iniziato a piovere. Con le slick valuta le intermedie.", "meteo", level=1, urgent=True)
            if w <= 1 and self.last_weather >= 3 and not slick:
                say("La pista si sta asciugando: presto le slick saranno più veloci.", "meteo", level=1, urgent=True)
        self.last_weather = w

    def on_lap_complete(self):
        """Chiamato dal coach quando il pilota passa sul traguardo."""
        me = self.me()
        if not me or not self.is_race:
            return
        s, say = self.c.session, self.c.say
        lap = me["lap_num"]            # giro appena iniziato
        total = s.get("total_laps", 0)
        remaining = total - lap + 1 if total else None

        if remaining is not None and remaining <= 0:
            return                      # gara finita
        if remaining == 1:
            say(f"Ultimo giro, sei P{me['position']}. Dai tutto!", "gara", level=1, urgent=True)

        # distacchi e tendenza
        hist = {}
        first = f"P{me['position']}"
        if remaining and remaining > 1:
            first += f", mancano {remaining} giri"
        parts = [first]
        prev = self.gap_hist.get(lap - 1, {})
        ahead, behind = self.rel(-1), self.rel(1)
        if ahead and me["pit_status"] == 0:
            gap = me["delta_front_ms"] / 1000
            hist["ahead"] = (ahead["idx"], gap)
            txt = f"davanti {self.name(ahead['idx'])} a {num(gap)}"
            p = prev.get("ahead")
            if p and p[0] == ahead["idx"]:
                diff = p[1] - gap
                if diff >= 0.05:
                    txt += f", recuperi {tenths(diff)} al giro"
                elif diff <= -0.05:
                    txt += f", perdi {tenths(diff)} al giro"
            t = self.tyres.get(ahead["idx"])
            mine = self.c.status.get("compound")
            if t and t.get("compound") and mine and t["compound"] != mine:
                txt += f", lui ha le {t['compound']} di {plural(t['tyre_age'], 'giro', 'giri')}"
            parts.append(txt)
        if behind and behind["pit_status"] == 0:
            gap = behind["delta_front_ms"] / 1000
            hist["behind"] = (behind["idx"], gap)
            txt = f"dietro {self.name(behind['idx'])} a {num(gap)}"
            p = prev.get("behind")
            if p and p[0] == behind["idx"]:
                diff = p[1] - gap
                if diff >= 0.05:
                    txt += f" e si avvicina di {tenths(diff)}"
            parts.append(txt)
        self.gap_hist[lap] = hist
        if remaining != 1:
            say(". ".join(p[0].upper() + p[1:] for p in parts) + ".", "gara", level=2)

        # finestra pit
        ideal, latest = s.get("pit_ideal", 0), s.get("pit_latest", 0)
        if ideal and me["pit_stops"] == 0:
            if lap < ideal and self._once(("finestra", ideal, latest)):
                say(f"Finestra pit tra il giro {ideal} e il {latest}.", "strategia", level=2)
            elif lap == ideal and self._once(("apre", ideal)):
                say(f"Siamo nella finestra pit: box quando vuoi entro il giro {latest}.", "strategia", level=1, urgent=True)
            elif lap == latest and self._once(("chiude", latest)):
                say("Ultimo giro utile per la sosta. Box, box!", "strategia", level=1, urgent=True)

        # usura gomme: stima dei giri rimasti prima del 70%
        wear = self._wear()
        if wear is not None:
            self.wear_hist.append((lap, wear))
            self.wear_hist = self.wear_hist[-4:]
            if len(self.wear_hist) >= 3:
                (l0, w0), (l1, w1) = self.wear_hist[0], self.wear_hist[-1]
                rate = (w1 - w0) / max(1, l1 - l0)
                if rate > 0.3 and wear < 70:
                    left = (70 - wear) / rate
                    if left <= 3 and (remaining is None or left < remaining) and lap - self.wear_warned_lap >= 2:
                        self.wear_warned_lap = lap
                        say(f"Gomme al limite tra circa {max(1, round(left))} giri. Prepariamo la sosta.",
                            "strategia", level=1, urgent=True)

    # ------------------------------------------------- stima rientro dal pit
    def rejoin_estimate(self, pit_loss=22.0):
        """Dove rientri se ti fermi adesso. Usa la stima del gioco se c'è, altrimenti i distacchi."""
        me = self.me()
        if not me or not self.is_race:
            return None
        my_t = me["delta_leader_ms"] / 1000 if me["position"] > 1 else 0.0
        target = my_t + pit_loss
        others = [c for c in self.cars if c["idx"] != me["idx"] and c["result_status"] in (2, 3)]
        def t_of(c):
            return 0.0 if c["position"] == 1 else c["delta_leader_ms"] / 1000
        ahead = [c for c in others if t_of(c) <= target]
        behind = [c for c in others if t_of(c) > target]
        out = {"pos": len(ahead) + 1, "loss": pit_loss, "source": "distacchi"}
        game = self.c.session.get("pit_rejoin", 0)
        if game:
            out["pos"], out["source"] = game, "gioco"
        if ahead:
            a = max(ahead, key=t_of)
            out["ahead"] = {"name": self.name(a["idx"]), "gap": round(target - t_of(a), 1)}
        if behind:
            b = min(behind, key=t_of)
            out["behind"] = {"name": self.name(b["idx"]), "gap": round(t_of(b) - target, 1)}
        return out

    # ---------------------------------------------------- per dashboard e AI
    def snapshot(self):
        me = self.me()
        s = self.c.session
        out = {"is_race": self.is_race, "session_name": s.get("session_name"),
               "weather": s.get("weather"), "track_temp": s.get("track_temp"),
               "air_temp": s.get("air_temp"), "safety_car": s.get("safety_car", 0),
               "pit_ideal": s.get("pit_ideal"), "pit_latest": s.get("pit_latest"),
               "total_laps": s.get("total_laps")}
        nxt = next((f for f in s.get("forecast", []) if f["min"] > 0 and (f["weather"] >= 3 or f["rain"] >= 50)), None)
        out["rain_next"] = nxt
        if me:
            out["position"] = me["position"]
            out["pit_stops"] = me["pit_stops"]
            out["cars"] = len(self.cars)
            for key, off in (("ahead", -1), ("behind", 1)):
                car = self.rel(off)
                if car:
                    gap = (me if off < 0 else car)["delta_front_ms"] / 1000
                    t = self.tyres.get(car["idx"], {})
                    out[key] = {"name": self.name(car["idx"]), "gap": round(gap, 1),
                                "compound": t.get("compound"), "tyre_age": t.get("tyre_age"),
                                "pit": car["pit_status"] > 0}
        return out

    def context(self):
        snap = self.snapshot()
        snap["storico_distacchi"] = {
            g: {k: {"pilota": self.name(v[0]), "distacco_s": round(v[1], 2)} for k, v in h.items()}
            for g, h in list(self.gap_hist.items())[-6:]}
        snap["usura_per_giro"] = self.wear_hist
        snap["rientro_se_ti_fermi_ora"] = self.rejoin_estimate(self.c.pit_loss)
        return snap
