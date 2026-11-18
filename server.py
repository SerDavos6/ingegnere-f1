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
import re
import shutil
import socket
import ssl
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import amici
import curve
import engineer_ai
import f1_packets as P
import ideale
import recorder
import registra
import utenti
import voce_ai
import voce_piper
import storico
import voci
from coach import Coach
from risposte import Answers

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
ARGS = None
PIPER = voce_piper.Piper(os.path.join(BASE, "piper"))   # voce del server, condivisa fra gli utenti
ENGINES = ("dev", "piper", "ai")


def voice_engine(cfg):
    """Motore scelto (dev = telefono, piper = server, ai = Gemini) e quello che si usa davvero adesso."""
    chosen = cfg.get("voice_engine") or ("ai" if cfg.get("ai_voice_on") else "piper")
    eff = chosen
    if eff == "ai" and not cfg.get("gemini_key"):
        eff = "piper"
    if eff == "piper" and not PIPER.ready():
        eff = "dev"
    return chosen, eff


# --------------------------------------------------------------------------- utente / PS5
class Tenant:
    """Tutto quello che riguarda una PS5: telemetria, coach, giri salvati, impostazioni."""

    def __init__(self, name, udp_port, root):
        self.name, self.udp_port = name, udp_port
        self.root = root
        self.data_dir = os.path.join(root, "dati")
        self.friends_dir = os.path.join(root, "amici")
        self.voice = voce_ai.VoiceAI(os.path.join(root, "voce_cache"))
        self.rec = registra.SessionRecorder(os.path.join(root, "registrazioni"))
        self.ref_dir = os.path.join(root, "riferimenti")
        self.config_path = os.path.join(root, "config.json")
        self.clients, self.clients_lock = [], threading.Lock()
        self.lock = threading.Lock()
        self.recent = []
        self.stats = {"packets": 0, "last_packet": 0.0, "format": None, "addr": None, "first_packet": None,
                      "ids": {}, "player_idx": None, "rate": 0.0, "_sec": 0, "_n": 0, "_hist": []}
        self.coach = Coach(self.emit, data_dir=self.data_dir, ref_dir=self.ref_dir)
        cfg = engineer_ai.load_config(self.config_path)
        self.coach.pit_loss = float(cfg.get("pit_loss", 22.0))
        self.coach.verbosity = int(cfg.get("verbosity", 2))
        self.coach.muted = bool(cfg.get("muted"))
        self.coach.training.enabled = cfg.get("training", True) is not False
        self.coach.targets = dict(cfg.get("targets") or {})
        self.coach.work_on = cfg.get("work_on") if cfg.get("work_on") in WORK_ON else "tutto"
        self.coach.prewarm = self.prewarm
        self._warmed = set()
        self.coach.setups_dir = os.path.join(root, "assetti")
        self.coach.teammates = lambda: find_teammates(self)
        self.coach.friend_info = lambda: friend_info(self)
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
        self.sock = sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self.udp_port))
        print(f"[{self.name}] in ascolto telemetria UDP sulla porta {self.udp_port}", flush=True)
        while True:
            try:
                data, addr = sock.recvfrom(4096)
            except OSError:
                return              # utente rimosso: socket chiuso
            self.stats["addr"] = addr[0]
            self.handle(data)

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass
        try:
            self.sock.close()
        except (OSError, AttributeError):
            pass

    def handle(self, data):
        self.rec.add(data)
        h = P.parse_header(data)
        if not h:
            return
        st, now = self.stats, time.time()
        st.update(packets=st["packets"] + 1, last_packet=now, format=h["format"], player_idx=h["player_idx"])
        if st["first_packet"] is None:
            st["first_packet"] = now
        pid, idx = h["packet_id"], h["player_idx"]
        st["ids"][pid] = st["ids"].get(pid, 0) + 1
        if pid == P.PID_TELEMETRY:          # frequenza: pacchetti di telemetria al secondo (media su 5 s)
            sec = int(now)
            if sec != st["_sec"]:
                st["_hist"] = (st["_hist"] + [st["_n"]])[-5:] if st["_sec"] else []
                st["_sec"], st["_n"] = sec, 0
                st["rate"] = sum(st["_hist"]) / len(st["_hist"]) if st["_hist"] else 0.0
            st["_n"] += 1
        if idx >= P.num_cars(h["format"]):
            return
        c = self.coach
        try:
            with self.lock:
                if c.on_header(h) is False:
                    return
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
                    c.race.on_motion_all(P.parse_motion_all(data))
                elif pid == P.PID_MOTION_EX:
                    v = P.parse_motion_ex(data)
                    v and c.on_motion_ex(v)
                elif pid == P.PID_EVENT:
                    ev = P.parse_event(data)
                    ev and c.race.on_event(ev)
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
                elif pid == P.PID_SETUPS:
                    v = P.parse_setup(data, idx)
                    v and c.on_setup(v)
        except Exception as e:  # un pacchetto strano non deve fermare tutto
            print(f"[{self.name}] errore pacchetto {pid}: {e}", flush=True)

    def snapshot(self):
        with self.lock:
            snap = self.coach.snapshot()
        snap["online"] = time.time() - self.stats["last_packet"] < 2
        snap["rec"] = self.rec.active
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
            answer = self._not_understood()
        self._save_radio_state()           # i comandi a voce (silenzio, parla di più…) restano salvati
        self.coach.last_spoken = answer     # "Radio, ripeti" ripete anche le risposte
        self.emit("ingegnere", answer, True, 2)
        return answer

    def _not_understood(self):
        """Domanda che non so a cui rispondere: la spiegazione lunga una volta sola, poi una frase corta.
        L'elenco completo lo dice "Radio, aiuto"."""
        now = time.time()
        first = now - getattr(self, "_nu_t", 0) > 1800        # la prima volta, o dopo mezz'ora
        self._nu_t = now
        if not first:
            return "Non ho capito, ripeti?"
        return ("Non ho capito. Prova con box, strategia, gomme, distacchi, meteo o batteria; "
                "dimmi «Radio, aiuto» per l'elenco completo.")

    def _save_radio_state(self):
        cfg = engineer_ai.load_config(self.config_path)
        c = self.coach
        new = {"verbosity": c.verbosity, "muted": c.muted, "training": c.training.enabled, "work_on": c.work_on}
        if any(cfg.get(k) != v for k, v in new.items()):
            cfg.update(new)
            engineer_ai.save_config(cfg, self.config_path)

    # ---- collegamento con la PS5
    def link_status(self):
        st = self.stats
        with self.lock:
            sess = self.coach.session
            session = f"{sess.get('session_name')} a {sess.get('track_name')}" if sess else None
        ip = (ARGS.public_ip if ARGS else None) or (lan_ip() if MODE != "cloud" else None)
        state, tips = storico.diagnosi(st, MODE == "cloud", ip, self.udp_port, session)
        last = st["last_packet"]
        return {"state": state, "tips": [{"kind": k, "text": t} for k, t in tips],
                "packets": st["packets"], "age": round(time.time() - last, 1) if last else None,
                "addr": st["addr"], "format": st["format"], "rate": round(st["rate"], 1),
                "types": sorted(storico.PACKET_NAMES.get(i, str(i)) for i in st["ids"]),
                "udp_port": self.udp_port, "server_ip": ARGS.public_ip if ARGS else None,
                "lan_ip": lan_ip() if MODE != "cloud" else None, "cloud": MODE == "cloud"}

    def reload_after_restore(self):
        cfg = engineer_ai.load_config(self.config_path)
        with self.lock:
            c = self.coach
            c.targets = dict(cfg.get("targets") or {})
            c.verbosity = int(cfg.get("verbosity", c.verbosity))
            if c.track_id is not None:
                c.ref = c._load_ref()

    # ---- analisi: giro ideale e progressi
    def ideal_of(self, sid):
        meta = next((s for s in recorder.list_sessions(self.data_dir) if s["id"] == sid), None)
        if not meta:
            return None
        laps = []
        for l in meta["laps"]:
            if l.get("valid") and not l.get("partial"):
                raw = recorder.load_lap(sid, l["lap"], self.data_dir)
                if raw:
                    j = json.loads(raw)
                    laps.append({"lap": l["lap"], "ms": l["ms"], "d": j["trace"]["d"], "t": j["trace"]["t"]})
        res = ideale.compute(laps)
        if res:
            res.pop("loss_by_seg", None)
        return res

    def progress(self, track, category=None):
        """Il giro migliore di ogni sessione su una pista (e categoria), dalla più vecchia alla più recente."""
        out = []
        for s in recorder.list_sessions(self.data_dir):
            if str(s.get("track", "")).lower() != track.lower():
                continue
            if not amici.same_category(s.get("category"), category):
                continue
            ok = [l for l in s["laps"] if l.get("valid") and not l.get("partial")]
            if ok:
                b = min(ok, key=lambda l: l["ms"])
                out.append({"id": s["id"], "started": s.get("started"), "session": s.get("session"),
                            "ms": b["ms"], "lap": b["lap"], "laps": len(ok)})
        return sorted(out, key=lambda x: x["started"] or 0)

    # ---- voce AI
    def prewarm(self, text, kind):
        """Prepara l'audio di un avviso che servirà tra poco (avvisi prima della curva):
        la voce del telefono poi lo trova già pronto in cache."""
        key = (text, kind)
        if key in self._warmed:
            return
        self._warmed.add(key)
        if len(self._warmed) > 300:
            self._warmed = {key}
        body = {"text": text, "kind": kind, "prio": 2, "fallback": False}
        threading.Thread(target=lambda: self.tts(body), daemon=True).start()

    def tts(self, body):
        """(audio WAV, None, motore usato) oppure (None, motivo, None) se va usata la voce del telefono.
        Voce AI che non risponde (quota, rete) → voce del server (Piper) → voce del telefono."""
        text = str(body.get("text", "")).strip()[:600]
        if not text:
            return None, "testo vuoto", None
        cfg = engineer_ai.load_config(self.config_path)
        engine = body.get("engine") if body.get("engine") in ENGINES else voice_engine(cfg)[1]
        cid = body.get("char") if voci.valid(body.get("char")) else cfg.get("voice_char", voci.DEFAULT)
        char = voci.get(cid)
        style = body.get("style") or voce_ai.style_for(text, str(body.get("kind", "")), int(body.get("prio") or 1))
        why = None
        if engine == "ai":
            try:
                return self.voice.synth(text, style, char["gemini"], cfg.get("gemini_key"),
                                        persona=char["descrizione"]), None, "ai"
            except voce_ai.QuotaError as e:
                why = str(e)
            except Exception as e:   # rete, chiave sbagliata, risposta strana
                print(f"[{self.name}] voce AI non disponibile: {e}", flush=True)
                why = "voce AI non disponibile"
        # (fallback false: la dashboard vuole una sola voce, quindi niente cambio da voce AI a voce del server)
        if (engine == "piper" or (engine == "ai" and body.get("fallback") is not False)) and PIPER.ready():
            try:
                return PIPER.synth(text, cid, style), why, "piper"
            except Exception as e:
                print(f"[{self.name}] voce del server non disponibile: {e}", flush=True)
                why = why or "voce del server non disponibile"
        return None, why or ("voce del server " + PIPER.status()["text"]), None

    # ---- giri degli amici
    def share_laps(self):
        return engineer_ai.load_config(self.config_path).get("share_laps", True) is not False

    def use_reference(self, body):
        """Usa come riferimento del coach un tuo giro salvato o quello di un amico."""
        if body.get("friend"):
            lap = amici.load(self, dict(TENANTS), str(body["friend"]))
            owner = lap.get("driver") if lap else None
        else:
            raw = recorder.load_lap(str(body.get("s", "")), str(body.get("l", "")), self.data_dir)
            lap = json.loads(raw) if raw else None
            meta = next((x for x in recorder.list_sessions(self.data_dir) if x["id"] == body.get("s")), {})
            if lap:
                lap.update(track=meta.get("track"), track_id=amici.track_id_of(meta), category=meta.get("category"))
            owner = None
        if not lap or not lap.get("trace"):
            return {"error": "Giro non trovato."}
        tid = amici.track_id_of(lap)
        if tid is None:
            return {"error": "Non riconosco la pista di questo giro."}
        with self.lock:
            ref = self.coach.set_reference(tid, lap["trace"], int(lap["ms"]), owner, lap.get("category"))
        return {"ok": True, "owner": owner, "ms": ref.lap_ms, "zones": len(ref.zones), "track": lap.get("track")}

    # ---- impostazioni
    def settings_view(self):
        cfg = engineer_ai.load_config(self.config_path)
        mask = lambda k: (k[:4] + "…" + k[-4:]) if len(k) > 10 else ("impostata" if k else "")
        return {"verbosity": self.coach.verbosity, "ai": engineer_ai.provider(cfg),
                "gemini_key": mask(cfg.get("gemini_key", "")), "anthropic_key": mask(cfg.get("anthropic_key", "")),
                "pit_loss": self.coach.pit_loss, "user": self.name if MODE == "cloud" else None,
                "share_laps": cfg.get("share_laps", True) is not False,
                "voice_engine": voice_engine(cfg)[0], "voice_engine_now": voice_engine(cfg)[1],
                "voice_char": cfg.get("voice_char", voci.DEFAULT), "voice_chars": voci.elenco(),
                "piper": PIPER.status(),
                "muted": self.coach.muted, "training_on": self.coach.training.enabled,
                "work_on": self.coach.work_on,
                "target_ms": self.coach.target_ms(), "target_track": self.coach.track_name
                if self.coach.track_id is not None else None,
                "udp_port": self.udp_port, "server_ip": ARGS.public_ip if ARGS else None,
                "team_color": cfg.get("team_color") or None,
                "admin": MODE == "cloud" and utenti.is_admin(self.name)}

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
        if "share_laps" in body:
            cfg["share_laps"] = bool(body["share_laps"])
        if body.get("voice_engine") in ENGINES:
            cfg["voice_engine"] = body["voice_engine"]
        if voci.valid(body.get("voice_char")):
            if cfg.get("voice_char") != body["voice_char"] and PIPER.ready():
                PIPER.warm([body["voice_char"]])
            cfg["voice_char"] = body["voice_char"]
        if "team_color" in body:
            c = str(body["team_color"] or "")
            cfg["team_color"] = c if re.fullmatch(r"#[0-9a-fA-F]{6}", c) else ""
        if "muted" in body:
            self.coach.muted = cfg["muted"] = bool(body["muted"])
        if body.get("work_on") in WORK_ON:
            self.coach.work_on = cfg["work_on"] = body["work_on"]
        if "training_on" in body:
            self.coach.training.set_enabled(body["training_on"])
            cfg["training"] = self.coach.training.enabled
        if "target" in body:
            key = self.coach.target_key()
            if key:
                ms = parse_lap_time(str(body["target"] or ""))
                targets = dict(cfg.get("targets") or {})
                if ms:
                    targets[key] = ms
                else:
                    targets.pop(key, None)
                cfg["targets"] = self.coach.targets = targets
                self.coach.target_said = None
        if "pit_loss" in body:
            try:
                self.coach.pit_loss = cfg["pit_loss"] = min(60.0, max(10.0, float(body["pit_loss"])))
            except (ValueError, TypeError):
                pass
        engineer_ai.save_config(cfg, self.config_path)


