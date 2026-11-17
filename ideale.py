"""Giro ideale: la somma dei tuoi tratti migliori di pista.

Ogni giro valido viene diviso in tratti di SEG metri. Per ogni tratto si prende il tempo più
basso fra tutti i giri della sessione: la somma è il giro che faresti mettendo insieme il meglio
di ogni giro. La differenza col tuo giro migliore dice quanto tempo hai "lasciato in pista", e dove.
"""
import bisect

SEG = 100.0


def _t_at(d_arr, t_arr, d):
    i = bisect.bisect_left(d_arr, d)
    if i <= 0:
        return t_arr[0]
    if i >= len(d_arr):
        return t_arr[-1]
    d0, d1 = d_arr[i - 1], d_arr[i]
    f = 0 if d1 == d0 else (d - d0) / (d1 - d0)
    return t_arr[i - 1] + (t_arr[i] - t_arr[i - 1]) * f


def segments(d_arr, t_arr, length):
    """Tempi (s) di ogni tratto da 0 a length. L'ultimo tratto arriva al tempo del giro."""
    n = max(1, int(length // SEG))
    edges = [k * SEG for k in range(n)] + [length]
    times = [_t_at(d_arr, t_arr, e) for e in edges]
    return [max(0.0, times[k + 1] - times[k]) for k in range(n)]


def compute(laps, length=None):
    """laps: lista di dict con ms, d, t (giri validi e completi). Restituisce None se ne servono di più.

    Il giro ideale = tempo del giro migliore - somma di quanto il migliore perde in ogni tratto
    rispetto al tratto più veloce, così resta coerente col tempo ufficiale del giro.
    """
    laps = [l for l in laps if l and len(l.get("d") or []) > 20]
    if len(laps) < 2:
        return None
    length = length or min(l["d"][-1] for l in laps)
    segs = []
    for l in laps:
        s = segments(l["d"], l["t"], length)
        # l'ultimo tratto chiude sul tempo ufficiale del giro
        s[-1] = max(0.0, l["ms"] / 1000 - sum(s[:-1]))
        segs.append(s)
    n = min(len(s) for s in segs)
    best_i = min(range(len(laps)), key=lambda i: laps[i]["ms"])
    best = segs[best_i]
    mins = [min(s[k] for s in segs) for k in range(n)]
    loss = [best[k] - mins[k] for k in range(n)]
    ideal_ms = round(laps[best_i]["ms"] - sum(loss) * 1000)
    # i due tratti di 300 metri dove il giro migliore perde di più
    win = 3
    sums = [(sum(loss[k:k + win]), k) for k in range(max(1, n - win + 1))]
    sums.sort(reverse=True)
    spots, used = [], set()
    for val, k in sums:
        if val < 0.03 or len(spots) == 2:
            break
        if any(j in used for j in range(k, k + win)):
            continue
        used.update(range(k, k + win))
        spots.append({"da_m": int(k * SEG), "a_m": int(min(length, (k + win) * SEG)), "perdi_s": round(val, 3)})
    return {"ideal_ms": ideal_ms, "best_ms": laps[best_i]["ms"], "best_lap": laps[best_i].get("lap"),
            "laps": len(laps), "gap_s": round(sum(loss), 3), "spots": spots, "loss_by_seg": loss, "seg_m": SEG}
