"""Nomi delle curve, scritti dagli utenti nella pagina Analisi.

Le staccate si trovano da sole nel giro di riferimento e sono numerate in ordine (1, 2, 3…).
Il numero però cambia da un riferimento all'altro, quindi il nome si lega alla posizione in pista:
"a 2.150 metri dal traguardo c'è la Variante della Roggia". Una staccata prende il nome
più vicino entro RAGGIO metri dal suo punto di frenata.

File: nomi_curve.json  →  {"11": [{"m": 760, "nome": "Variante del Rettifilo"}, ...]}
Sul server su internet il file è unico: i nomi scritti da uno valgono per tutti.
"""
import json
import os
import threading

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nomi_curve.json")
RAGGIO = 200          # metri
MAX_NOME = 40
_lock = threading.Lock()

# Nomi già pronti, ricavati da giri veri (distanza del punto di frenata dal traguardo, in metri).
# Valgono finché un utente non scrive un nome suo nello stesso punto.
# Shanghai (2): da una gara vera con le auto 2026, 7 staccate.
BUILTIN = {
    "2": [{"m": 570, "nome": "curva 1"}, {"m": 845, "nome": "curva 3"}, {"m": 1475, "nome": "curva 6"},
          {"m": 2250, "nome": "curva 9"}, {"m": 3030, "nome": "curva 11"}, {"m": 4655, "nome": "curva 14"},
          {"m": 5090, "nome": "curva 16"}],
}
_cache = {"mtime": None, "data": {}}


def _load():
    try:
        mt = os.stat(PATH).st_mtime_ns
    except OSError:
        return {}
    if mt != _cache["mtime"]:
        try:
            with open(PATH) as f:
                data = json.load(f)
            _cache.update(mtime=mt, data=data if isinstance(data, dict) else {})
        except (OSError, ValueError):
            return _cache["data"]
    return _cache["data"]


def names(track_id):
    if track_id is None:
        return []
    mine = _load().get(str(track_id), [])
    base = [dict(x, builtin=True) for x in BUILTIN.get(str(track_id), [])
            if all(abs(x["m"] - y["m"]) > RAGGIO / 2 for y in mine)]
    return sorted(mine + base, key=lambda x: x["m"])


def name_at(track_id, m):
    best = None
    for x in names(track_id):
        dist = abs(x["m"] - m)
        if dist <= RAGGIO and (best is None or dist < best[0]):
            best = (dist, x["nome"])
    return best[1] if best else None


def set_name(track_id, m, nome):
    """Scrive (o cancella, con nome vuoto) il nome della curva vicina a m metri."""
    nome = " ".join(str(nome or "").split())[:MAX_NOME]
    m = int(round(float(m)))
    with _lock:
        data = dict(_load())
        lst = [x for x in data.get(str(track_id), []) if abs(x["m"] - m) > RAGGIO / 2]
        if nome:
            lst.append({"m": m, "nome": nome})
        data[str(track_id)] = sorted(lst, key=lambda x: x["m"])
        tmp = PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, PATH)
    return names(track_id)
