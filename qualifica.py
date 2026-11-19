"""Qualifica: il riepilogo mentre torni ai box, come alla radio vera.

Per ogni auto ricordo il miglior giro valido e i suoi settori (dai dati dei giri che il gioco manda
per tutti). Dopo il tuo giro lanciato ti dico: posizione provvisoria, distacco dalla pole e da chi ti
sta davanti, se sei dentro o fuori dal taglio (Q1/Q2), in che settore perdi rispetto alla pole e se
c'è tempo per un altro tentativo.
"""
from parlato import tenths

# tipi di sessione con eliminazione: Q1 e SQ1 tagliano gli ultimi, Q2 e SQ2 tengono i primi 10
Q1_TYPES = {5, 10}
Q2_TYPES = {6, 11}


class QualiBoard:
    def __init__(self, race):
        self.r = race
        self.reset()

    def reset(self):
        self.best = {}      # idx -> (ms, s1, s2)
        self.cur = {}       # idx -> [giro, invalido, s1, s2] del giro in corso

    @property
    def c(self):
        return self.r.c

    def on_laps(self, cars):
        for car in cars:
            i, lap = car["idx"], car["lap_num"]
            p = self.cur.get(i)
            if p and lap == p[0] + 1:
                ms = car.get("last_lap_ms") or 0
                if ms > 0 and not p[1] and p[2] > 0 and p[3] > 0 and ms > p[2] + p[3]:
                    b = self.best.get(i)
                    if not b or ms < b[0]:
                        self.best[i] = (ms, p[2], p[3])
                p = None
            if not p or p[0] != lap:
                p = [lap, 0, 0, 0]
            p[1] = p[1] or car.get("invalid", 0)
            p[2] = car.get("s1_ms") or p[2]
            p[3] = car.get("s2_ms") or p[3]
            self.cur[i] = p

    def cut(self):
        """Ultima posizione che passa il turno, o None se la sessione non elimina nessuno."""
        st = self.c.session.get("session_type")
        n = len(self.r.cars)
        if st in Q1_TYPES and n > 15:
            return n - 6 if n >= 22 else n - 5
        if st in Q2_TYPES and n > 10:
            return 10
        return None

    def _at(self, pos):
        car = next((c for c in self.r.cars if c["position"] == pos), None)
        return car, (self.best.get(car["idx"]) if car else None)

    def report(self):
        """Il riepilogo del giro appena fatto (None se non ho ancora il tuo tempo)."""
        me = self.r.me()
        mine = self.best.get(self.c.player_idx) if me else None
        if not me:
            return None
        pos = me["position"]
        name = self.r.name
        parts = ["Sei in pole provvisoria" if pos == 1 else f"Provvisoriamente sei P{pos}"]
        if not mine:
            return parts[0] + "."
        ms = mine[0]
        pole_car, pole = self._at(1)
        if pos == 1:
            c2, b2 = self._at(2)
            if b2:
                parts[0] += f", {tenths((b2[0] - ms) / 1000)} su {name(c2['idx'])}"
        elif pole:
            parts[0] += f", a {tenths((ms - pole[0]) / 1000)} dalla pole di {name(pole_car['idx'])}"
            if pos > 2:
                up_car, up = self._at(pos - 1)
                if up and up[0] < ms:
                    parts.append(f"per la P{pos - 1} ti {'serve' if (ms - up[0]) < 1000 else 'servono'} "
                                 f"{tenths((ms - up[0]) / 1000)}")
        cut = self.cut()
        if cut:
            nxt = "Q3" if self.c.session.get("session_type") in Q2_TYPES else "Q2"
            if pos > cut:
                _, last_in = self._at(cut)
                if last_in:
                    parts.append(f"sei fuori dalla {nxt} di {tenths((ms - last_in[0]) / 1000)}")
                else:
                    parts.append(f"per ora sei fuori dalla {nxt}")
            else:
                _, first_out = self._at(cut + 1)
                if first_out:
                    margin = (first_out[0] - ms) / 1000
                    parts.append(f"sei dentro la {nxt} con {tenths(margin)} di margine" + (", al limite" if margin < 0.2 else ""))
        # settore dove perdi rispetto alla pole (o a chi ti sta dietro se sei in pole: dove sei forte)
        if pos > 1 and pole and pole[1] and mine[1]:
            mine3 = (mine[1], mine[2], ms - mine[1] - mine[2])
            pole3 = (pole[1], pole[2], pole[0] - pole[1] - pole[2])
            loss = [(m - p) / 1000 for m, p in zip(mine3, pole3)]
            worst = max(range(3), key=lambda k: loss[k])
            if loss[worst] >= 0.05:
                txt = f"il tempo lo perdi nel settore {worst + 1}, {tenths(loss[worst])}"
                best = min(range(3), key=lambda k: loss[k])
                if loss[best] <= -0.05:
                    txt += f", mentre nel {best + 1} sei più veloce di lui"
                parts.append(txt)
        # c'è tempo per un altro giro?
        left = self.c.session.get("time_left")
        if left is not None and left > 0:
            lap_s = ms / 1000
            if left >= 2 * lap_s + 20:
                parts.append(f"hai tempo per un altro tentativo, mancano {max(1, round(left / 60))} minuti")
            elif left >= lap_s:
                parts.append("c'è tempo solo per un altro giro di fila: se le gomme reggono, spingi subito")
        return ". ".join(p[0].upper() + p[1:] for p in parts) + "."