def parse_lap_time(txt):
    """'1:20.5', '1:20,500', '1.20.500', '80.5' -> millisecondi (None se non valido o vuoto)."""
    t = txt.strip().replace(",", ".").replace("'", ":")
    m3 = re.fullmatch(r"(\d{1,2})[:.](\d{2})\.(\d{1,3})", t)      # 1:20.500 o 1.20.500
    m2 = re.fullmatch(r"(\d{1,2}):(\d{1,2}(?:\.\d{1,3})?)", t)     # 1:20.5 o 1:20
    m1 = re.fullmatch(r"\d{2,3}(?:\.\d{1,3})?", t)                  # 80.5 (secondi)
    if m3:
        ms = int(m3[1]) * 60000 + int(m3[2]) * 1000 + int(m3[3].ljust(3, "0"))
    elif m2:
        ms = int(m2[1]) * 60000 + round(float(m2[2]) * 1000)
    elif m1:
        ms = round(float(t) * 1000)
    else:
        return None
    return ms if 20000 <= ms <= 300000 else None


# --------------------------------------------------------------------------- squadra e campionato
WORK_ON = ("tutto", "frenata", "centro", "uscita")   # Impostazioni → Su cosa lavorare


def find_teammates(me):
    """Gli altri utenti del server nella stessa gara online (stessa sessione del gioco):
    [(nome, indice della sua auto nei miei dati)]. Solo chi condivide i giri."""
    return [(n, i) for n, i, _ in _friends_in_race(me)]


