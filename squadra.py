"""Gioco di squadra quando tu e il tuo amico guidate per la stessa scuderia.

Come in F1:
- non vi chiamo ai box nello stesso giro (chi è dietro aspetta un giro: niente fila in corsia box)
- quando siete attaccati in pista decido come al muretto:
  * se dietro di voi arriva qualcuno (entro 2 secondi, o si avvicina in fretta), battervi vi fa perdere
    tempo a tutti e due: chi è più lento lascia passare l'altro, oppure chi è dietro non attacca
  * se dietro non c'è nessuno vicino, lotta libera: pulita, niente contatti
Ognuno dei due ingegneri fa lo stesso conto con gli stessi dati, quindi vi dicono la stessa cosa
(ognuno dal suo punto di vista).
"""
CLOSE = 1.2         # secondi tra voi due per dire che vi state battendo
THREAT = 2.0        # chi vi segue entro questo distacco è un pericolo
THREAT_FAR = 4.0    # ... o entro questo, se si avvicina in fretta
CLOSING = 0.3       # secondi a giro
FASTER = 0.25       # secondi a giro di differenza per dire "è più veloce"


class TeamPlay:
    def __init__(self, race):
        self.r = race
        self.reset()

    def reset(self):
        self.laps = {}            # idx -> ultimi tempi sul giro (ms)
        self.seen_lap = {}        # idx -> giro dell'ultimo tempo registrato
        self.battle = {}          # idx compagno -> giri di fila attaccati
        self.said = {}            # idx compagno -> (decisione, giro)
        self.threat_gap = {}      # idx -> distacco della minaccia al giro prima
        self.split_done = False   # soste divise con il compagno: già decise

    @property
    def c(self):
        return self.r.c

    def mates(self):
        try:
            return [f for f in (self.c.friend_info() or []) if self.r.same_team(f["idx"]) and f["idx"] in self.r.by_idx]
        except Exception:
            return []

    def _record(self, car):
        idx = car["idx"]
        if car.get("last_lap_ms") and self.seen_lap.get(idx) != car["lap_num"]:
            self.seen_lap[idx] = car["lap_num"]
            self.laps.setdefault(idx, []).append(car["last_lap_ms"])
            del self.laps[idx][:-4]

    def _avg(self, idx):
        v = self.laps.get(idx, [])[-3:]
        return sum(v) / len(v) / 1000 if len(v) >= 2 else None

    # ------------------------------------------------------------ box nello stesso giro
    def hold_box(self, me):
        """Il compagno davanti si ferma questo giro: io aspetto il prossimo."""
        for f in self.mates():
            car = self.r.by_idx.get(f["idx"])
            if car and f.get("box_lap") == me["lap_num"] and car["position"] < me["position"]:
                return f["name"]
        return None

    # ------------------------------------------------------------ soste divise
    def _split(self, me):
        """Compagni con la sosta prevista quasi allo stesso giro: chi è davanti anticipa, l'altro allunga.
        Così non fate la fila ai box e la squadra copre due strategie contro gli avversari."""
        st = self.r.strategy
        plan = st.last_plan
        if self.split_done or me["lap_num"] < 2 or not plan or not plan[0] or not plan[1]:
            return
        mine_plan = st.team_lap or plan[1]
        if mine_plan - me["lap_num"] < 3:
            return
        for f in self.mates():
            car = self.r.by_idx.get(f["idx"])
            his = f.get("team_lap") or f.get("plan_lap")
            if not car or not his or (f.get("stops") or 0) != (me.get("pit_stops") or 0):
                continue
            ahead = me["position"] < car["position"]
            if f.get("team_lap"):
                # il suo ingegnere ha già deciso: mi metto dall'altra parte
                if (ahead and mine_plan <= his - 3) or (not ahead and mine_plan >= his + 3):
                    self.split_done = True          # siamo già divisi bene
                    return
                mine = his - 3 if ahead else his + 3
            else:
                if abs(his - mine_plan) > 2:
                    continue
                base = min(his, mine_plan)
                mine = base - 1 if ahead else base + 3
            mine = max(mine, me["lap_num"] + 2)
            remaining = self.r.strategy.remaining(me) or 99
            if mine >= me["lap_num"] + remaining - 2:
                continue
            self.split_done = True
            st.team_lap = mine
            other = base + 3 if ahead and not f.get("team_lap") else (his if f.get("team_lap") else base - 1)
            n = f["name"]
            if ahead:
                txt = (f"Tu e {n} avete la sosta prevista quasi allo stesso giro. Per non fare la fila ai box e "
                       f"coprire due strategie: tu anticipi al giro {mine}, {n} allunga fino al {other}.")
            else:
                txt = (f"Tu e {n} avete la sosta prevista quasi allo stesso giro. Lui è davanti e si ferma per primo, "
                       f"verso il giro {other}: tu allunghi fino al {mine}, così nel finale hai le gomme più fresche.")
            self.c.say(txt, "squadra", level=1)
            return

    # ------------------------------------------------------------ ordini di scuderia, a fine giro
    def on_lap(self, me):
        if me and not me["pit_status"]:
            try:
                self._split(me)
            except Exception as e:
                print("Soste divise non calcolate:", e, flush=True)
        if not me or me["lap_num"] <= 2 or me["pit_status"]:
            return
        self._record(me)
        for f in self.mates():
            car = self.r.by_idx.get(f["idx"])
            if not car or car["pit_status"] or car.get("result_status") not in (2, None):
                continue
            self._record(car)
            idx = f["idx"]
            if abs(car["position"] - me["position"]) != 1:
                self.battle[idx] = 0
                continue
            front, rear = (car, me) if car["position"] < me["position"] else (me, car)
            gap = self.r.gap_between(front, rear)
            self.battle[idx] = self.battle.get(idx, 0) + 1 if gap < CLOSE else 0
            if self.battle[idx] < 2:
                continue
            # chi vi segue?
            threat = next((c for c in self.r.cars if c["position"] == rear["position"] + 1), None)
            tg = self.r.gap_between(rear, threat) if threat else None
            prev = self.threat_gap.get(idx)
            self.threat_gap[idx] = tg
            closing = (prev - tg) if (prev is not None and tg is not None) else 0
            danger = tg is not None and (tg < THREAT or (tg < THREAT_FAR and closing >= CLOSING))
            fa, ra = self._avg(front["idx"]), self._avg(rear["idx"])
            rear_faster = fa is not None and ra is not None and fa - ra >= FASTER
            decision = ("cambio" if rear_faster else "in_fila") if danger else "libera"
            last = self.said.get(idx)
            if last and last[0] == decision and me["lap_num"] - last[1] < 6:
                continue
            self.said[idx] = (decision, me["lap_num"])
            self._say(decision, me is front, f["name"], threat, tg, (fa - ra) if rear_faster else 0)

    def _say(self, decision, i_am_front, mate, threat, tg, diff):
        name = self.r.name(threat["idx"]) if threat else ""
        g = self.c.gap_text(tg) if tg is not None else ""
        if decision == "libera":
            txt = (f"Lotta libera con {mate}: dietro non c'è nessuno vicino. Pulito, niente contatti, "
                   "e non perdiamo troppo tempo.")
        elif decision == "cambio":
            if i_am_front:
                txt = (f"Lascia passare {mate}: è più veloce di {self.c.gap_text(diff)} a giro e dietro arriva "
                       f"{name} a {g}. Se vi battete vi prende tutti e due.")
            else:
                txt = (f"{mate} ti lascia passare: dietro c'è {name} a {g}. Passalo pulito sul rettilineo, "
                       "poi spingi.")
        else:   # in fila
            if i_am_front:
                txt = f"{mate} ti copre le spalle: {name} è a {g} da lui. Spingi, restate in fila."
            else:
                txt = (f"Non attaccare {mate}: {name} è a {g} da te. Se vi battete vi prende. "
                       "Restate in fila e difendi da lui.")
        self.c.say(txt, "squadra", level=1, urgent=True)
