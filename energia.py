"""Gestione dell'energia (batteria ERS): quando attaccare, quando difendersi, quando ricaricare.

Con le auto 2026 l'energia elettrica conta moltissimo, ma i consigli valgono anche per le 2025.
Dati dal pacchetto Car Status del gioco: energia nella batteria (in joule, al massimo 4 MJ),
modalità di utilizzo, energia recuperata e usata nel giro.

- In gara: chi hai davanti è dentro il secondo e hai batteria → "attacca sul rettilineo lungo";
  chi ti segue è vicino e la batteria è scarica → "ricarica e tieni energia per difenderti";
  batteria quasi vuota prima del rettilineo lungo → avviso.
- In prova e qualifica, a fine giro veloce: batteria avanzata ("potevi usarne di più") o finita
  prima del traguardo ("dosala meglio").
- Tendenza in gara: se a ogni giro la batteria scende parecchio, l'ingegnere lo dice.
"""
from parlato import num

FULL_J = 4e6            # capacità della batteria nel gioco (F1 25)
ATTACK_GAP = 1.0        # secondi: chi hai davanti è attaccabile
DEFEND_GAP = 1.0
MODES = {0: "spento", 1: "medio", 2: "giro veloce", 3: "sorpasso"}


def pct(j, full=FULL_J):
    return max(0, min(100, round(j / full * 100))) if j is not None else None