def friend_info(me):
    """Cosa sa il server delle macchine degli amici in gara con te: gomme, batteria, soste, piano."""
    out = []
    for name, idx, o in _friends_in_race(me):
        oc = o.coach
        try:
            wear = oc.damage.get("tyre_wear")
            ld = oc.last_lapdata or {}
            plan = oc.race.strategy.last_plan or (False, None, None, None)     # (sosta?, giro, gomma, due soste)
            out.append({"name": name, "idx": idx, "wear": max(wear) if wear else None,
                        "compound": oc.status.get("compound"), "tyre_age": oc.status.get("tyre_age"),
                        "ers": oc.energy.now_pct, "stops": ld.get("pit_stops"),
                        "plan_lap": plan[1] if plan[0] else None})
        except Exception as e:
            print("Dati amico non letti:", e, flush=True)
    return out


def _friends_in_race(me):
    if MODE != "cloud":
        return []
    c = me.coach
    if c.session_uid is None or not c.in_race:
        return []
    out = []
    for name, o in list(TENANTS.items()):
        oc = o.coach
        if o is me or oc.session_uid != c.session_uid or time.time() - o.stats["last_packet"] > 5 or not o.share_laps():
            continue
        idx = oc.player_idx
        theirs = oc.race.names.get(idx) or {}
        # stesso pilota nei miei dati: per nome online, poi per squadra e numero, se no stesso indice
        for i, p in c.race.names.items():
            if theirs.get("name") and p.get("name") == theirs["name"] and not p.get("ai"):
                idx = i
                break
        else:
            for i, p in c.race.names.items():
                if theirs.get("number") and p.get("number") == theirs.get("number") and p.get("team") == theirs.get("team"):
                    idx = i
                    break
        out.append((name, idx, o))
    return out


POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]


def championship(me):
    """Le gare online fatte insieme (stessa sessione del gioco, almeno 2 utenti del server):
    punti come in F1 dalla posizione d'arrivo, classifica piloti e risultati gara per gara."""
    races = {}
    for name, t in list(TENANTS.items()):
        if t is not me and not t.share_laps():
            continue
        for meta in recorder.list_sessions(t.data_dir):
            r = storico._race_of(meta)
            if not r or not r.get("uid") or r["laps"] < 1:
                continue
            races.setdefault(r["uid"], []).append(dict(r, driver=name, me=t is me))
    rows, results = {}, []
    for uid, rs in races.items():
        drivers = {x["driver"] for x in rs}
        if len(drivers) < 2:
            continue
        # chi è arrivato in ordine d'arrivo, poi i ritirati (zero punti)
        rs = sorted({x["driver"]: x for x in rs}.values(), key=lambda x: (x["finish"] is None, x["finish"] or 99))
        if all(x["finish"] is None for x in rs):
            continue                    # gara lasciata a metà da tutti: non conta
        best = min((x["best_ms"] for x in rs if x["best_ms"]), default=None)
        for x in rs:
            pts = POINTS[x["finish"] - 1] if x["finish"] and x["finish"] <= len(POINTS) else 0
            x["points"] = pts
            x["fastest"] = bool(best and x["best_ms"] == best)
            row = rows.setdefault(x["driver"], {"driver": x["driver"], "me": x["me"], "points": 0, "races": 0,
                                               "wins": 0, "podiums": 0, "beaten": 0})
            row["points"] += pts
            row["races"] += 1
            fin = x["finish"]
            row["wins"] += fin == 1
            row["podiums"] += bool(fin and fin <= 3)
            row["dnf"] = row.get("dnf", 0) + (fin is None)
            row["beaten"] += sum(1 for y in rs if fin and (y["finish"] is None or y["finish"] > fin))
        results.append({"uid": uid, "track": rs[0]["track"], "date": min(x["date"] or 0 for x in rs),
                        "category_name": rs[0]["category_name"],
                        "results": [{k: x.get(k) for k in ("driver", "me", "finish", "grid", "points", "best_ms", "fastest", "dnf", "laps")}
                                    for x in rs]})
    table = sorted(rows.values(), key=lambda r: (-r["points"], -r["wins"], -r["podiums"]))
    for i, r in enumerate(table):
        r["pos"] = i + 1
        r["gap"] = table[0]["points"] - r["points"]
    results.sort(key=lambda r: r["date"], reverse=True)
    return {"cloud": MODE == "cloud", "standings": table, "races": results}


