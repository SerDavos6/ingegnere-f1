"""Ingegnere di pista per EA F1 su PS5.

Due modi di funzionare:
- LOCALE (Termux, Raspberry, PC di casa): nessun utente configurato, nessuna password.
  La PS5 manda i dati all'IP del telefono; la dashboard si apre nella rete di casa.
- SERVER SU INTERNET (Oracle Cloud): ci sono utenti in utenti.json. Ogni utente ha
  la sua porta UDP, i suoi dati e la sua password. Vedi CLOUD.md.

Uso:  python3 server.py [--udp-port 20777] [--http-port 8080] [--bind 0.0.0.0] [--public-ip X]
Solo libreria standard di Python 3.8+.
"""
import argparse
import json
import os
import queue
import socket
import ssl
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import engineer_ai
import f1_packets as P
import recorder
import utenti
from coach import Coach
from risposte import Answers

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
ARGS = None


# --------------------------------------------------------------------------- utente / PS5
class Tenant:
    """Tutto quello che riguarda una PS5: telemetria, coach, giri salvati, impostazioni."""

    def __init__(self, name, udp_port, root):
        self.name, self.udp_port = name, udp_port
        self.data_dir = os.path.join(root, "dati")
        self.ref_dir = os.path.join(root, "riferimenti")
        self.config_path = os.path.join(root, "config.json")
        self.clients, self.clients_lock = [], threading.Lock()
        self.lock = threading.Lock()
        self.recent = []
        self.stats = {"packets": 0, "last_packet": 0.0, "format": None}
        self.coach = Coach(self.emit, data_dir=self.data_dir, ref_dir=self.ref_dir)
        cfg = engineer_ai.load_config(self.config_path)
        self.coach.pit_loss = float(cfg.get("pit_loss", 22.0))
        self.coach.verbosity = int(cfg.get("verbosity", 2))
        self.answers = Answers(self.coach)

    def broadcast(self, event, payload):
        line = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        with self.clients_lock:
            for q in list(self.clients):
                try:
                    q.put_nowait(line)
                except queue.Full:
                    pass

    def emit(self, kind, text, speak, priority):
        msg = {"kind": kind, "text": text, "speak": speak, "priority": priority, "ts": time.time()}
        if kind not in ("zona", "fine_giro"):
            self.recent.append(msg)
            del self.recent[:-30]
            print(f"[{self.name}] [{kind}] {text}", flush=True)
        self.broadcast("msg", msg)

    # ---- telemetria
    def udp_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self.udp_port))
        print(f"[{self.name}] in ascolto telemetria UDP sulla porta {self.udp_port}", flush=True)
        while True:
            data, _ = sock.recvfrom(4096)
            self.handle(data)

    def handle(self, data):
        h = P.parse_header(data)
        if not h:
            return
        self.stats.update(packets=self.stats["packets"] + 1, last_packet=time.time(), format=h["format"])
        pid, idx = h["packet_id"], h["player_idx"]
        if idx >= P.num_cars(h["format"]):
            return
        c = self.coach
        try:
            with self.lock:
                c.on_header(h)
                if pid == P.PID_SESSION:
                    s = P.parse_session(data)
                    s and c.on_session(s)
                elif pid == P.PID_LAP:
                    c.on_laps_all(P.parse_lap_all(data))
                    v = P.parse_lap(data, idx)
                    v and c.on_lap(v)
                elif pid == P.PID_MOTION:
                    v = P.parse_motion(data, idx)
                    v and c.on_motion(v)
                elif pid == P.PID_PARTICIPANTS:
                    c.race.on_participants(P.parse_participants(data))
                elif pid == P.PID_TELEMETRY:
                    v = P.parse_telemetry(data, idx)
                    v and c.on_telemetry(v)
                elif pid == P.PID_STATUS:
                    v = P.parse_status(data, idx)
                    v and c.on_status(v)
                    c.race.on_status_all(P.parse_status_all(data))
                elif pid == P.PID_DAMAGE:
                    v = P.parse_damage(data, idx)
                    v and c.on_damage(v)
        except Exception as e:  # un pacchetto strano non deve fermare tutto
            print(f"[{self.name}] errore pacchetto {pid}: {e}", flush=True)

    def snapshot(self):
        with self.lock:
            snap = self.coach.snapshot()
        snap["online"] = time.time() - self.stats["last_packet"] < 2
        snap["packets"] = self.stats["packets"]
        return snap

    # ---- domande
    def ask(self, question):
        """Prima le risposte a regole (gratis, istantanee); se la domanda è diversa, l'AI."""
        with self.lock:
            answer = self.answers.answer(question)
            ctx = self.coach.context_for_ai() if answer is None else None
        if answer is None:
            answer = engineer_ai.ask(ctx, question, self.config_path)
        if answer is None:
            answer = ("Non ho capito la domanda. " + Answers.HELP +
                      " Per le domande libere inserisci una chiave Gemini gratuita nelle impostazioni.")
        self.emit("ingegnere", answer, True, 2)
        return answer

    # ---- impostazioni
    def settings_view(self):
        cfg = engineer_ai.load_config(self.config_path)
        mask = lambda k: (k[:4] + "…" + k[-4:]) if len(k) > 10 else ("impostata" if k else "")
        return {"verbosity": self.coach.verbosity, "ai": engineer_ai.provider(cfg),
                "gemini_key": mask(cfg.get("gemini_key", "")), "anthropic_key": mask(cfg.get("anthropic_key", "")),
                "pit_loss": self.coach.pit_loss, "user": self.name if MODE == "cloud" else None,
                "udp_port": self.udp_port, "server_ip": ARGS.public_ip if ARGS else None}

    def update_settings(self, body):
        cfg = engineer_ai.load_config(self.config_path)
        if "verbosity" in body:
            try:
                self.coach.verbosity = cfg["verbosity"] = min(3, max(1, int(body["verbosity"])))
            except (ValueError, TypeError):
                pass
        for k in ("gemini_key", "anthropic_key"):
            if k in body:
                cfg[k] = str(body[k]).strip()
        if "pit_loss" in body:
            try:
                self.coach.pit_loss = cfg["pit_loss"] = min(60.0, max(10.0, float(body["pit_loss"])))
            except (ValueError, TypeError):
                pass
        engineer_ai.save_config(cfg, self.config_path)


