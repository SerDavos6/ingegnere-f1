"""Storico delle gare, backup dei dati e controllo del collegamento con la PS5.

- gare(data_dir): tutte le gare salvate, con partenza, arrivo, giro migliore, costanza e soste,
  più le statistiche per pista e totali (pagina /gare).
- backup / ripristino: un file zip con giri, riferimenti, giri degli amici e impostazioni.
- diagnosi(...): cosa non va nel collegamento PS5 → server, in parole semplici (pagina /collegamento).
"""
import io
import json
import os
import shutil
import tempfile
import time
import zipfile

import recorder

BACKUP_DIRS = ("dati", "riferimenti", "amici", "assetti", "prove")
BACKUP_FILES = ("config.json", "segnalazioni.json")


# --------------------------------------------------------------------------- storico gare
def _race_of(meta):
    laps = sorted(meta.get("laps") or [], key=lambda l: l["lap"])
    if not laps or not (any(l.get("race") for l in laps) or str(meta.get("session", "")).lower() == "gara"):
        return None
    full = [l for l in laps if not l.get("partial")]
    valid = [l for l in full if l.get("valid") and l.get("ms")]
    best = min(valid, key=lambda l: l["ms"]) if valid else None
    grid = next((l.get("grid") for l in laps if l.get("grid")), None)
    finish = next((l.get("position") for l in reversed(laps) if l.get("position")), None)
    # arrivata in fondo? Sì se c'è la bandiera a scacchi o se hai fatto tutti i giri. Le gare salvate
    # prima di questa versione non lo dicono: se l'ultimo giro è a metà (sei uscito) è un ritiro.
    total = max((l.get("total_laps") or 0 for l in laps), default=0)
    if any(l.get("finished") for l in laps) or (total and laps[-1]["lap"] >= total and not laps[-1].get("partial")):
        finished = True
    elif any(l.get("result") in (4, 5, 6, 7) for l in laps) or laps[-1].get("partial"):
        finished = False
    else:
        finished = not total            # vecchie gare complete: le considero arrivate
    if not finished:
        finish = None
    sd = None
    if best:
        clean = [l["ms"] / 1000 for l in valid if l["lap"] > 1 and l["ms"] <= best["ms"] * 1.03]
        if len(clean) >= 4:
            m = sum(clean) / len(clean)
            sd = round((sum((x - m) ** 2 for x in clean) / len(clean)) ** 0.5, 3)
    return {"id": meta["id"], "uid": meta.get("uid"), "track": meta.get("track", "-"), "track_id": meta.get("track_id"),
            "category": meta.get("category"), "category_name": meta.get("category_name"),
            "date": meta.get("started"), "laps": len(full), "grid": grid, "finish": finish, "dnf": not finished,
            "total_laps": total or None,
            "gained": (grid - finish) if grid and finish else None,
            "best_ms": best["ms"] if best else None, "best_lap": best["lap"] if best else None,
            "stops": max((l.get("pit_stops") or 0) for l in laps), "consistency": sd}


def records(data_dir, track=None, category=None, exclude_uid=None):
    """Il record personale di sempre per ogni pista e categoria (giri validi e completi, anche in prova
    e qualifica), con il miglioramento dalla prima sessione. Con track: solo quella pista."""
    best = {}
    for m in recorder.list_sessions(data_dir):
        if track is not None and (str(m.get("track", "")).lower() != str(track).lower()
                                  or (m.get("category") or None) != (category or None)):
            continue
        if exclude_uid is not None and str(m.get("uid")) == str(exclude_uid):
            continue
        ok = [l for l in m.get("laps", []) if l.get("valid") and not l.get("partial") and l.get("ms")]
        if not ok:
            continue
        lap = min(ok, key=lambda l: l["ms"])
        k = (m.get("track", "-"), m.get("category_name") or "")
        r = best.setdefault(k, {"track": k[0], "category_name": k[1], "ms": None, "sessions": 0,
                                "first_ms": None, "first_date": None})
        r["sessions"] += 1
        date = m.get("started") or 0
        if r["ms"] is None or lap["ms"] < r["ms"]:
            r.update(ms=lap["ms"], date=date, session=m.get("session"), id=m.get("id"), lap=lap["lap"])
        if r["first_date"] is None or date < r["first_date"]:
            r.update(first_ms=lap["ms"], first_date=date)
    out = list(best.values())
    for r in out:
        r["gained_ms"] = r["first_ms"] - r["ms"] if r["sessions"] >= 2 else None
    return sorted(out, key=lambda r: (-(r["sessions"]), r["track"]))