def setups_view(me, track_id=None):
    """Gli assetti del giro migliore per pista: i tuoi e quelli degli amici che condividono i giri."""
    out = []
    for name, t in list(TENANTS.items()):
        if t is not me and not t.share_laps():
            continue
        d = os.path.join(t.root, "assetti")
        try:
            files = os.listdir(d)
        except OSError:
            continue
        for f in files:
            try:
                with open(os.path.join(d, f)) as fh:
                    j = json.load(fh)
            except (OSError, ValueError):
                continue
            if track_id is not None and str(j.get("track_id")) != str(track_id):
                continue
            out.append(dict(j, driver=name if MODE == "cloud" else "Tu", me=t is me))
    out.sort(key=lambda x: (str(x.get("track")), str(x.get("category_name")), x.get("ms") or 0))
    return out


# --------------------------------------------------------------------------- classifica
def leaderboard(me):
    """Giro migliore di ogni utente su ogni pista (solo chi condivide i giri, più te)."""
    tracks = {}
    for name, t in list(TENANTS.items()):
        if t is not me and not t.share_laps():
            continue
        best = {}
        for sess in recorder.list_sessions(t.data_dir):
            key = (str(sess.get("track", "-")), sess.get("category") or "")
            for l in sess["laps"]:
                if l.get("valid") and not l.get("partial") and (key not in best or l["ms"] < best[key]["ms"]):
                    best[key] = {"ms": l["ms"], "sid": sess["id"], "lap": l["lap"], "date": sess.get("started"),
                                 "session": sess.get("session")}
        for key, b in best.items():
            row = dict(b, driver=name if MODE == "cloud" else "Tu", me=t is me,
                       id=None if t is me else f"u:{name}:{b['sid']}:{b['lap']}")
            tracks.setdefault(key, []).append(row)
    out = []
    for (track, cat), rows in tracks.items():
        rows.sort(key=lambda r: r["ms"])
        for i, r in enumerate(rows):
            r["gap"] = r["ms"] - rows[0]["ms"]
            r["pos"] = i + 1
        out.append({"track": track, "category": cat,
                    "category_name": P.CATEGORY_NAMES.get(cat, "Prima della divisione per categoria" if not cat else cat),
                    "rows": rows, "mine": any(r["me"] for r in rows)})
    out.sort(key=lambda x: (not x["mine"], -len(x["rows"]), x["track"]))
    return out


# --------------------------------------------------------------------------- admin
def admin_view():
    users = utenti.load_users()
    rows = []
    for name, u in sorted(users.items(), key=lambda kv: int(kv[1].get("udp_port", 0))):
        t = TENANTS.get(name)
        last = t.stats["last_packet"] if t else 0
        sessions = recorder.list_sessions(t.data_dir) if t else []
        rows.append({"name": name, "port": u.get("udp_port"), "created": u.get("creato"),
                     "admin": utenti.is_admin(name, users), "online": bool(last and time.time() - last < 5),
                     "last_packet": last or None, "sessions": len(sessions),
                     "last_session": sessions[0].get("started") if sessions else None})
    return {"users": rows, "invite": utenti.invite_code(), "max": utenti.PORTA_MAX - utenti.PORTA_MIN + 1,
            "server_ip": ARGS.public_ip if ARGS else None}


def admin_action(me, body):
    act, name = body.get("action"), str(body.get("user", "")).strip().lower()
    if act == "invito":
        code = str(body.get("code", "")).strip()
        if code and (len(code) < 4 or len(code) > 40 or " " in code):
            return {"error": "Il codice deve avere da 4 a 40 caratteri, senza spazi."}
        utenti.set_invite_code(code)
        return {"ok": True, "msg": f"Codice invito: {code}" if code else "Registrazione chiusa."}
    if act in ("reset", "rimuovi") and name not in utenti.load_users():
        return {"error": "Utente non trovato."}
    if act == "reset":
        pw = utenti.reset_password(name)
        return {"ok": True, "msg": f"Nuova password di {name}: {pw}", "password": pw}
    if act == "rimuovi":
        if name == me.name:
            return {"error": "Non puoi rimuovere te stesso."}
        u = utenti.remove_user(name)
        t = TENANTS.pop(name, None)
        if t:
            t.close()
        folder = os.path.join(BASE, "utenti_dati", utenti.folder_of(name, u or {}))
        if os.path.isdir(folder):    # i suoi giri non passano a chi si registrerà con lo stesso nome
            dest = os.path.join(BASE, "utenti_dati", "_rimossi", f"{name}-{time.strftime('%Y%m%d-%H%M%S')}")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(folder, dest)
        print(f"[{name}] utente rimosso da {me.name}", flush=True)
        return {"ok": True, "msg": f"Utente {name} rimosso."}
    return {"error": "Richiesta non valida."}


def state_loop():
    while True:
        time.sleep(0.1)
        for t in list(TENANTS.values()):
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
            folder = utenti.folder_of(name, u)
            TENANTS[name] = Tenant(name, int(u["udp_port"]), os.path.join(BASE, "utenti_dati", folder))
    else:
        TENANTS["locale"] = Tenant("locale", udp_port, BASE)   # stesse cartelle delle versioni precedenti


def add_tenant(name, port):
    """Utente appena registrato: parte subito, senza riavviare il server."""
    t = Tenant(name, int(port), os.path.join(BASE, "utenti_dati", name))
    TENANTS[name] = t
    threading.Thread(target=t.udp_loop, daemon=True).start()
    print(f"[{name}] nuovo utente registrato, porta UDP {port}", flush=True)
    return t


