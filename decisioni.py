"""Partenza, pioggia e danni: le decisioni in cui serve un conto, non solo un avviso.

- Partenza: nel giro di formazione ti ricordo di scaldare gomme e freni; dopo il via ti dico com'è andata
  (scatto allo spegnimento dei semafori, ruote che pattinano, posizioni alla prima curva).
- Pioggia: guardo chi ha già le intermedie (o chi è tornato sulle slick) e confronto i tempi con i tuoi.
- Danni all'ala: misuro quanto ti costa a giro e lo confronto con il tempo del cambio ai box.
"""
from parlato import tenths

SLICK = ("soft", "medie", "dure", "supersoft")
WET = ("intermedie", "da bagnato")
WING_CHANGE_S = 6.0      # secondi in più di una sosta normale per cambiare l'ala


def _median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


class Decisions:
    def __init__(self, race):
        self.r = race
        self.reset()

    def reset(self):
        self.lights_out = None        # c.now allo spegnimento dei semafori
        self.start_pos = None
        self.move_t = None            # secondi dallo spegnimento a quando la macchina si muove
        self.max_slip = 0.0
        self.start_done = False
        self.formation_said = False
        self.wet_lap = -10
        self.wing_seen = None         # (giro, danno, passo prima del danno)
        self.wing_said = 0

    @property
    def c(self):
        return self.r.c

    # ------------------------------------------------------------------ partenza
    def on_lights_out(self):
        me = self.r.me()
        self.lights_out = self.c.now
        self.start_pos = self.r.start_grid or (me["position"] if me else None)
        self.move_t, self.max_slip, self.start_done = None, 0.0, False

    def on_sample(self, speed=None, slip=None):
        c = self.c
        if not self.r.is_race or self.start_done:
            return
        if self.lights_out is None:
            # giro di formazione: in gara, prima dei semafori, la macchina si muove
            if speed and speed > 40 and not self.formation_said and (self.r.me() or {}).get("lap_num", 0) <= 1:
                self.formation_said = True
                c.say("Giro di formazione: scalda le gomme con qualche zig-zag e i freni con due o tre frenate "
                      "decise. Al via gas progressivo, non tutto subito.", "gara", level=2)
            return
        dt = c.now - self.lights_out
        if speed is not None and self.move_t is None and speed > 10:
            self.move_t = dt
        if slip is not None and dt < 3.5:
            self.max_slip = max(self.max_slip, max(abs(slip[0]), abs(slip[1])))   # ruote posteriori

    def on_lapdata(self, me):
        """Dopo la prima curva (circa 900 metri) dico com'è andata la partenza."""
        if self.lights_out is None or self.start_done or not me or me.get("lap_distance", 0) < 900:
            return
        self.start_done = True
        bits = []
        m = self.move_t
        if m is not None:
            if m <= 0.45:
                bits.append("scatto pronto")
            elif m >= 0.8:
                bits.append(f"un po' lento allo stacco, {tenths(m)} dallo spegnimento")
        if self.max_slip >= 0.5:
            bits.append("le ruote hanno pattinato: meno gas nei primi metri")
        d = (self.start_pos - me["position"]) if self.start_pos else 0
        if d > 0:
            bits.append(f"{'una posizione guadagnata' if d == 1 else f'{d} posizioni guadagnate'} alla prima curva")
        elif d < 0:
            bits.append(f"{'una posizione persa' if d == -1 else f'{-d} posizioni perse'} alla prima curva")
        else:
            bits.append("posizione tenuta")
        good = d > 0 or (d == 0 and self.max_slip < 0.5)
        txt = ("Bella partenza: " if good and d > 0 else "Partenza: ") + ", ".join(bits) + "."
        self.c.say(self.c.with_name(txt) if good and d > 0 else txt, "gara", level=2)

    # ------------------------------------------------------------------ pioggia
    def on_lap(self, me):
        """A fine giro: confronto i tuoi tempi con quelli di chi ha l'altro tipo di gomma."""
        c, r = self.c, self.r
        mine = c.status.get("compound")
        last = me.get("last_lap_ms")
        if not mine or not last or me["lap_num"] < 3 or me["lap_num"] - self.wet_lap < 3 or me["pit_status"]:
            self._wing(me)
            return
        other = WET if mine in SLICK else SLICK if mine in WET else ()
        times = []
        for car in r.cars:
            t = r.tyres.get(car["idx"], {})
            if car["idx"] == me["idx"] or car["pit_status"] or t.get("compound") not in other:
                continue
            if (t.get("tyre_age") or 0) >= 1 and car.get("last_lap_ms"):
                times.append(car["last_lap_ms"])
        if len(times) >= 2:
            gain = (last - _median(times)) / 1000
            if mine in SLICK and gain >= 1.5:
                self.wet_lap = me["lap_num"]
                c.say(f"Chi ha le intermedie gira {tenths(gain)} più veloce di te. Box, box: montiamo le intermedie.",
                      "strategia", level=1, urgent=True)
            elif mine in WET and gain >= 1.0:
                self.wet_lap = me["lap_num"]
                c.say(f"La pista si asciuga: chi è sulle slick gira {tenths(gain)} più veloce. "
                      "Box per le slick.", "strategia", level=1, urgent=True)
        self._wing(me)

    # ------------------------------------------------------------------ danni all'ala
    def _wing(self, me):
        c = self.c
        wing = c.damage.get("front_wing", 0) or 0
        if wing < 15:
            self.wing_seen = None
            return
        if self.wing_seen is None or wing > self.wing_seen[1] + 10:
            self.wing_seen = (me["lap_num"], wing, c.race_pace())
            return
        lap0, w0, pace = self.wing_seen
        if me["lap_num"] - lap0 < 2 or self.wing_said >= wing or not pace:
            return
        laps = [h["ms"] for h in c.history if h.get("race") and h["valid"] and h.get("lap", 0) > lap0][-2:]
        if len(laps) < 2:
            return
        cost = (sum(laps) / len(laps) - pace) / 1000          # secondi persi a giro
        rem = self.r.strategy.remaining(me) or 0
        if rem <= 1:
            return
        self.wing_said = wing
        plan = self.r.strategy.last_plan
        stop_soon = plan and plan[0] and plan[1] and plan[1] - me["lap_num"] <= 6
        total = cost * rem
        change = c.pit_loss + WING_CHANGE_S
        if cost < 0.15:
            txt = f"Ala danneggiata al {wing} per cento, ma non ti costa quasi niente sul giro. Resta fuori."
        elif stop_soon:
            txt = (f"Con l'ala al {wing} per cento perdi circa {tenths(cost)} a giro: la cambiamo alla sosta "
                   f"del giro {plan[1]}.")
        elif total > change:
            txt = (f"L'ala ti costa {tenths(cost)} a giro: in {rem} giri sono {round(total)} secondi, il cambio ne "
                   f"costa circa {round(change)}. Box, cambiamo l'ala.")
        else:
            txt = (f"L'ala ti costa {tenths(cost)} a giro: in {rem} giri sono {round(total)} secondi, il cambio ne "
                   f"costa circa {round(change)}. Resta fuori.")
        c.say(txt, "strategia", level=1, urgent=total > change)
