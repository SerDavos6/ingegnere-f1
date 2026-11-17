"""Allenamento mirato (prova libera e time trial): una staccata alla volta.

Dopo un paio di giri l'ingegnere sceglie la staccata dove perdi di più, in media, e per qualche giro
ti parla solo di quella. A ogni giro ti dice come va ("da 3 decimi persi a 1"). Quando la sistemi
passa alla successiva; se dopo 5 giri non migliora la lascia per ora e ci torna più tardi.
Le altre staccate restano scritte sulla dashboard, ma a voce non le dice.
Si accende e si spegne in Dettagli o a voce: "Radio, spegni l'allenamento".
"""
from parlato import tenths

MIN_LOSS = 0.10        # secondi: sotto questo una staccata va già bene
MAX_LAPS = 5           # giri su una staccata prima di passare a un'altra


class Training:
    def __init__(self, coach):
        self.c = coach
        self.enabled = True
        self.reset()

    def reset(self):
        self.focus = None          # {"n", "at", "start", "last", "laps", "good"}
        self.done = set()          # staccate sistemate in questa sessione
        self.skipped = {}          # staccata -> giro in cui l'ho lasciata

    def set_enabled(self, on):
        self.enabled = bool(on)
        if not on:
            self.focus = None

    def active(self):
        c = self.c
        st = c.session.get("session_type") or 0
        return self.enabled and not c.in_race and not c.is_quali and bool(c.session) and (1 <= st <= 4 or st == 18)

    def focus_zone(self):
        return self.focus["n"] if self.focus and self.active() else None

    def _avg_losses(self):
        """Perdita media per staccata negli ultimi 3 giri completi."""
        out = {}
        for h in self.c.history[-3:]:
            for z in h.get("zones") or []:
                out.setdefault(z["n"], []).append((z["loss"], z.get("at")))
        return {n: (sum(v for v, _ in vals) / len(vals), vals[-1][1]) for n, vals in out.items() if len(vals) >= 2}

    def _label(self, n, at):
        return self.c.zone_label({"n": n, "at": at})

    def _pick(self):
        lap = self.c.lap_num or 0
        cands = [(loss, n, at) for n, (loss, at) in self._avg_losses().items()
                 if loss >= MIN_LOSS and n not in self.done and lap - self.skipped.get(n, -99) >= 6]
        if not cands:
            return None
        loss, n, at = max(cands)
        self.focus = {"n": n, "at": at, "start": loss, "last": loss, "laps": 0, "good": 0}
        return (f"Allenamento: lavoriamo su {self._label(n, at)}, dove perdi in media {tenths(loss)}. "
                f"Per qualche giro ti parlo solo di questa.")

    def on_lap_complete(self, entry, complete, valid):
        """Dopo ogni giro: il messaggio dell'allenamento da dire, oppure None."""
        if not self.active() or not complete:
            return None
        f = self.focus
        if f is None:
            return self._pick()
        res = next((z for z in entry.get("zones") or [] if z["n"] == f["n"]), None)
        if res is None:
            return None
        f["laps"] += 1
        loss, label = res["loss"], self._label(f["n"], f["at"])
        f["last"] = loss
        if loss <= max(0.04, f["start"] * 0.4):
            f["good"] += 1
        else:
            f["good"] = 0
        if f["good"] >= 2 or loss <= 0.0:
            self.done.add(f["n"])
            self.focus = None
            nxt = self._pick()
            now = "ora la fai come il riferimento" if loss <= 0.05 else f"ora perdi {tenths(loss)}"
            txt = f"{label[0].upper() + label[1:]} sistemata: perdevi {tenths(f['start'])}, {now}. Bel lavoro!"
            return txt + (" " + nxt.replace("Allenamento: lavoriamo", "Ora lavoriamo") if nxt else
                          " Per ora le altre staccate vanno bene: se ne trovo una da sistemare te lo dico.")
        if f["laps"] >= MAX_LAPS:
            self.skipped[f["n"]] = self.c.lap_num or 0
            self.focus = None
            nxt = self._pick()
            txt = f"Su {label} non migliora ancora: la lasciamo per ora e ci torniamo dopo."
            return txt + (" " + nxt.replace("Allenamento: lavoriamo", "Passiamo") if nxt else "")
        if loss < f["start"] - 0.03:
            return f"{label[0].upper() + label[1:]}: {tenths(loss)} persi, all'inizio erano {tenths(f['start'])}. Ci siamo quasi."
        return f"{label[0].upper() + label[1:]}: ancora {tenths(loss)} persi. Riprova, concentrati solo su questa."

    def status_text(self):
        if not self.enabled:
            return "L'allenamento mirato è spento. Dimmi «Radio, accendi l'allenamento» per riattivarlo."
        if not self.active():
            return "L'allenamento mirato parte in prova libera e in time trial."
        f = self.focus
        if not f:
            return "Allenamento acceso: dopo un paio di giri scelgo la staccata su cui lavorare."
        return (f"Stiamo lavorando su {self._label(f['n'], f['at'])}: all'inizio perdevi {tenths(f['start'])}, "
                f"nell'ultimo giro {tenths(f['last'])}.")

    def snapshot(self):
        f = self.focus if self.active() else None
        return {"on": self.enabled, "active": self.active(),
                "zone": self._label(f["n"], f["at"]) if f else None,
                "start": round(f["start"], 2) if f else None, "last": round(f["last"], 2) if f else None,
                "done": len(self.done)}