def state_loop():
    while True:
        time.sleep(0.1)
        for t in TENANTS.values():
            if t.clients:
                t.broadcast("state", t.snapshot())


TENANTS = {}
MODE = "locale"
PORT_TO_TENANT = {}


def setup_tenants(udp_port):
    global MODE
    users = utenti.load_users()
    if users:
        MODE = "cloud"
        for name, u in users.items():
            TENANTS[name] = Tenant(name, int(u["udp_port"]), os.path.join(BASE, "utenti_dati", name))
    else:
        TENANTS["locale"] = Tenant("locale", udp_port, BASE)   # stesse cartelle delle versioni precedenti


# --------------------------------------------------------------------------- pagine web
LOGIN_HTML = """<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#0b0d10">
<title>Accesso · Ingegnere F1</title><style>
*{box-sizing:border-box;margin:0}body{background:#0b0d10;color:#e8edf2;font-family:-apple-system,Roboto,sans-serif;
min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
form{background:#14181d;border:1px solid #232a32;border-radius:14px;padding:22px;width:100%;max-width:340px;display:flex;flex-direction:column;gap:12px}
h1{font-size:16px;letter-spacing:.08em;text-transform:uppercase}h1 b{color:#e10600}
input{background:#0b0d10;border:1px solid #232a32;color:#e8edf2;border-radius:10px;padding:13px;font-size:16px}
button{background:#e10600;color:#fff;border:0;border-radius:10px;padding:13px;font-size:16px;font-weight:700}
.err{color:#ff6b72;font-size:14px;min-height:18px}</style></head><body>
<form method="post" action="/login"><h1><b>F1</b> Ingegnere</h1>
<input name="u" placeholder="Utente" autocapitalize="none" autocomplete="username" required>
<input name="p" type="password" placeholder="Password" autocomplete="current-password" required>
<div class="err">__ERR__</div><button>Entra</button></form></body></html>"""

