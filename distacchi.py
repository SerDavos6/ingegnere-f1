"""Distacchi calcolati da noi, come il cronometraggio della F1.

Il gioco manda già il distacco dall'auto davanti (deltaToCarInFront), ma quel valore
non è sempre affidabile: si aggiorna solo in certi punti della pista, durante e
subito dopo un sorpasso si riferisce ancora all'auto di prima, e può restare
"vecchio" per diversi secondi. Così l'ingegnere poteva dire "è a meno di un
secondo" quando in realtà era a 1,7.

Qui per ogni auto ci ricordiamo in che momento è passata da ogni punto della pista
(distanza totale percorsa, un punto ogni STEP metri). Il distacco tra due auto è:
    adesso  -  momento in cui l'auto davanti è passata dove si trova ora quella dietro
È sempre aggiornato, a ogni pacchetto, ed è lo stesso metodo dei tempi ufficiali.
"""
import bisect

STEP = 5.0            # metri tra un punto memorizzato e l'altro
KEEP = 12000.0        # metri di storia tenuti per ogni auto (più di un giro su ogni pista)


class GapTimer:
    def __init__(self):
        self.reset()

    def reset(self):
        self.hist = {}        # idx -> ([distanze], [tempi])
        self.dist = {}        # idx -> distanza attuale
        self.last_now = None

    def update(self, cars, now):
        if self.last_now is not None and now < self.last_now - 0.5:
            self.reset()                       # flashback o ripartenza: il tempo torna indietro
        self.last_now = now
        for c in cars:
            d = c.get("total_distance")
            if not d:                          # 0 = auto non in pista / dato mancante
                continue
            idx = c["idx"]
            ds, ts = self.hist.setdefault(idx, ([], []))
            if ds and d < ds[-1] - 50:         # l'auto è tornata indietro (flashback, ritiro)
                ds.clear()
                ts.clear()
            self.dist[idx] = d
            if not ds or d - ds[-1] >= STEP:
                ds.append(d)
                ts.append(now)
                if ds[-1] - ds[0] > KEEP + 1000:
                    cut = bisect.bisect_left(ds, ds[-1] - KEEP)
                    del ds[:cut]
                    del ts[:cut]

    def _passed(self, idx, d):
        """Momento in cui l'auto idx è passata alla distanza d (None se non lo sappiamo)."""
        ds, ts = self.hist.get(idx, ((), ()))
        if len(ds) < 2 or d < ds[0] or d > ds[-1]:
            return None
        i = bisect.bisect_left(ds, d)
        if i == 0:
            return ts[0]
        d0, d1 = ds[i - 1], ds[i]
        f = 0.0 if d1 == d0 else (d - d0) / (d1 - d0)
        return ts[i - 1] + (ts[i] - ts[i - 1]) * f

    def gap(self, front_idx, back_idx):
        """Secondi tra l'auto davanti e quella dietro, oppure None se non è calcolabile."""
        if self.last_now is None:
            return None
        d = self.dist.get(back_idx)
        if d is None:
            return None
        t = self._passed(front_idx, d)
        if t is None:
            return None
        g = self.last_now - t
        return g if g >= 0 else None
