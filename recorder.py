"""Salva su disco la telemetria di ogni giro, per la pagina Analisi.

Struttura:  dati/<sessione>/meta.json  e  dati/<sessione>/giro_NN.json
"""
import json
import os
import re
import time

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dati")
STEP = 3.0     # un punto ogni 3 metri
SAFE = re.compile(r"^[A-Za-z0-9_\-]+$")


class Recorder:
    def __init__(self, data_dir=DATA_DIR):
        self.data_dir = data_dir
        self.session_dir = None
        self.session_uid = None

    def _ensure_session(self, uid, session):
        if self.session_uid == uid and self.session_dir:
            return
        self.session_uid = uid
        sid = time.strftime("%Y%m%d-%H%M%S") + f"_{session.get('track_id', 'x')}"
        self.session_dir = os.path.join(self.data_dir, sid)
        os.makedirs(self.session_dir, exist_ok=True)
        meta = {"id": sid, "uid": str(uid), "track": session.get("track_name", "-"),
                "track_id": session.get("track_id"),
                "session": session.get("session_name", "sessione"), "started": time.time()}
        with open(os.path.join(self.session_dir, "meta.json"), "w") as f:
            json.dump(meta, f, ensure_ascii=False)

    def save_lap(self, uid, session, lap, info):
        """lap: oggetto coach.Lap; info: numero giro, tempo, validità, settori, staccate."""
        if len(lap) < 20:
            return
        self._ensure_session(uid, session)
        tr = {k: [] for k in ("d", "t", "v", "thr", "brk", "gear", "x", "z")}
        d, end = lap.dist[0], lap.dist[-1]
        while d <= end:
            tr["d"].append(round(d, 1))
            tr["t"].append(round(lap.interp(lap.t, d), 3))
            tr["v"].append(round(lap.interp(lap.speed, d)))
            tr["thr"].append(round(lap.interp(lap.thr, d), 2))
            tr["brk"].append(round(lap.interp(lap.brk, d), 2))
            tr["gear"].append(int(round(lap.interp(lap.gear, d))))
            tr["x"].append(round(lap.interp(lap.x, d), 1))
            tr["z"].append(round(lap.interp(lap.z, d), 1))
            d += STEP
        out = dict(info, trace=tr)
        with open(os.path.join(self.session_dir, f"giro_{info['lap']:02d}.json"), "w") as f:
            json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        idx_path = os.path.join(self.session_dir, "giri.json")
        try:
            with open(idx_path) as f:
                index = json.load(f)
        except (OSError, ValueError):
            index = []
        index = [x for x in index if x["lap"] != info["lap"]]
        row = {"lap": info["lap"], "ms": info["ms"], "valid": info["valid"]}
        if info.get("partial"):
            row.update(partial=True, kind=info.get("kind", "parziale"))
        index.append(row)
        with open(idx_path, "w") as f:
            json.dump(sorted(index, key=lambda x: x["lap"]), f)


def list_sessions(data_dir=DATA_DIR):
    out = []
    if not os.path.isdir(data_dir):
        return out
    for sid in sorted(os.listdir(data_dir), reverse=True):
        p = os.path.join(data_dir, sid)
        try:
            with open(os.path.join(p, "meta.json")) as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        try:
            with open(os.path.join(p, "giri.json")) as f:
                laps = json.load(f)
        except (OSError, ValueError):
            laps = []
        if laps:
            meta["laps"] = laps
            out.append(meta)
    return out


def load_lap(sid, lap, data_dir=DATA_DIR):
    if not SAFE.match(sid or "") or not str(lap).isdigit():
        return None
    try:
        with open(os.path.join(data_dir, sid, f"giro_{int(lap):02d}.json")) as f:
            return f.read()
    except OSError:
        return None
