"""Utenti e password per la modalità server su internet.

Se esiste almeno un utente, il server chiede il login e ogni utente ha:
la sua porta UDP (dove la sua PS5 manda i dati), i suoi giri, i suoi riferimenti, le sue impostazioni.

Uso da terminale:
  python3 utenti.py aggiungi marco 20777     (chiede la password)
  python3 utenti.py password marco           (cambia la password)
  python3 utenti.py rimuovi marco
  python3 utenti.py elenco
"""
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
USERS_PATH = os.path.join(BASE, "utenti.json")
SESS_PATH = os.path.join(BASE, "sessioni.json")
NAME_RE = re.compile(r"^[a-z0-9_]{2,20}$")
SESSION_DAYS = 90
_lock = threading.Lock()


def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()


def load_users():
    try:
        with open(USERS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def set_user(name, password, udp_port=None):
    users = load_users()
    salt = secrets.token_hex(16)
    u = users.get(name, {})
    u.update({"salt": salt, "hash": _hash(password, salt)})
    if udp_port:
        u["udp_port"] = int(udp_port)
    users[name] = u
    _save(USERS_PATH, users)


def verify(name, password):
    u = load_users().get(name)
    if not u:
        _hash(password, "00" * 16)       # stesso tempo anche se l'utente non esiste
        return False
    return hmac.compare_digest(_hash(password, u["salt"]), u["hash"])


def _load_sessions():
    try:
        with open(SESS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def new_session(name):
    token = secrets.token_urlsafe(32)
    with _lock:
        s = {k: v for k, v in _load_sessions().items() if v["exp"] > time.time()}
        s[token] = {"user": name, "exp": time.time() + SESSION_DAYS * 86400}
        _save(SESS_PATH, s)
    return token


def user_for(token):
    if not token:
        return None
    v = _load_sessions().get(token)
    if v and v["exp"] > time.time() and v["user"] in load_users():
        return v["user"]
    return None


def end_session(token):
    with _lock:
        s = _load_sessions()
        if s.pop(token, None):
            _save(SESS_PATH, s)


def _ask_password():
    while True:
        p1 = getpass.getpass("Password (almeno 8 caratteri): ")
        if len(p1) < 8:
            print("Troppo corta.")
            continue
        if p1 != getpass.getpass("Ripeti la password: "):
            print("Le password non coincidono.")
            continue
        return p1


def main(argv):
    if len(argv) < 2 or argv[1] not in ("aggiungi", "password", "rimuovi", "elenco"):
        print(__doc__)
        return 1
    cmd = argv[1]
    users = load_users()
    if cmd == "elenco":
        for n, u in users.items():
            print(f"{n}: porta UDP {u.get('udp_port')}")
        return 0
    if len(argv) < 3:
        print(__doc__)
        return 1
    name = argv[2].lower()
    if not NAME_RE.match(name):
        print("Nome non valido: usa da 2 a 20 lettere minuscole, numeri o _.")
        return 1
    if cmd == "aggiungi":
        port = int(argv[3]) if len(argv) > 3 else 20777 + len(users)
        if any(u.get("udp_port") == port for n, u in users.items() if n != name):
            print(f"La porta {port} è già usata da un altro utente.")
            return 1
        set_user(name, _ask_password(), port)
        print(f"Utente {name} creato. Nel gioco dovrà usare la porta UDP {port}.")
    elif cmd == "password":
        if name not in users:
            print("Utente non trovato.")
            return 1
        set_user(name, _ask_password())
        print("Password cambiata.")
    elif cmd == "rimuovi":
        users.pop(name, None)
        _save(USERS_PATH, users)
        print(f"Utente {name} rimosso (i suoi giri restano su disco).")
    print("Riavvia il server per applicare le modifiche: sudo systemctl restart ingegnere-f1")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