failed_logins = {}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError, ssl.SSLError, OSError):
            pass        # browser che chiude la connessione o rifiuta il certificato: normale

    # ---- utilità
    def _send(self, code, body, ctype, headers=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json")

    def _redirect(self, to, headers=None):
        self._send(302, b"", "text/plain", dict(headers or {}, Location=to))

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(n) if n else b""

    def _cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _secure(self):
        return self.headers.get("X-Forwarded-Proto") == "https" or isinstance(self.connection, ssl.SSLSocket)

    def _client_ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def _tenant(self):
        if MODE == "locale":
            return TENANTS["locale"]
        name = utenti.user_for(self._cookie("f1s"))
        return TENANTS.get(name) if name else None

    def _static(self, name):
        with open(os.path.join(STATIC, name), "rb") as f:
            self._send(200, f.read(), "text/html; charset=utf-8")

    def _maybe_https_redirect(self, path):
        """Modalità locale: dall'IP di casa in http passa da solo alla pagina https (serve al microfono)."""
        if MODE != "locale" or not HTTPS_PORT or isinstance(self.connection, ssl.SSLSocket):
            return False
        host = (self.headers.get("Host") or "").split(":")[0]
        if host in ("127.0.0.1", "localhost", "") or path not in ("/", "/index.html", "/analisi", "/analisi.html"):
            return False
        self._redirect(f"https://{host}:{HTTPS_PORT}{self.path}")
        return True

    # ---- login
    def _login(self):
        ip = self._client_ip()
        fails = failed_logins.get(ip, [0, 0.0])
        if fails[0] >= 5 and time.time() - fails[1] < 300:
            return self._send(429, LOGIN_HTML.replace("__ERR__", "Troppi tentativi, riprova tra 5 minuti."),
                              "text/html; charset=utf-8")
        form = urllib.parse.parse_qs(self._body().decode("utf-8", "ignore"))
        user = (form.get("u", [""])[0]).strip().lower()
        pw = form.get("p", [""])[0]
        if utenti.verify(user, pw) and user in TENANTS:
            failed_logins.pop(ip, None)
            token = utenti.new_session(user)
            cookie = f"f1s={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={utenti.SESSION_DAYS * 86400}"
            if self._secure():
                cookie += "; Secure"
            return self._redirect("/", {"Set-Cookie": cookie})
        failed_logins[ip] = [fails[0] + 1, time.time()]
        time.sleep(1)
        self._send(401, LOGIN_HTML.replace("__ERR__", "Utente o password sbagliati."), "text/html; charset=utf-8")

    # ---- GET
    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        if path == "/login":
            return self._send(200, LOGIN_HTML.replace("__ERR__", ""), "text/html; charset=utf-8")
        if path == "/logout":
            utenti.end_session(self._cookie("f1s") or "")
            return self._redirect("/login", {"Set-Cookie": "f1s=; Path=/; Max-Age=0"})
        if self._maybe_https_redirect(path):
            return
        t = self._tenant()
        if t is None:
            if path in ("/", "/index.html", "/analisi", "/analisi.html"):
                return self._redirect("/login")
            return self._json({"error": "login"}, 401)
        if path in ("/", "/index.html"):
            self._static("index.html")
        elif path in ("/analisi", "/analisi.html"):
            self._static("analisi.html")
        elif path == "/api/sessions":
            self._json(recorder.list_sessions(t.data_dir))
        elif path == "/api/lap":
            body = recorder.load_lap(params.get("s"), params.get("l", ""), t.data_dir)
            if body is None:
                self._json({}, 404)
            else:
                self._send(200, body, "application/json")
        elif path == "/settings":
            self._json(t.settings_view())
        elif path == "/state":
            snap = t.snapshot()
            snap["format"] = t.stats["format"]
            snap["ai"] = engineer_ai.provider(engineer_ai.load_config(t.config_path))
            self._json(snap)
        elif path == "/events":
            self._events(t)
        else:
            self._send(404, b"not found", "text/plain")

    def _events(self, t):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        q = queue.Queue(maxsize=200)
        for m in t.recent[-10:]:
            q.put(f"event: msg\ndata: {json.dumps(dict(m, speak=False), ensure_ascii=False)}\n\n")
        with t.clients_lock:
            t.clients.append(q)
        try:
            while True:
                try:
                    line = q.get(timeout=15)
                except queue.Empty:
                    line = ": ping\n\n"
                self.wfile.write(line.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with t.clients_lock:
                t.clients.remove(q)

    # ---- POST
    def do_POST(self):
        if self.path == "/login":
            return self._login()
        t = self._tenant()
        if t is None:
            return self._json({"error": "login"}, 401)
        try:
            body = json.loads(self._body() or b"{}")
        except ValueError:
            body = {}
        if self.path == "/settings":
            t.update_settings(body)
            return self._json(t.settings_view())
        if self.path == "/ask":
            question = str(body.get("question", "")).strip()[:500]
            question = question or "Fammi il debrief degli ultimi giri: dove perdo tempo e cosa cambio?"
            return self._json({"answer": t.ask(question)})
        self._send(404, b"not found", "text/plain")


# --------------------------------------------------------------------------- avvio
HTTPS_PORT = None


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def start_https(port, bind):
    """HTTPS con certificato locale (solo modalità locale): serve al microfono su iPhone/iPad."""
    cert, key = os.path.join(BASE, "cert.pem"), os.path.join(BASE, "key.pem")
    if not (os.path.exists(cert) and os.path.exists(key)):
        return False
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv = ThreadingHTTPServer((bind, port), Handler)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return True
    except (OSError, ssl.SSLError) as e:
        print("HTTPS non disponibile:", e, flush=True)
        return False


def main():
    global ARGS, HTTPS_PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--udp-port", type=int, default=20777, help="solo modalità locale")
    ap.add_argument("--http-port", type=int, default=8080)
    ap.add_argument("--https-port", type=int, default=8443, help="0 per disattivare")
    ap.add_argument("--bind", default="0.0.0.0", help="127.0.0.1 dietro Caddy sul server cloud")
    ap.add_argument("--public-ip", default=None, help="IP pubblico del server cloud (mostrato nelle impostazioni)")
    ARGS = a = ap.parse_args()
    setup_tenants(a.udp_port)
    for t in TENANTS.values():
        threading.Thread(target=t.udp_loop, daemon=True).start()
    threading.Thread(target=state_loop, daemon=True).start()
    print("=" * 60)
    if MODE == "cloud":
        print(f" Modalità SERVER SU INTERNET, utenti: {', '.join(f'{n} (UDP {t.udp_port})' for n, t in TENANTS.items())}")
        print(f" Dashboard: http://{a.bind}:{a.http_port} (pubblicata da Caddy in https)")
    else:
        ip = lan_ip()
        if a.https_port and start_https(a.https_port, a.bind):
            HTTPS_PORT = a.https_port
        print(f" Nel gioco: Impostazioni > Telemetria > IP UDP = {ip}, porta {a.udp_port}")
        print(f" Sul telefono apri:  http://{ip}:{a.http_port}" + ("  (passa da solo a https)" if HTTPS_PORT else ""))
        print(f" Sull'Android del server: http://127.0.0.1:{a.http_port}")
        print(f" Analisi dei giri:   /analisi")
        prov = engineer_ai.provider(engineer_ai.load_config(TENANTS['locale'].config_path))
        print(f" Domande libere: {prov if prov else 'solo risposte a regole (aggiungi una chiave Gemini dalle impostazioni)'}")
    print("=" * 60, flush=True)
    ThreadingHTTPServer((a.bind, a.http_port), Handler).serve_forever()


if __name__ == "__main__":
    main()