# --------------------------------------------------------------------------- pagine web
LOGIN_HTML = """<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#0b0d10">
<title>Accesso · Ingegnere F1</title><link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" href="/apple-touch-icon.png"><link rel="icon" href="/icon-192.png"><style>
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
<div class="err">__ERR__</div><button>Entra</button>__REG__</form></body></html>"""

PAGE_CSS = LOGIN_HTML.split("<style>")[1].split("</style>")[0] + """
a{color:#9fb0c0;font-size:14px;text-align:center}p{color:#9fb0c0;font-size:14px;line-height:1.45}
.box{background:#0b0d10;border:1px solid #232a32;border-radius:10px;padding:12px;font-size:15px;line-height:1.7}
.box b{color:#fff;font-size:18px}.btn{background:#e10600;color:#fff;border-radius:10px;padding:13px;font-weight:700;text-decoration:none;font-size:16px}"""


def _page(title, inner):
    return (f'<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#0b0d10">'
            f'<title>{title} · Ingegnere F1</title><link rel="manifest" href="/manifest.webmanifest">'
            f'<link rel="apple-touch-icon" href="/apple-touch-icon.png"><style>{PAGE_CSS}</style></head><body>{inner}</body></html>')


def login_page(err=""):
    reg = '<a href="/registrati">Non hai un account? Registrati</a>' if utenti.invite_code() else ""
    return LOGIN_HTML.replace("__ERR__", err).replace("__REG__", reg)


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def register_page(err="", name=""):
    return _page("Registrati", f"""<form method="post" action="/registrati"><h1><b>F1</b> Ingegnere</h1>
<p>Crea il tuo utente. Ti serve il codice invito che ti ha dato chi gestisce il server.</p>
<input name="u" placeholder="Nome utente (minuscole, numeri, _)" value="{esc(name)}" autocapitalize="none" autocomplete="username" required>
<input name="p" type="password" placeholder="Password (almeno 8 caratteri)" autocomplete="new-password" required>
<input name="p2" type="password" placeholder="Ripeti la password" autocomplete="new-password" required>
<input name="c" placeholder="Codice invito" autocapitalize="none" autocomplete="off" required>
<div class="err">{esc(err)}</div><button>Registrati</button><a href="/login">Hai già un account? Entra</a></form>""")


def welcome_page(t):
    ip = (ARGS.public_ip if ARGS else None) or "l'IP del server"
    return _page("Benvenuto", f"""<form><h1><b>F1</b> Ingegnere</h1>
<p>Benvenuto <b style="color:#fff">{esc(t.name)}</b>! Ora sulla PS5 apri F1 → Impostazioni → Telemetria e scrivi:</p>
<div class="box">Telemetria UDP: <b>On</b><br>Broadcast: <b>Off</b><br>Indirizzo IP: <b>{esc(ip)}</b><br>
Porta: <b>{t.udp_port}</b><br>Frequenza: <b>20 Hz</b><br>Formato: <b>2025</b></div>
<p>Questi dati li ritrovi sempre in Dettagli → Impostazioni. Consiglio: dal browser del telefono scegli
"Aggiungi a schermata Home", così si apre come un'app.</p>
<p>Poi entra in pista e apri il <b>controllo del collegamento</b>: ti dice subito se i dati della PS5 arrivano.</p>
<a class="btn" href="/collegamento">🔌 Controlla il collegamento</a>
<p></p><a class="btn" href="/">Vai alla dashboard</a></form>""")


failed_logins = {}
failed_regs = {}

