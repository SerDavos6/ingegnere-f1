"""Calendario del campionato: l'ordine delle piste, la prossima gara e cosa sapere su quella pista.

Il calendario lo decidete voi (pagina Campionato) ed è condiviso tra tutti gli utenti del server.
Una pista è "fatta" quando dopo la creazione del calendario c'è una gara lì: si va avanti da soli.
Per la prossima pista: il tuo record, quello degli amici, com'è andata l'ultima gara lì e le curve
dove perdi di più.
"""
import json
import os
import threading
import time

F1_2026 = ["Melbourne", "Shanghai", "Suzuka", "Bahrain", "Jeddah", "Miami", "Montreal", "Monaco",
           "Barcellona", "Red Bull Ring", "Silverstone", "Spa", "Hungaroring", "Zandvoort", "Monza",
           "Baku", "Singapore", "Austin", "Città del Messico", "Interlagos", "Las Vegas", "Losail", "Abu Dhabi"]

_lock = threading.Lock()


def load(path):
    try:
        with open(path) as f:
            d = json.load(f)
        if isinstance(d.get("tracks"), list):
            return d
    except (OSError, ValueError):
        pass
    return {"tracks": [], "created": None}


def save(path, tracks, valid):
    tracks = [str(t) for t in tracks if str(t) in valid][:40]
    old = load(path)
    # stesso calendario (solo riordinato o allungato in fondo): tengo la data, se no riparte da capo
    keep = old.get("created") and old["tracks"][:len(tracks)] == tracks[:len(old["tracks"])]
    d = {"tracks": tracks, "created": old["created"] if keep else time.time()}
    with _lock:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    return d


def progress(cal, races):
    """races: [{"track", "date"}] di tutte le gare (le tue e quelle fatte insieme).
    → (indice della prossima pista, [date delle piste fatte])"""
    tracks, start = cal.get("tracks") or [], cal.get("created") or 0
    i, done = 0, []
    for r in sorted((r for r in races if (r.get("date") or 0) >= start), key=lambda r: r.get("date") or 0):
        if i < len(tracks) and str(r.get("track", "")).lower() == tracks[i].lower():
            done.append(r.get("date"))
            i += 1
    return i, done


def next_info(track, my_records, board_rows, my_races, profile_tracks):
    """Cosa sapere sulla prossima pista."""
    t = track.lower()
    rec = [r for r in my_records if str(r.get("track", "")).lower() == t and r.get("ms")]
    friends = [r for r in board_rows if str(r.get("track", "")).lower() == t]
    last = next((r for r in my_races if str(r.get("track", "")).lower() == t), None)
    prof = next((p for p in profile_tracks if str(p.get("track", "")).lower() == t), None)
    out = {"track": track,
           "records": [{"category_name": r.get("category_name"), "ms": r["ms"]} for r in rec],
           "friends": [], "last_race": None, "corners": [], "main": None}
    for row in friends:
        for x in row["rows"]:
            if not x.get("me"):
                out["friends"].append({"driver": x["driver"], "ms": x["ms"], "category_name": row.get("category_name")})
    if last:
        out["last_race"] = {"date": last.get("date"), "finish": last.get("finish"), "grid": last.get("grid"),
                            "best_ms": last.get("best_ms"), "dnf": last.get("dnf")}
    if prof:
        out["corners"] = prof.get("corners") or []
        out["main"] = prof.get("main")
    return out


def spoken(info):
    """Una frase per il briefing e per Discord."""
    bits = [f"Prossima gara del campionato: {info['track']}"]
    lr = info.get("last_race")
    if lr:
        bits.append("l'ultima volta qui ti sei ritirato" if lr.get("dnf") or not lr.get("finish")
                    else f"l'ultima volta qui sei arrivato P{lr['finish']}")
    if info.get("corners"):
        c = info["corners"][0]
        bits.append(f"il punto da curare è {c['name']}: {c['tip'].split(' (')[0].lower()}")
    elif not info.get("records"):
        bits.append("non ci hai ancora mai girato: fai qualche giro di prova")
    return ". ".join(b[0].upper() + b[1:] for b in bits) + "."
