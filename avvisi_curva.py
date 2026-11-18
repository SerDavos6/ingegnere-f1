"""Avvisi prima della curva, come fanno i coach AI su PC (AiMotor, Trophi).

Dopo la curva l'ingegnere sa già cosa è andato storto. Al giro dopo, qualche secondo prima della
frenata, te lo ricorda: "Curva 14, puoi frenare 15 metri dopo". Solo sulle 1-2 curve dove perdi di più
(o su quella dell'allenamento mirato), una volta per giro, e smette quando la curva va bene.

Prova e time trial: a voce. Qualifica: mai durante il giro lanciato. Gara: solo con "Tanto".
"""

LEAD_S = 4.0          # secondi prima della frenata (la voce ci mette un attimo a partire)
MIN_LEAD_M = 120
MAX_CUES = 2          # curve seguite insieme
MAX_LAPS = 4          # giri di fila con l'avviso sulla stessa curva senza miglioramenti, poi pausa
PAUSE_LAPS = 3
MIN_LOSS = 0.08

# il tipo di consiglio → il gruppo di "Su cosa lavorare" (Impostazioni)
GROUP = {"freni_prima": "frenata", "freni_tardi": "frenata", "vmin": "centro", "ingresso": "centro",
         "gas": "uscita", "uscita": "uscita"}


class CornerCues:
    def __init__(self, coach):
        self.c = coach
        self.reset()

    def reset(self):
        self.cand = {}        # staccata -> {"kind", "loss", "m", "lap", "streak"}
        self.paused = {}      # staccata -> giro fino al quale non avviso
        self.done = set()     # staccate già avvisate in questo giro
        self.lap = None

    # ------------------------------------------------------------ dopo ogni curva
    def on_zone(self, z, kind, loss, meters=None):
        """Chiamato dall'analisi della curva: kind None = curva fatta bene (o senza un consiglio)."""
        n, lap = z["n"], self.c.lap_num or 0
        old = self.cand.get(n)
        if kind is None or loss < MIN_LOSS:
            if old:
                self.cand.pop(n, None)          # sistemata: non la ricordo più
            return
        streak = old["streak"] + 1 if old and old["kind"] == kind and lap - old["lap"] <= 2 else 1
        self.cand[n] = {"kind": kind, "loss": loss, "m": meters, "lap": lap, "streak": streak}
        if streak > MAX_LAPS:
            # dopo 4 giri con lo stesso avviso, se non cambia niente, smetto per un po'
            self.paused[n] = lap + PAUSE_LAPS
            self.cand.pop(n, None)
        else:
            self.prewarm(z, self.cand[n])

    def active(self):
        """Le curve da avvisare al prossimo giro."""
        c, lap = self.c, self.c.lap_num or 0
        focus = c.training.focus_zone()
        work = c.work_on or "tutto"
        out = []
        for n, x in self.cand.items():
            if lap - x["lap"] > 2 or self.paused.get(n, -1) >= lap:
                continue
            if focus is not None and n != focus:
                continue
            if work != "tutto" and GROUP.get(x["kind"]) != work:
                continue
            out.append((x["loss"], n, x))
        out.sort(reverse=True)
        return [(n, x) for _, n, x in out[:1 if focus is not None else MAX_CUES]]

    # ------------------------------------------------------------ durante il giro
    def on_distance(self, d, speed_kmh):
        c = self.c
        if c.lap_num != self.lap:
            self.lap, self.done = c.lap_num, set()
        if not c.ref or c.lap_pit or d < 0 or (c.in_race and (c.lap_num or 0) <= 1):
            return
        if c.is_quali and c.on_flying_lap():
            return
        zones = {z["n"]: z for z in c.ref.zones}
        for n, x in self.active():
            z = zones.get(n)
            if not z or n in self.done:
                continue
            lead = max(MIN_LEAD_M, speed_kmh / 3.6 * LEAD_S)
            start = z["brake"] - lead
            if start < 0:
                continue                         # frenata troppo vicina al traguardo
            if start <= d < z["brake"] - 30:
                self.done.add(n)
                focus = c.training.focus_zone() == n
                level = 1 if focus else 3 if c.in_race else 2
                c.say(self.text(z, x), "curva", level=level, urgent=True)

    # ------------------------------------------------------------ le frasi
    def text(self, z, x):
        tag = self.c.zone_label(z, capital=True)
        k, m = x["kind"], x.get("m")
        if k == "freni_prima":
            return f"{tag}, puoi frenare {m:.0f} metri dopo." if m and m >= 15 else f"{tag}, frena un filo dopo."
        if k == "freni_tardi":
            return f"{tag}, frena un attimo prima, entra pulito."
        if k in ("vmin", "ingresso"):
            return f"{tag}, porta più velocità a centro curva."
        return f"{tag}, gas pieno prima in uscita."

    def prewarm(self, z, x):
        """Preparo subito l'audio sul server, così al giro dopo la voce parte senza ritardo."""
        try:
            self.c.prewarm(self.text(z, x), "curva")
        except Exception:
            pass