# file senza login: servono al telefono per installare l'app (icona, nome, schermo intero)
PUBLIC_FILES = {
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/icon-maskable.png": ("icon-maskable.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/favicon.ico": ("icon-192.png", "image/png"),
}


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

    def _send_file(self, path, name, ctype="application/gzip"):
        if not path:
            return self._json({"error": "file non trovato"}, 404)
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(256 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json")

    def _redirect(self, to, headers=None):
        self._send(302, b"", "text/plain", dict(headers or {}, Location=to))

    MAX_BODY = 4 * 1024 * 1024      # un giro esportato pesa circa 100-200 KB

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > self.MAX_BODY:
            self.rfile.read(n)
            return None
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
            return self._send(429, login_page("Troppi tentativi, riprova tra 5 minuti."),
                              "text/html; charset=utf-8")
        form = urllib.parse.parse_qs(self._body().decode("utf-8", "ignore"))
        user = (form.get("u", [""])[0]).strip().lower()
        pw = form.get("p", [""])[0]
        if utenti.verify(user, pw) and user in TENANTS:
            failed_logins.pop(ip, None)
            return self._redirect("/", {"Set-Cookie": self._session_cookie(user)})
        failed_logins[ip] = [fails[0] + 1, time.time()]
        time.sleep(1)
        self._send(401, login_page("Utente o password sbagliati."), "text/html; charset=utf-8")

    def _session_cookie(self, user):
        cookie = (f"f1s={utenti.new_session(user)}; Path=/; HttpOnly; SameSite=Lax; "
                  f"Max-Age={utenti.SESSION_DAYS * 86400}")
        return cookie + ("; Secure" if self._secure() else "")

    # ---- registrazione con codice invito
    def _register(self):
        html = "text/html; charset=utf-8"
        if MODE != "cloud":
            return self._redirect("/")
        ip = self._client_ip()
        fails = failed_regs.get(ip, [0, 0.0])
        if fails[0] >= 5 and time.time() - fails[1] < 600:
            return self._send(429, register_page("Troppi tentativi, riprova tra 10 minuti."), html)
        form = urllib.parse.parse_qs((self._body() or b"").decode("utf-8", "ignore"))
        get = lambda k: form.get(k, [""])[0]
        name = get("u").strip().lower()
        port, err = utenti.register(name, get("p"), get("p2"), get("c"))
        if err:
            if "Codice" in err:
                failed_regs[ip] = [fails[0] + 1, time.time()]
                time.sleep(1)
            return self._send(400, register_page(err, name), html)
        failed_regs.pop(ip, None)
        add_tenant(name, port)
        self._redirect("/benvenuto", {"Set-Cookie": self._session_cookie(name)})

    # ---- profilo: cambio password e nome
    def _profile(self, t, body):
        if MODE != "cloud":
            return self._json({"error": "Il profilo c'è solo sul server su internet."}, 400)
        ip = self._client_ip()
        fails = failed_logins.get(ip, [0, 0.0])
        if fails[0] >= 5 and time.time() - fails[1] < 300:
            return self._json({"error": "Troppi tentativi, riprova tra 5 minuti."}, 429)
        cur = str(body.get("current", ""))
        if body.get("action") == "password":
            err = utenti.change_password(t.name, cur, str(body.get("new", "")), str(body.get("new2", "")))
        elif body.get("action") == "nome":
            old = t.name
            new = str(body.get("name", "")).strip().lower()
            err = utenti.rename(old, new, cur)
            if not err:
                TENANTS[new] = t
                t.name = new
                TENANTS.pop(old, None)
                print(f"[{new}] utente rinominato (prima: {old})", flush=True)
        else:
            err = "Richiesta non valida."
        if err and "password" in err.lower() and "sbagliata" in err:
            failed_logins[ip] = [fails[0] + 1, time.time()]
            time.sleep(1)
        if err:
            return self._json({"error": err}, 400)
        failed_logins.pop(ip, None)
        return self._json({"ok": True, "user": t.name})

    # ---- GET
    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        if path in PUBLIC_FILES:
            name, ctype = PUBLIC_FILES[path]
            with open(os.path.join(STATIC, name), "rb") as f:
                return self._send(200, f.read(), ctype, {"Cache-Control": "no-cache"} if name == "sw.js" else None)
        if path == "/login":
            return self._send(200, login_page(), "text/html; charset=utf-8")
        if path == "/registrati":
            if MODE != "cloud":
                return self._redirect("/")
            if not utenti.invite_code():
                return self._send(200, login_page("La registrazione è chiusa: chiedi a chi gestisce il server."),
                                  "text/html; charset=utf-8")
            return self._send(200, register_page(), "text/html; charset=utf-8")
        if path == "/logout":
            utenti.end_session(self._cookie("f1s") or "")
            return self._redirect("/login", {"Set-Cookie": "f1s=; Path=/; Max-Age=0"})
        if self._maybe_https_redirect(path):
            return
        t = self._tenant()
        if t is None:
            if path in ("/", "/index.html", "/analisi", "/analisi.html", "/benvenuto", "/classifica", "/admin",
                        "/gare", "/collegamento", "/campionato", "/assetti"):
                return self._redirect("/login")
            return self._json({"error": "login"}, 401)
        if path == "/benvenuto":
            return self._send(200, welcome_page(t), "text/html; charset=utf-8")
        if path in ("/", "/index.html"):
            self._static("index.html")
        elif path in ("/analisi", "/analisi.html"):
            self._static("analisi.html")
        elif path in ("/classifica", "/classifica.html"):
            self._static("classifica.html")
        elif path in ("/gare", "/gare.html"):
            self._static("gare.html")
        elif path in ("/collegamento", "/collegamento.html"):
            self._static("collegamento.html")
        elif path in ("/campionato", "/campionato.html"):
            self._static("campionato.html")
        elif path in ("/assetti", "/assetti.html"):
            self._static("assetti.html")
        elif path == "/api/campionato":
            self._json(championship(t))
        elif path == "/api/assetti":
            self._json({"setups": setups_view(t), "current": t.coach.track_name if t.coach.track_id is not None else None})
        elif path == "/api/gare":
            self._json(storico.gare(t.data_dir))
        elif path == "/api/collegamento":
            self._json(t.link_status())
        elif path == "/api/backup":
            zp = storico.make_backup(t.root)
            try:
                name = f"ingegnere-f1-backup-{t.name}-{time.strftime('%Y%m%d')}.zip"
                self._send_file(zp, name, "application/zip")
            finally:
                os.remove(zp)
        elif path in ("/admin", "/admin.html"):
            if MODE != "cloud" or not utenti.is_admin(t.name):
                return self._redirect("/")
            self._static("admin.html")
        elif path == "/api/classifica":
            self._json({"tracks": leaderboard(t), "me": t.name if MODE == "cloud" else None})
        elif path == "/api/admin":
            if MODE != "cloud" or not utenti.is_admin(t.name):
                return self._json({"error": "Solo per l'amministratore."}, 403)
            self._json(admin_view())
        elif path == "/api/curve":
            self._json(curve.names(params.get("track_id")))
        elif path == "/api/sessions":
            self._json(recorder.list_sessions(t.data_dir))
        elif path == "/api/lap":
            body = recorder.load_lap(params.get("s"), params.get("l", ""), t.data_dir)
            if body is None:
                self._json({}, 404)
            else:
                self._send(200, body, "application/json")
        elif path == "/api/friends":
            self._json(amici.list_laps(t, dict(TENANTS), params.get("track", ""), params.get("cat") or None))
        elif path == "/api/friend_lap":
            lap = amici.load(t, dict(TENANTS), params.get("id", ""))
            self._json(lap or {}, 200 if lap else 404)
        elif path == "/api/ideal":
            self._json(t.ideal_of(params.get("s", "")) or {})
        elif path == "/api/progress":
            self._json(t.progress(params.get("track", ""), params.get("cat") or None))
        elif path == "/api/rec":
            self._json(t.rec.status())
        elif path == "/api/rec_file":
            self._send_file(t.rec.path(params.get("name", "")), params.get("name", ""))
        elif path == "/api/outline":
            with t.lock:
                self._json({"v": t.coach.outline_v, "track": t.coach.track_name, "pts": t.coach.outline or []})
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

    def _restore(self, t):
        """Ripristino del backup: il file zip arriva così com'è (può pesare decine di MB)."""
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n or n > 500 * 1024 * 1024:
            if n:
                self.rfile.read(n)
            return self._json({"error": "File vuoto o troppo grande."}, 400)
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".zip")
        try:
            with os.fdopen(fd, "wb") as f:
                left = n
                while left:
                    chunk = self.rfile.read(min(left, 256 * 1024))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            res = storico.restore_backup(t.root, tmp)
        finally:
            os.remove(tmp)
        if res.get("ok"):
            t.reload_after_restore()
            print(f"[{t.name}] backup ripristinato: {res['files']} file", flush=True)
        return self._json(res, 200 if res.get("ok") else 400)

    # ---- POST
    def do_POST(self):
        if self.path == "/login":
            return self._login()
        if self.path == "/registrati":
            return self._register()
        t = self._tenant()
        if t is None:
            return self._json({"error": "login"}, 401)
        if self.path == "/api/restore":
            return self._restore(t)
        raw = self._body()
        if raw is None:
            return self._json({"error": "File troppo grande."}, 413)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if self.path == "/api/profile":
            return self._profile(t, body)
        if self.path == "/api/admin":
            if MODE != "cloud" or not utenti.is_admin(t.name):
                return self._json({"error": "Solo per l'amministratore."}, 403)
            res = admin_action(t, body)
            return self._json(res, 400 if res.get("error") else 200)
        if self.path == "/api/curve":
            try:
                tid, m = int(body.get("track_id")), float(body.get("m"))
            except (TypeError, ValueError):
                return self._json({"error": "Dati non validi."}, 400)
            return self._json(curve.set_name(tid, m, body.get("nome", "")))
        if self.path == "/settings":
            t.update_settings(body)
            return self._json(t.settings_view())
        if self.path == "/api/rec":
            act = body.get("action")
            if act == "start":
                return self._json(t.rec.start())
            if act == "stop":
                return self._json(t.rec.stop("fermata da te"))
            if act == "delete":
                return self._json(t.rec.delete(str(body.get("name", ""))))
            return self._json(t.rec.status())
        if self.path == "/tts":
            wav, why, used = t.tts(body)
            if wav is None:
                return self._json({"error": why}, 503)
            hdr = {"X-Voce": used}
            if why:
                hdr["X-Voce-Nota"] = urllib.parse.quote(why)
            return self._send(200, wav, "audio/wav", hdr)
        if self.path == "/api/friend_upload":
            out = amici.save_upload(t.friends_dir, body)
            return self._json(out, 400 if "error" in out else 200)
        if self.path == "/api/friend_delete":
            return self._json({"ok": amici.delete_upload(t.friends_dir, str(body.get("id", "")))})
        if self.path == "/api/use_ref":
            out = t.use_reference(body)
            return self._json(out, 400 if "error" in out else 200)
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
        # niente certificato: lo creo (serve openssl; su Termux: pkg install openssl-tool)
        import shutil
        import subprocess
        if not shutil.which("openssl"):
            print("HTTPS non attivo: manca openssl (su Termux: pkg install openssl-tool). "
                  "Sull'Android del server il microfono funziona lo stesso su http://127.0.0.1:8080", flush=True)
            return False
        try:
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                            "-out", cert, "-days", "3650", "-subj", "/CN=ingegnere-f1"],
                           check=True, capture_output=True, timeout=60)
            print("Creato il certificato per la pagina https.", flush=True)
        except (OSError, subprocess.SubprocessError) as e:
            print("HTTPS non attivo: non riesco a creare il certificato:", e, flush=True)
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
    ap.add_argument("--no-piper", action="store_true", help="non installare né usare la voce del server (Piper)")
    ARGS = a = ap.parse_args()
    if a.no_piper:
        PIPER.state = "non_supportato"
    else:
        # la prima volta scarica Piper e le voci (in sottofondo), poi prepara le frasi comuni
        # per i personaggi scelti da chi usa la voce del server
        PIPER.start(lambda: sorted({engineer_ai.load_config(t.config_path).get("voice_char", voci.DEFAULT)
                                    for t in TENANTS.values()}))
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