class Energy:
    def __init__(self, coach):
        self.c = coach
        self.reset()

    def reset(self):
        self.full = FULL_J
        self.now_pct = None
        self.mode = None
        self.lap_min = None           # (percentuale minima, distanza) del giro in corso
        self.lap_start = None         # percentuale al traguardo
        self.lap_ends = []            # percentuale a fine di ogni giro di gara
        self.said = {}                # chiave -> giro dell'ultimo avviso
        self.straight_cache = (None, None)

    # ---------------------------------------------------------- dati
    def on_status(self, st):
        j = st.get("ers_store")
        if j is None:
            return
        if j > self.full * 1.02:       # una batteria più grande (auto diverse): mi adatto
            self.full = j
        self.now_pct = pct(j, self.full)
        self.mode = st.get("ers_mode")
        ld = self.c.last_lapdata or {}
        d = ld.get("lap_distance", -1)
        if d >= 0 and (self.lap_min is None or self.now_pct < self.lap_min[0]):
            self.lap_min = (self.now_pct, d)
        self._live(ld)

    def longest_straight(self):
        """(inizio, fine) in metri del rettilineo più lungo, dal giro di riferimento."""
        ref = self.c.ref
        if not ref or len(ref.zones) < 2:
            return None
        key = (id(ref), len(ref.zones))
        if self.straight_cache[0] == key:
            return self.straight_cache[1]
        zones = sorted(ref.zones, key=lambda z: z["brake"])
        length = self.c.session.get("track_length") or 0
        best = None
        for i, z in enumerate(zones):
            nxt = zones[(i + 1) % len(zones)]
            start = z.get("full") or z["exit"]
            end = nxt["brake"] + (length if i + 1 == len(zones) and length else 0)
            if best is None or end - start > best[1] - best[0]:
                best = (start, end)
        self.straight_cache = (key, best)
        return best

    def _meters_to_straight(self, d):
        st = self.longest_straight()
        length = self.c.session.get("track_length") or 0
        if not st or not length:
            return None
        return (st[0] - d) % length

    def _once_per(self, key, laps):
        lap = self.c.lap_num or 0
        last = self.said.get(key)
        if last is not None and lap - last < laps:
            return False
        self.said[key] = lap
        return True

    # ---------------------------------------------------------- avvisi in pista
    def _live(self, ld):
        c, p = self.c, self.now_pct
        if p is None or not ld or ld.get("pit_status") or c.race_done:
            return
        d = ld.get("lap_distance", -1)
        if d < 0:
            return
        to_straight = self._meters_to_straight(d)
        near_straight = to_straight is not None and 150 <= to_straight <= 700
        r = c.race
        if c.in_race and (ld.get("lap_num") or 0) > 1:
            ahead, behind = r.rel(-1), r.rel(1)
            me = r.me()
            if ahead and me and near_straight and p >= 45 and ahead.get("pit_status") == 0:
                gap = r.gap_between(ahead, me)
                if 0 < gap <= ATTACK_GAP and self._once_per(("attacco", ahead["idx"]), 2):
                    n = r.name(ahead["idx"])
                    c.say(f"Batteria al {p} per cento e {n} è a {num(gap)}: attacca sul rettilineo lungo, "
                          f"usa l'energia in uscita dalla curva.", "energia", level=1, urgent=True)
                    return
            if behind and me and p <= 30 and behind.get("pit_status") == 0:
                gap = r.gap_between(me, behind)
                if 0 < gap <= DEFEND_GAP and self._once_per("difesa", 3):
                    n = r.name(behind["idx"])
                    c.say(f"Batteria al {p} per cento con {n} dietro a {num(gap)}: ricarica in staccata e "
                          f"tieni l'energia per difenderti sul rettilineo lungo.", "energia", level=1, urgent=True)
                    return
        # (in prova e qualifica lo dico a fine giro, non durante il giro veloce)
        if p <= 10 and near_straight and c.in_race and self._once_per("scarica", 2):
            c.say(f"Batteria quasi scarica, {p} per cento, prima del rettilineo lungo. Ricarica: "
                  f"meno energia in uscita dalle curve lente.", "energia", level=1, urgent=True)

    # ---------------------------------------------------------- fine giro
    def on_lap_complete(self, valid, complete):
        """Chiamato dal coach a ogni traguardo. Restituisce un consiglio da dire o None."""
        c, p = self.c, self.now_pct
        lap_min, start = self.lap_min, self.lap_start
        self.lap_min = None
        self.lap_start = p
        if p is None:
            return None
        if c.in_race:
            self.lap_ends.append(p)
            self.lap_ends = self.lap_ends[-4:]
            if len(self.lap_ends) >= 3:
                drop = (self.lap_ends[0] - self.lap_ends[-1]) / (len(self.lap_ends) - 1)
                if drop >= 8 and p < 50 and self._once_per("tendenza", 5):
                    laps = max(1, int(p / drop))
                    return (f"Batteria: perdi circa {round(drop)} per cento a giro, ora sei al {p}. "
                            f"Così tra {laps} {'giro' if laps == 1 else 'giri'} sei a secco: ricarica di più in staccata.")
            return None
        if not (valid and complete):
            return None
        # prova e qualifica: com'è andato il giro veloce
        length = c.session.get("track_length") or 0
        if lap_min and lap_min[0] <= 3 and length and lap_min[1] < length * 0.85 and self._once_per("finita", 2):
            left = int(round((length - lap_min[1]) / 10) * 10)
            return (f"Batteria finita a {left} metri dal traguardo: dosala meglio, meno energia nella prima parte "
                    f"del giro e tienila per gli ultimi rettilinei.")
        if p >= 35 and (start is None or p >= start - 30) and self._once_per("avanzata", 3):
            return (f"Hai chiuso il giro con il {p} per cento di batteria: potevi usarne di più, "
                    f"prova la modalità giro veloce.")
        return None

    def answer(self):
        """Risposta a "Radio, come va la batteria?"."""
        p = self.now_pct
        if p is None:
            return "Non ho ancora i dati della batteria."
        txt = f"Batteria al {p} per cento"
        if self.mode in MODES:
            txt += f", modalità {MODES[self.mode]}"
        txt += "."
        if len(self.lap_ends) >= 2:
            diff = self.lap_ends[-1] - self.lap_ends[-2]
            if abs(diff) >= 3:
                txt += f" Nell'ultimo giro {'hai recuperato' if diff > 0 else 'hai usato'} {abs(diff)} per cento."
        if p < 20:
            txt += " È bassa: ricarica in staccata e usa meno energia in uscita dalle curve lente."
        elif p > 70 and self.c.in_race:
            txt += " Ne hai tanta: puoi usarla per attaccare."
        return txt

    def snapshot(self):
        return {"pct": self.now_pct, "mode": MODES.get(self.mode)}