def gare(data_dir):
    races = [r for r in (_race_of(m) for m in recorder.list_sessions(data_dir)) if r and r["laps"] >= 2]
    races.sort(key=lambda r: r["date"] or 0, reverse=True)

    def stats(rs):
        fin = [r["finish"] for r in rs if r["finish"]]
        gained = [r["gained"] for r in rs if r["gained"] is not None]
        return {"races": len(rs), "wins": sum(1 for f in fin if f == 1), "podiums": sum(1 for f in fin if f <= 3),
                "dnf": sum(1 for r in rs if r.get("dnf")),
                "avg_finish": round(sum(fin) / len(fin), 1) if fin else None,
                "best_finish": min(fin) if fin else None,
                "gained": sum(gained) if gained else 0,
                "best_ms": min((r["best_ms"] for r in rs if r["best_ms"]), default=None)}
    by_track = {}
    for r in races:
        by_track.setdefault((r["track"], r["category_name"] or ""), []).append(r)
    tracks = [dict(stats(rs), track=k[0], category_name=k[1]) for k, rs in by_track.items()]
    tracks.sort(key=lambda x: (-x["races"], x["track"]))
    rated = [t for t in tracks if t["avg_finish"] is not None and t["races"] >= 1]
    best = min(rated, key=lambda t: t["avg_finish"]) if rated else None
    worst = max(rated, key=lambda t: t["avg_finish"]) if len(rated) >= 2 else None
    return {"races": races, "total": stats(races), "tracks": tracks, "records": records(data_dir),
            "best_track": best["track"] if best else None,
            "worst_track": worst["track"] if worst and worst is not best else None}


# --------------------------------------------------------------------------- backup
def make_backup(root):
    """Crea lo zip in un file temporaneo e ne restituisce il percorso."""
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("LEGGIMI.txt", "Backup dell'ingegnere F1 del " + time.strftime("%d/%m/%Y %H:%M") +
                   ".\nPer ripristinarlo: Dettagli → Backup dei dati → Ripristina.\n")
        for d in BACKUP_DIRS:
            base = os.path.join(root, d)
            for cur, _, files in os.walk(base):
                for f in files:
                    if f.endswith((".tmp", ".part")):
                        continue
                    full = os.path.join(cur, f)
                    z.write(full, os.path.relpath(full, root))
        for f in BACKUP_FILES:
            if os.path.exists(os.path.join(root, f)):
                z.write(os.path.join(root, f), f)
    return path


AUTO_DAYS, AUTO_KEEP = 7, 4


def auto_backups(root):
    d = os.path.join(root, "backup_auto")
    try:
        names = sorted(f for f in os.listdir(d) if f.startswith("backup-") and f.endswith(".zip"))
    except OSError:
        return []
    return [{"name": f, "size": os.path.getsize(os.path.join(d, f)),
             "date": os.path.getmtime(os.path.join(d, f))} for f in reversed(names)]


def auto_backup(root, force=False):
    """Una copia dei dati ogni settimana sul server (le ultime 4): se cancelli qualcosa per sbaglio
    o un aggiornamento va storto, ritrovi i giri di qualche giorno prima."""
    have = auto_backups(root)
    if not force and have and time.time() - have[0]["date"] < AUTO_DAYS * 86400:
        return None
    if not os.path.isdir(os.path.join(root, "dati")):
        return None                         # utente che non ha ancora mai corso
    d = os.path.join(root, "backup_auto")
    os.makedirs(d, exist_ok=True)
    tmp = make_backup(root)
    dest = os.path.join(d, time.strftime("backup-%Y%m%d-%H%M.zip"))
    shutil.move(tmp, dest)
    for old in auto_backups(root)[AUTO_KEEP:]:
        os.remove(os.path.join(d, old["name"]))
    return dest


