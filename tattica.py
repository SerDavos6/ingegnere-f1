"""Tattica in gara: duelli, batteria, pioggia in arrivo e gomme negli ultimi giri.

- Batteria per il duello: quando lotti con qualcuno da un paio di giri ti dico dove ricaricare
  (le staccate lente prima del rettilineo) e dove usarla (il rettilineo), per attaccare o difenderti.
- Sorpasso in arrivo: chi ti segue è in scia prima del rettilineo → «difendi l'interno alla staccata di curva 14».
- Pioggia in arrivo: se la sosta prevista cade poco prima della pioggia, allunghiamo e ci fermiamo
  direttamente per le intermedie; se sei sul bagnato e sta per smettere, non montare gomme nuove da bagnato.
- Ultimi giri: con l'usura che hai, le gomme arrivano in fondo? Puoi spingere o devi gestire.
"""
from parlato import plural, tenths

BATTLE_GAP = 1.2          # secondi: si lotta
SLIP_GAP = 0.6            # secondi: in scia
STRAIGHT_MIN = 400        # metri: rettilineo dove si sorpassa
WEAR_LIMIT = 75           # oltre questa usura la gomma crolla


class Tactics:
    def __init__(self, race):
        self.r = race
        self.reset()

    def reset(self):
        self.said = {}              # chiave -> giro (o momento) dell'ultimo avviso
        self.battle = {}            # idx -> giri consecutivi vicini
        self.cache = (None, None)
        self.rain_said = None
        self.dry_said = None
        self.end_said = False

    @property
    def c(self):
        return self.r.c

    def _once(self, key, laps, lap):
        last = self.said.get(key)
        if last is not None and lap - last < laps:
            return False
        self.said[key] = lap
        return True

    # ------------------------------------------------------------ la pista
    def straights(self):
        """[(inizio, fine, curva prima, curva in fondo)] dei rettilinei dove si sorpassa, il più lungo prima."""
        ref = self.c.ref
        length = self.c.session.get("track_length") or 0
        if not ref or len(ref.zones) < 3 or not length:
            return []
        key = (id(ref), len(ref.zones))
        if self.cache[0] == key:
            return self.cache[1]
        zones = sorted(ref.zones, key=lambda z: z["brake"])
        out = []
        for i, z in enumerate(zones):
            nxt = zones[(i + 1) % len(zones)]
            start = z.get("full") or z["exit"]
            end = nxt["brake"] + (length if i + 1 == len(zones) else 0)
            if end - start >= STRAIGHT_MIN:
                out.append((start, end, z, nxt))
        out.sort(key=lambda s: s[0] - s[1])
        out = out[:2]
        self.cache = (key, out)
        return out

    def _to(self, d, m):
        length = self.c.session.get("track_length") or 0
        return (m - d) % length if length else None

    def _harvest(self, st):
        """Le due staccate più lente prima del rettilineo: lì si ricarica."""
        zones = sorted(self.c.ref.zones, key=lambda z: z["brake"])
        i = zones.index(st[2])
        before = [zones[(i - k) % len(zones)] for k in range(3)]
        slow = sorted(before, key=lambda z: z.get("vmin") or 999)[:2]
        return sorted(slow, key=lambda z: z["brake"])

    # ------------------------------------------------------------ in diretta
    def on_live(self, me):
        """Chiamato con i dati del giro: chi ti segue è in scia prima del rettilineo."""
        c, r = self.c, self.r
        if not me or me["pit_status"] or me["lap_num"] < 2 or c.race_done:
            return
        behind = r.rel(1)
        if not behind or behind.get("pit_status"):
            return
        d = me.get("lap_distance", -1)
        if d < 0:
            return
        for st in self.straights():
            to = self._to(d, st[0])
            if to is None or not (60 <= to <= 350):
                continue
            gap = r.gap_between(me, behind)
            if gap is None or not (0 < gap <= SLIP_GAP):
                return
            if not self._once(("scia", behind["idx"], st[0]), 3, me["lap_num"]):
                return
            if self.said.get("energia_detta") == me["lap_num"]:
                return
            n = r.name(behind["idx"])
            n = n[0].upper() + n[1:]
            end = c.zone_label(st[3])
            c.say(f"{n} in scia, a {r.c.gap_text(gap)}: difendi l'interno alla staccata di {end}.",
                  "duello", level=1, urgent=True)
            return

    # ------------------------------------------------------------ fine giro
    def on_lap(self, me, remaining):
        if not me or me["pit_status"] or remaining is None:
            return
        self._battle(me)
        self._rain(me, remaining)
        self._tyres_end(me, remaining)

    def _battle(self, me):
        """Dopo 2 giri di lotta: dove ricaricare e dove usare la batteria."""
        c, r = self.c, self.r
        lap = me["lap_num"]
        sts = self.straights()
        if lap < 3 or not sts:
            return
        ahead, behind = r.rel(-1), r.rel(1)
        near = {}
        if ahead and not ahead["pit_status"]:
            g = r.gap_between(ahead, me)
            if g is not None and 0 < g <= BATTLE_GAP:
                near[ahead["idx"]] = "attacco"
        if behind and not behind["pit_status"]:
            g = r.gap_between(me, behind)
            if g is not None and 0 < g <= BATTLE_GAP:
                near[behind["idx"]] = "difesa"
        self.battle = {i: self.battle.get(i, 0) + 1 for i in near}
        for idx, kind in near.items():
            if self.battle[idx] < 2 or not self._once(("batteria", idx), 5, lap):
                continue
            st = sts[0]
            harv = self._harvest(st)
            where = " e ".join(c.zone_label(z) for z in harv) if harv else "le staccate lente"
            after = c.zone_label(st[2])
            n = r.name(idx)
            ers = c.energy.now_pct
            if kind == "attacco":
                txt = (f"Per attaccare {n}: ricarica in staccata a {where}, poi tutta la batteria sul rettilineo "
                       f"dopo {after}. Lì è dove lo prendi.")
            else:
                txt = (f"Per difenderti da {n}: non usare la batteria nel resto del giro, ricarica a {where} "
                       f"e tienila per il rettilineo dopo {after}.")
            if ers is not None and ers < 25 and not c.energy.cycling:
                txt += f" Adesso sei al {ers} per cento: ricarica prima di tutto."
            self.said["energia_detta"] = lap
            c.say(txt, "duello", level=2)
            return

    def _lap_s(self, me):
        p = self.c.race_pace() if hasattr(self.c, "race_pace") else None
        if p:
            return p / 1000
        return (me.get("last_lap_ms") or 0) / 1000 or None

    def _rain(self, me, remaining):
        """Pioggia (o asciutto) in arrivo, confrontata con la sosta prevista."""
        c, r = self.c, self.r
        s = c.session
        lap_s = self._lap_s(me)
        if not lap_s or remaining <= 2:
            return
        comp = c.status.get("compound")
        slick = comp in ("soft", "medie", "dure", "supersoft")
        fc = [f for f in s.get("forecast", []) if f["min"] > 0]
        lap = me["lap_num"]
        if slick and s.get("weather", 0) < 3:
            rain = next((f for f in fc if f["weather"] >= 3 and f["rain"] >= 40), None)
            if not rain:
                return
            laps_to = max(1, round(rain["min"] * 60 / lap_s))
            if laps_to >= remaining:
                return
            st = r.strategy
            plan = st.last_plan
            stop_lap = plan[1] if plan and plan[0] and plan[1] else None
            key = (rain["min"] // 5, stop_lap)
            if self.rain_said == key:
                return
            kind = "leggera" if rain["weather"] == 3 else "forte"
            if stop_lap and lap < stop_lap <= lap + laps_to + 1:
                self.rain_said = key
                st.extend_until = lap + laps_to + 1          # niente chiamata box per le slick prima della pioggia
                st.rain_hold = lap + laps_to + 1
                c.say(f"Pioggia {kind} prevista tra {rain['min']} minuti, circa {plural(laps_to, 'giro', 'giri')}. "
                      f"La sosta del giro {stop_lap} cadrebbe poco prima: allunghiamo e ci fermiamo direttamente "
                      f"per le intermedie.", "strategia", level=1, urgent=True)
            elif laps_to <= 4 and self.rain_said != ("vicina", rain["min"] // 3):
                self.rain_said = ("vicina", rain["min"] // 3)
                c.say(f"Pioggia {kind} tra {rain['min']} minuti, probabilità {rain['rain']} per cento. "
                      f"Quando inizia ti dico io quando entrare: non fermarti per le slick.",
                      "meteo", level=1, urgent=True)
        elif not slick and comp:
            dry = next((f for f in fc if f["weather"] <= 2), None)
            if not dry or dry["min"] > 20:
                return
            key = dry["min"] // 5
            if self.dry_said == key:
                return
            self.dry_said = key
            c.say(f"Tra {dry['min']} minuti è previsto asciutto. Se devi fermarti, non montare gomme nuove da "
                  f"bagnato: tieni queste e passiamo alle slick quando la pista si asciuga.",
                  "meteo", level=2)

    def _tyres_end(self, me, remaining):
        """Ultimi giri: le gomme arrivano in fondo?"""
        c, r = self.c, self.r
        if self.end_said or remaining > 6 or remaining < 2:
            return
        st = r.strategy
        p = st.last_plan
        if p and p[0] and p[1] and p[1] > me["lap_num"]:
            return                                   # c'è ancora una sosta da fare
        w = c.damage.get("tyre_wear")
        rate = st.wear_rate()
        if not w or rate is None:
            return
        now = max(w)
        end = now + rate * remaining
        self.end_said = True
        if end <= WEAR_LIMIT - 10:
            txt = (f"Mancano {plural(remaining, 'giro', 'giri')} e le gomme arrivano in fondo al {round(end)} per cento: "
                   f"puoi spingere al massimo.")
        elif end <= WEAR_LIMIT:
            txt = (f"Mancano {plural(remaining, 'giro', 'giri')}: le gomme arrivano al limite, circa {round(end)} per cento. "
                   f"Spingi, ma senza esagerare in uscita dalle curve lente.")
        else:
            txt = (f"Mancano {plural(remaining, 'giro', 'giri')} e le gomme rischiano di non arrivare, "
                   f"sarebbero al {round(end)} per cento. Gestisci: entra più dolce e non pattinare in uscita.")
        c.say(txt, "strategia", level=1)
