"""Utenti e password per la modalità server su internet.

Se esiste almeno un utente, il server chiede il login e ogni utente ha:
la sua porta UDP (dove la sua PS5 manda i dati), i suoi giri, i suoi riferimenti, le sue impostazioni.

Gli amici possono anche registrarsi da soli dalla pagina /registrati, se conoscono il codice invito.
Ognuno riceve in automatico la prima porta libera fra PORTA_MIN e PORTA_MAX.

Uso da terminale:
  python3 utenti.py aggiungi marco 20777     (chiede la password)
  python3 utenti.py password marco           (cambia la password)
  python3 utenti.py rimuovi marco
  python3 utenti.py elenco
  python3 utenti.py invito PISTA2026         (codice per registrarsi da soli)
  python3 utenti.py invito                   (mostra il codice attuale)
  python3 utenti.py invito --spento          (nessuno può più registrarsi da solo)
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
INVITE_PATH = os.path.join(BASE, "invito.json")
NAME_RE = re.compile(r"^[a-z0-9_]{2,20}$")
RESERVED = {"locale", "admin", "root", "_rimossi"}
PORTA_MIN, PORTA_MAX = 20777, 20796      # 20 utenti: le stesse porte vanno aperte su Oracle
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
    u.setdefault("creato", time.time())
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


# ---- registrazione con codice invito
def invite_code():
    try:
        with open(INVITE_PATH) as f:
            return str(json.load(f).get("codice") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def set_invite_code(code):
    _save(INVITE_PATH, {"codice": code})


def name_taken(name, users):
    """Usato da un utente, o come cartella dei dati di chi si è rinominato."""
    return name in users or any(u.get("cartella") == name for u in users.values())


def free_port(users=None):
    used = {int(u.get("udp_port", 0)) for u in (users or load_users()).values()}
    for p in range(PORTA_MIN, PORTA_MAX + 1):
        if p not in used:
            return p
    return None


def register(name, password, password2, code):
    """Crea un utente dalla pagina /registrati. Ritorna (porta, None) oppure (None, messaggio d'errore)."""
    expected = invite_code()
    if not expected:
        return None, "La registrazione è chiusa: chiedi a chi gestisce il server di crearti l'utente."
    if not hmac.compare_digest(code.strip().encode(), expected.encode()):
        return None, "Codice invito sbagliato."
    if not NAME_RE.match(name) or name in RESERVED:
        return None, "Nome non valido: da 2 a 20 caratteri tra lettere minuscole, numeri e _."
    if len(password) < 8:
        return None, "La password deve avere almeno 8 caratteri."
    if password != password2:
        return None, "Le due password non coincidono."
    with _lock:
        users = load_users()
        if name_taken(name, users):
            return None, "Questo nome è già usato, scegline un altro."
        port = free_port(users)
        if port is None:
            return None, "Il server è pieno: non ci sono più posti liberi."
        set_user(name, password, port)
    return port, None


# ---- profilo (dalla dashboard)
def folder_of(name, u=None):
    """Cartella dei dati: resta quella del primo nome anche se l'utente si rinomina."""
    u = u if u is not None else load_users().get(name, {})
    return u.get("cartella") or name


def change_password(name, current, new, new2):
    if not verify(name, current):
        return "La password attuale è sbagliata."
    if len(new) < 8:
        return "La nuova password deve avere almeno 8 caratteri."
    if new != new2:
        return "Le due nuove password non coincidono."
    set_user(name, new)
    return None


def rename(old, new, current):
    new = new.strip().lower()
    if not verify(old, current):
        return "La password è sbagliata."
    if new == old:
        return "È già il tuo nome."
    if not NAME_RE.match(new) or new in RESERVED:
        return "Nome non valido: da 2 a 20 caratteri tra lettere minuscole, numeri e _."
    with _lock:
        users = load_users()
        if name_taken(new, users):
            return "Questo nome è già usato, scegline un altro."
        u = users.pop(old)
        u["cartella"] = folder_of(old, u)
        users[new] = u
        _save(USERS_PATH, users)
        s = _load_sessions()
        for v in s.values():
            if v.get("user") == old:
                v["user"] = new
        _save(SESS_PATH, s)
    return None


# ---- amministratore (chi gestisce il server)
def is_admin(name, users=None):
    """L'utente con "admin": true; se nessuno ce l'ha, il primo utente (la porta più bassa)."""
    users = users if users is not None else load_users()
    if name not in users:
        return False
    flagged = [n for n, u in users.items() if u.get("admin")]
    if flagged:
        return name in flagged
    return name == min(users, key=lambda n: int(users[n].get("udp_port", 99999)))


def reset_password(name):
    """Password nuova casuale (da dare all'amico); chiude le sue sessioni aperte."""
    if name not in load_users():
        return None
    pw = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))
    set_user(name, pw)
    end_sessions_of(name)
    return pw


def remove_user(name):
    with _lock:
        users = load_users()
        u = users.pop(name, None)
        if u is None:
            return None
        _save(USERS_PATH, users)
    end_sessions_of(name)
    return u


def end_sessions_of(name):
    with _lock:
        s = _load_sessions()
        s = {k: v for k, v in s.items() if v.get("user") != name}
        _save(SESS_PATH, s)


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
    if len(argv) < 2 or argv[1] not in ("aggiungi", "password", "rimuovi", "elenco", "invito"):
        print(__doc__)
        return 1
    cmd = argv[1]
    users = load_users()
    if cmd == "invito":
        if len(argv) < 3:
            c = invite_code()
            print(f"Codice invito: {c}" if c else "Registrazione chiusa (nessun codice invito).")
        elif argv[2] == "--spento":
            set_invite_code("")
            print("Registrazione chiusa: nessuno può più registrarsi da solo.")
        else:
            if len(argv[2]) < 4:
                print("Codice troppo corto: usa almeno 4 caratteri.")
                return 1
            set_invite_code(argv[2])
            print(f"Codice invito impostato: {argv[2]}. Chi lo conosce può registrarsi da /registrati.")
        return 0
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
        if name not in users and name_taken(name, users):
            print("Nome già usato (cartella di un utente che si è rinominato).")
            return 1
        port = int(argv[3]) if len(argv) > 3 else (free_port(users) or PORTA_MAX + 1)
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