def restore_backup(root, zip_path):
    """Rimette giri, riferimenti, amici e impostazioni. Non cancella niente: aggiunge e sovrascrive.
    Le chiavi AI già salvate restano se nel backup mancano."""
    try:
        z = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        return {"error": "Il file non è un backup valido (serve lo zip scaricato da qui)."}
    n = 0
    with z:
        names = z.namelist()
        if not any(x.split("/")[0] in BACKUP_DIRS or x in BACKUP_FILES for x in names):
            return {"error": "Nel file non ci sono dati dell'ingegnere."}
        for name in names:
            parts = name.replace("\\", "/").split("/")
            if name.endswith("/") or ".." in parts or name.startswith("/") or ":" in parts[0]:
                continue
            if name == "config.json":
                try:
                    new = json.loads(z.read(name))
                    path = os.path.join(root, "config.json")
                    old = json.load(open(path)) if os.path.exists(path) else {}
                    for k in ("gemini_key", "anthropic_key"):
                        if not new.get(k) and old.get(k):
                            new[k] = old[k]
                    with open(path, "w") as f:
                        json.dump(new, f, ensure_ascii=False, indent=2)
                    n += 1
                except (ValueError, OSError):
                    pass
                continue
            if parts[0] not in BACKUP_DIRS:
                continue
            dest = os.path.join(root, *parts)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(name) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
            n += 1
    races = sum(1 for x in names if x.startswith("dati/") and x.endswith("meta.json"))
    return {"ok": True, "files": n, "sessions": races}


# --------------------------------------------------------------------------- collegamento
PACKET_NAMES = {0: "movimento", 1: "sessione", 2: "giri", 3: "eventi", 4: "piloti", 5: "assetto",
                6: "telemetria", 7: "stato auto", 8: "classifica", 9: "lobby", 10: "danni",
                11: "storico giri", 12: "gomme", 13: "movimento extra", 14: "dati tempo", 15: "posizioni per giro"}


def diagnosi(st, cloud, server_ip, port, session):
    """st: statistiche del collegamento. Restituisce stato ('ok', 'attesa', 'problema') e i consigli."""
    out = []
    now = time.time()
    age = now - st["last_packet"] if st.get("last_packet") else None
    ip_game = server_ip or "l'IP che il server scrive all'avvio"
    if not st.get("packets"):
        out.append(("problema", "Non è ancora arrivato nessun dato dalla PS5."))
        out.append(("passo", f"Controlla le impostazioni qui sopra una per una, soprattutto indirizzo IP ({ip_game}) "
                             f"e porta ({port}): basta una cifra sbagliata e i dati non arrivano."))
        out.append(("passo", "Entra in una sessione (anche una prova libera) e scendi in pista: "
                             "nei menu il gioco non manda dati."))
        if cloud:
            out.append(("passo", f"Su Oracle (Security List) deve esserci la regola UDP 20777-20796: "
                                 f"senza quella i dati non arrivano. La porta {port} è la tua."))
        out.append(("info", "Questa pagina si aggiorna da sola: appena arriva il primo dato lo vedi qui."))
        return "attesa", out
    if age is not None and age > 5:
        mins = int(age // 60)
        quando = f"{mins} minuti" if mins >= 2 else "1 minuto" if mins == 1 else f"{int(age)} secondi"
        out.append(("attesa", f"Ultimi dati arrivati {quando} fa. Il collegamento funziona: il gioco manda dati "
                              f"solo quando sei in una sessione (non nei menu né in pausa)."))
    else:
        out.append(("ok", "I dati della PS5 arrivano: il collegamento funziona. Vai in pista!"))
    fmt, rate = st.get("format"), st.get("rate") or 0
    if fmt and fmt != 2025:
        out.append(("consiglio", f"Il gioco manda il formato UDP {fmt}. Funziona, ma il più provato è il 2025: "
                                 f"se qualcosa non torna, in Telemetria metti Formato UDP 2025."))
    if age is not None and age <= 5:
        if cloud and rate > 35:
            out.append(("consiglio", f"Arrivano circa {round(rate)} pacchetti di telemetria al secondo: su internet "
                                     f"basta la frequenza 20 Hz, consuma meno connessione."))
        elif 0 < rate < 8:
            out.append(("consiglio", "La frequenza è bassa: in Telemetria metti almeno 20 Hz, così le staccate "
                                     "si analizzano bene."))
        if st.get("player_idx") == 255:
            out.append(("problema", "Stai guardando la gara come spettatore: l'ingegnere lavora solo quando guidi tu."))
        seen = st.get("ids") or {}
        if (st.get("packets") or 0) > 300 and 6 not in seen:
            out.append(("problema", "Arrivano dati ma non la telemetria dell'auto: controlla che la telemetria "
                                    "UDP sia attiva e non in modalità ridotta."))
    if session:
        out.append(("info", f"Sessione: {session}."))
    state = "problema" if any(k == "problema" for k, _ in out) else "ok" if out[0][0] == "ok" else "attesa"
    return state, out
