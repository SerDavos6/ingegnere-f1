"""Profilo del pilota: cosa dicono tutti i tuoi giri messi insieme.

- risultati e record per pista (da storico.py)
- i tuoi punti deboli ricorrenti: in quale fase della curva lasci più tempo, su tutte le piste
  (frenata, velocità a centro curva, uscita/gas, traiettoria)
- per ogni pista le 3 curve dove perdi di più, con il nome della curva
- se stai migliorando: tempo lasciato nelle curve a giro, sessione dopo sessione
"""
import glob
import json
import os

import curve
import recorder
import storico

KINDS = {
    "frenata": "Freni troppo presto",
    "centro": "Poca velocità a centro curva",
    "uscita": "Gas pieno troppo tardi in uscita",
    "traiettoria": "Traiettoria (frenata e velocità giuste, ma perdi lo stesso)",
}
MIN_LOSS = 0.05
_cache = {}


def kind_of(z):
    bd, vd, fd = z.get("brake_delta"), z.get("vmin_delta"), z.get("full_delta")
    if bd is not None and bd <= -12:
        return "frenata"
    if vd is not None and vd <= -4:
        return "centro"
    if fd is not None and fd >= 20:
        return "uscita"
    return "traiettoria"


def _sessions_key(data_dir):
    try:
        return tuple(sorted((d, os.path.getmtime(os.path.join(data_dir, d))) for d in os.listdir(data_dir)))
    except OSError:
        return ()


def build(data_dir):
    key = _sessions_key(data_dir)
    hit = _cache.get(data_dir)
    if hit and hit[0] == key:
        return hit[1]
    res = _build(data_dir)
    _cache[data_dir] = (key, res)
    return res


def _build(data_dir):
    kinds = {k: {"loss": 0.0, "n": 0} for k in KINDS}
    by_track, trend, laps_n = {}, [], 0
    for m in sorted(recorder.list_sessions(data_dir), key=lambda m: m.get("started") or 0):
        d = os.path.join(data_dir, m["id"])
        tid, tname = m.get("track_id"), m.get("track") or "-"
        cat = m.get("category_name") or ""
        tr = by_track.setdefault((tname, cat), {"track": tname, "category_name": cat, "track_id": tid,
                                                "corners": {}, "laps": 0, "kinds": {k: 0.0 for k in KINDS}})
        sess_loss, sess_laps = 0.0, 0
        for f in sorted(glob.glob(os.path.join(d, "giro_*.json"))):
            try:
                with open(f) as fh:
                    lap = json.load(fh)
            except (OSError, ValueError):
                continue
            zones = lap.get("zones") or []
            if not lap.get("valid") or not zones or lap.get("partial"):
                continue
            laps_n += 1
            tr["laps"] += 1
            sess_laps += 1
            for z in zones:
                # in gara non conta il distacco dal giro più veloce (gestisci gomme e traffico) ma quanto
                # quella curva è andata peggio del tuo solito in quella gara
                loss = (z.get("pace_loss") if lap.get("race") else z.get("loss")) or 0
                if loss <= MIN_LOSS or loss > 1.0:     # più di un secondo: errore o traffico, non un'abitudine
                    continue
                k = kind_of(z)
                kinds[k]["loss"] += loss
                kinds[k]["n"] += 1
                tr["kinds"][k] += loss
                sess_loss += loss
                # stessa curva in giri diversi: la riconosco dalla distanza (a 50 m)
                at = z.get("at") or 0
                ck = int(round(at / 50.0))
                cr = tr["corners"].setdefault(ck, {"at": at, "loss": 0.0, "n": 0, "kinds": {}})
                cr["loss"] += loss
                cr["n"] += 1
                cr["kinds"][k] = cr["kinds"].get(k, 0) + loss
        if sess_laps >= 3:
            trend.append({"date": m.get("started"), "track": tname, "session": m.get("session"),
                          "loss_per_lap": round(sess_loss / sess_laps, 3)})

    total = sum(v["loss"] for v in kinds.values()) or 1.0
    weak = sorted(({"kind": k, "label": KINDS[k], "share": round(v["loss"] / total * 100), "times": v["n"]}
                   for k, v in kinds.items() if v["n"]), key=lambda x: -x["share"])
    tracks = []
    for t in by_track.values():
        if not t["laps"]:
            continue
        cs = []
        for c in t["corners"].values():
            if c["n"] < 2:
                continue
            main = max(c["kinds"], key=c["kinds"].get)
            name = curve.name_at(t["track_id"], c["at"]) if t["track_id"] is not None else None
            cs.append({"name": name or f"curva a {c['at']:.0f} m", "avg": round(c["loss"] / t["laps"], 3),
                       "times": c["n"], "kind": main, "tip": KINDS[main]})
        cs.sort(key=lambda x: -x["avg"])
        tk = sum(t["kinds"].values()) or 1.0
        main = max(t["kinds"], key=t["kinds"].get)
        tracks.append({"track": t["track"], "category_name": t["category_name"], "laps": t["laps"],
                       "corners": cs[:3], "main": KINDS[main] if t["kinds"][main] > 0 else None,
                       "main_share": round(t["kinds"][main] / tk * 100)})
    tracks.sort(key=lambda x: -x["laps"])
    out = {"laps": laps_n, "weak": weak, "tracks": tracks, "trend": trend[-12:]}
    try:
        g = storico.gare(data_dir)
        out["races"] = g["total"]
        out["best_track"], out["worst_track"] = g.get("best_track"), g.get("worst_track")
        out["records"] = g.get("records")
    except Exception as e:
        print("Profilo: storico non letto:", e, flush=True)
    if len(trend) >= 4:
        a = sum(x["loss_per_lap"] for x in trend[:len(trend) // 2]) / (len(trend) // 2)
        b = sum(x["loss_per_lap"] for x in trend[len(trend) // 2:]) / (len(trend) - len(trend) // 2)
        out["improving"] = {"before": round(a, 2), "after": round(b, 2)}
    return out
