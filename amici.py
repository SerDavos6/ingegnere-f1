"""Giri degli amici: confronto nella pagina Analisi e riferimento del coach.

Due modi per avere il giro di un amico:
  1. Stesso server su internet (modalità cloud): i giri migliori degli altri utenti
     sono già lì. Ognuno può disattivare la condivisione nelle impostazioni.
  2. File: l'amico tocca "Esporta giro" nella sua pagina Analisi e ti manda il file
     (WhatsApp, Telegram, email); tu lo carichi con "Carica giro amico".
     I file caricati stanno in amici/ e restano anche dopo il riavvio.
"""
import json
import os
import re
import time
import uuid

import f1_packets as P
import recorder

FILE_ID = re.compile(r"^[a-f0-9]{32}$")
TRACK_IDS = {name.lower(): tid for tid, name in P.TRACKS.items()}
MAX_POINTS = 12000


def _int(x, default=0):
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def track_id_of(meta_or_lap):
    tid = meta_or_lap.get("track_id")
    if isinstance(tid, int):
        return tid
    return TRACK_IDS.get(str(meta_or_lap.get("track", "")).lower())


# ------------------------------------------------------------- file caricati
def _index_path(friends_dir):
    return os.path.join(friends_dir, "indice.json")


def _load_index(friends_dir):
    try:
        with open(_index_path(friends_dir)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def validate(lap):
    """Controlla un giro caricato da file. Restituisce il giro pulito oppure un messaggio d'errore."""
    if not isinstance(lap, dict):
        return "Il file non è un giro dell'ingegnere."
    tr = lap.get("trace")
    if not isinstance(tr, dict):
        return "Il file non contiene la telemetria del giro."
    keys = ("d", "t", "v", "thr", "brk", "gear", "x", "z")
    n = len(tr.get("d") or [])
    if n < 20 or n > MAX_POINTS:
        return "Il giro nel file è troppo corto o troppo lungo."
    clean = {}
    for k in keys:
        arr = tr.get(k)
        if not isinstance(arr, list) or len(arr) != n or not all(isinstance(x, (int, float)) for x in arr):
            return f"Il file è danneggiato (campo {k})."
        clean[k] = arr
    ms = _int(lap.get("ms"))
    if ms <= 0:
        return "Nel file manca il tempo del giro."
    track = str(lap.get("track") or "")[:40]
    if not track:
        return "Nel file manca la pista."
    out = {"driver": (str(lap.get("driver") or "Amico").strip() or "Amico")[:40], "track": track,
           "track_id": track_id_of(lap), "ms": ms, "valid": bool(lap.get("valid", True)),
           "lap": _int(lap.get("lap")), "s1": _int(lap.get("s1")), "s2": _int(lap.get("s2")),
           "s3": _int(lap.get("s3")),
           "compound": str(lap.get("compound") or "")[:20] or None, "trace": clean,
           "category": str(lap.get("category") or "")[:20] or None}
    return out


def save_upload(friends_dir, lap):
    lap = validate(lap)
    if isinstance(lap, str):
        return {"error": lap}
    os.makedirs(friends_dir, exist_ok=True)
    fid = uuid.uuid4().hex
    with open(os.path.join(friends_dir, fid + ".json"), "w") as f:
        json.dump(lap, f, ensure_ascii=False, separators=(",", ":"))
    index = _load_index(friends_dir)
    index.append({"id": "f:" + fid, "driver": lap["driver"], "track": lap["track"], "ms": lap["ms"],
                  "added": time.time(), "category": lap.get("category")})
    with open(_index_path(friends_dir), "w") as f:
        json.dump(index, f, ensure_ascii=False)
    return {"ok": True, "id": "f:" + fid, "driver": lap["driver"], "track": lap["track"], "ms": lap["ms"]}


def delete_upload(friends_dir, lap_id):
    fid = lap_id[2:] if lap_id.startswith("f:") else ""
    if not FILE_ID.match(fid):
        return False
    try:
        os.remove(os.path.join(friends_dir, fid + ".json"))
    except OSError:
        pass
    index = [x for x in _load_index(friends_dir) if x["id"] != lap_id]
    with open(_index_path(friends_dir), "w") as f:
        json.dump(index, f, ensure_ascii=False)
    return True


# --------------------------------------------------------- altri utenti del server
def same_category(a, b):
    """Le sessioni registrate prima della divisione per categoria non hanno la categoria: valgono per tutte."""
    return not a or not b or a == b


def _best_of_user(tenant, track, category=None):
    """Il giro valido più veloce di un utente su una pista (e categoria): (sid, info del giro) oppure None."""
    best = None
    for s in recorder.list_sessions(tenant.data_dir):
        if str(s.get("track", "")).lower() != track.lower():
            continue
        if category and s.get("category") != category:
            continue
        for l in s["laps"]:
            if l.get("valid") and not l.get("partial") and (best is None or l["ms"] < best[1]["ms"]):
                best = (s["id"], l)
    return best


def list_laps(me, tenants, track, category=None):
    """Giri degli amici per una pista e categoria: quelli degli altri utenti e quelli caricati da file."""
    out = []
    for name, t in tenants.items():
        if t is me or not t.share_laps():
            continue
        b = _best_of_user(t, track, category)
        if b:
            out.append({"id": f"u:{name}:{b[0]}:{b[1]['lap']}", "driver": name, "track": track,
                        "ms": b[1]["ms"], "source": "server"})
    for x in _load_index(me.friends_dir):
        if x["track"].lower() == track.lower() and same_category(x.get("category"), category):
            out.append(dict(x, source="file"))
    return sorted(out, key=lambda x: x["ms"])


def load(me, tenants, lap_id):
    """Il giro completo (telemetria compresa) di un amico, come dict, oppure None."""
    if lap_id.startswith("f:"):
        fid = lap_id[2:]
        if not FILE_ID.match(fid):
            return None
        try:
            with open(os.path.join(me.friends_dir, fid + ".json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    if lap_id.startswith("u:"):
        parts = lap_id.split(":")
        if len(parts) != 4:
            return None
        _, name, sid, lap = parts
        t = tenants.get(name)
        if t is None or t is me or not t.share_laps():
            return None
        body = recorder.load_lap(sid, lap, t.data_dir)
        if body is None:
            return None
        out = json.loads(body)
        meta = next((s for s in recorder.list_sessions(t.data_dir) if s["id"] == sid), {})
        out.update(driver=name, track=meta.get("track"), track_id=track_id_of(meta), category=meta.get("category"))
        return out
    return None
