"""L'ingegnere su Discord: la voce arriva nelle cuffie della PS5.

La PS5 entra da sola nei canali vocali di Discord. Un bot (uno per utente, con il suo token) sta nel
canale e dice i messaggi dell'ingegnere; ascolta solo il suo pilota e, quando sente "Radio, …", gira la
domanda all'ingegnere come il pulsante Parla dell'app.

Pezzi:
- Runtime (uno per tutto il server): scarica Node.js, installa le librerie Discord con npm e il modello
  Whisper per capire la voce. Tutto nella cartella del programma, senza permessi di amministratore.
- DiscordLink (uno per utente): avvia il bot (discord/bot.mjs), gli passa l'audio, riceve le domande.

Il bot e il server parlano con righe JSON su stdin/stdout.
"""
import array
import json
import os
import platform
import queue
import re
import shutil
import struct
import subprocess
import tarfile
import threading
import time
import urllib.request
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
NODE_MAJOR = 22
STT_PKG = "sherpa-onnx-node@^1.12.0"     # riconoscimento vocale: installato a parte, se non va resta solo la voce in uscita


def _npm_error(text):
    """Le righe utili di un errore di npm (senza avvisi di aggiornamento né il percorso del log)."""
    lines = [l for l in text.splitlines() if l.strip() and "npm notice" not in l and "complete log" not in l]
    err = [l for l in lines if "npm error" in l or "ERR" in l] or lines
    return " | ".join(l.replace("npm error", "").strip() for l in err[-6:])[-500:]
WHISPER_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-base.tar.bz2"
MODES = ("telefono", "discord", "entrambi")    # dove esce la voce dell'ingegnere


def _arch():
    m = platform.machine().lower()
    if platform.system() != "Linux" or "ANDROID_ROOT" in os.environ:
        return None
    return {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(m)


def _download(url, dest, timeout=120):
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "ingegnere-f1"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 256 * 1024)
    os.replace(tmp, dest)


def _safe_extract(tf, dest):
    for m in tf.getmembers():
        if m.name.startswith(("/", "..")) or ".." in m.name.split("/") or m.issym() and m.linkname.startswith("/"):
            raise RuntimeError("archivio non valido")
    tf.extractall(dest)


# ------------------------------------------------------------------ audio
def wav_to_discord(wav):
    """WAV (mono o stereo, 16 bit, qualsiasi frequenza) → PCM 48 kHz stereo 16 bit per Discord."""
    if wav[:4] != b"RIFF":
        raise ValueError("audio non WAV")
    i, rate, ch, bits, data = 12, None, 1, 16, None
    while i + 8 <= len(wav):
        cid, size = wav[i:i + 4], struct.unpack_from("<I", wav, i + 4)[0]
        if cid == b"fmt ":
            _, ch, rate, _, _, bits = struct.unpack_from("<HHIIHH", wav, i + 8)
        elif cid == b"data":
            data = wav[i + 8:i + 8 + size]
            break
        i += 8 + size + (size & 1)
    if not rate or data is None or bits != 16:
        raise ValueError("WAV non supportato")
    src = array.array("h")
    src.frombytes(data[:len(data) - len(data) % (2 * ch)])
    if ch == 2:                                        # in mono per il ricampionamento
        src = array.array("h", ((src[k] + src[k + 1]) // 2 for k in range(0, len(src), 2)))
    n = len(src)
    if not n:
        return b""
    m = int(n * 48000 / rate)
    step = rate / 48000
    out = array.array("h", bytes(4 * m))
    last = n - 1
    for j in range(m):
        x = j * step
        k = int(x)
        if k >= last:
            v = src[last]
        else:
            fr = x - k
            v = int(src[k] + (src[k + 1] - src[k]) * fr)
        out[2 * j] = v
        out[2 * j + 1] = v
    # 120 ms di silenzio in fondo: l'ultima sillaba non viene tagliata
    return out.tobytes() + bytes(4 * 5760)


# ------------------------------------------------------------------ installazione (una per server)
class Runtime:
    def __init__(self, root):
        self.root = root
        self.node_dir = os.path.join(root, "node")
        self.models = os.path.join(root, "whisper")
        self.state, self.detail = "spento", ""
        self.whisper = None
        self.lock = threading.Lock()
        self._thread = None
        self.on_whisper = []          # i bot già avviati ripartono quando arriva il riconoscimento vocale

    @property
    def node(self):
        return os.path.join(self.node_dir, "bin", "node")

    def ready(self):
        return self.state == "pronto"

    def status(self):
        txt = {"spento": "non installato", "scarico": f"installo ({self.detail})…", "pronto": "pronto",
               "errore": f"errore: {self.detail}", "non_supportato": "non disponibile su questo computer"}
        return {"state": self.state, "text": txt.get(self.state, self.state), "voice_in": bool(self.whisper)}

    def ensure(self):
        """Installa in sottofondo la prima volta che qualcuno attiva Discord."""
        with self.lock:
            if self.state in ("pronto", "scarico", "non_supportato") or (self._thread and self._thread.is_alive()):
                return
            self.state, self.detail = "scarico", "inizio"
            self._thread = threading.Thread(target=self._install, daemon=True)
            self._thread.start()

    def _install(self):
        try:
            arch = _arch()
            if not arch:
                self.state = "non_supportato"
                return
            os.makedirs(self.root, exist_ok=True)
            if not os.path.exists(self.node):
                self.detail = "Node.js"
                with urllib.request.urlopen(urllib.request.Request(
                        "https://nodejs.org/dist/index.json", headers={"User-Agent": "ingegnere-f1"}), timeout=60) as r:
                    rel = json.load(r)
                ver = next(x["version"] for x in rel if x["version"].startswith(f"v{NODE_MAJOR}.") and x.get("lts"))
                name = f"node-{ver}-linux-{arch}"
                txz = os.path.join(self.root, "node.tar.xz")
                _download(f"https://nodejs.org/dist/{ver}/{name}.tar.xz", txz, 300)
                with tarfile.open(txz) as tf:
                    _safe_extract(tf, self.root)
                os.remove(txz)
                shutil.rmtree(self.node_dir, ignore_errors=True)
                os.replace(os.path.join(self.root, name), self.node_dir)
            # librerie Discord (npm): rifaccio l'installazione se package.json è cambiato
            pkg_src = os.path.join(HERE, "discord", "package.json")
            pkg_dst = os.path.join(self.root, "package.json")
            if not os.path.exists(os.path.join(self.root, "node_modules", "@discordjs", "voice")) or \
                    open(pkg_src).read() != (open(pkg_dst).read() if os.path.exists(pkg_dst) else ""):
                self.detail = "librerie Discord"
                shutil.copy(pkg_src, pkg_dst)
                r = self._npm(["install"])
                if r.returncode != 0:
                    print("Discord: npm install non riuscito:\n" + r.stdout[-3000:] + r.stderr[-3000:], flush=True)
                    raise RuntimeError("npm: " + _npm_error(r.stderr + "\n" + r.stdout))
            self.state = "pronto"           # la voce in uscita funziona già: Whisper è un di più
            self._install_whisper()
        except Exception as e:
            self.state, self.detail = "errore", str(e)[:300]
            print("Discord: installazione non riuscita:", e, flush=True)

    def _npm(self, args):
        env = dict(os.environ, PATH=os.path.join(self.node_dir, "bin") + os.pathsep + os.environ.get("PATH", ""),
                   npm_config_cache=os.path.join(self.root, ".npm"), npm_config_update_notifier="false",
                   HOME=self.root)
        return subprocess.run([os.path.join(self.node_dir, "bin", "npm")] + args +
                              ["--omit=dev", "--no-audit", "--no-fund", "--no-save", "--loglevel=error",
                               # prism-media chiede versioni vecchie di opusscript tra le dipendenze "facoltative"
                               "--legacy-peer-deps"],
                              cwd=self.root, env=env, capture_output=True, text=True, timeout=900)

    def lib_path(self):
        """sherpa-onnx su Linux vuole la cartella delle sue librerie in LD_LIBRARY_PATH."""
        nm = os.path.join(self.root, "node_modules")
        dirs = [os.path.join(nm, d) for d in os.listdir(nm)] if os.path.isdir(nm) else []
        return os.pathsep.join(d for d in dirs if os.path.basename(d).startswith("sherpa-onnx-linux"))

    def _install_whisper(self):
        try:
            if not os.path.isdir(os.path.join(self.root, "node_modules", "sherpa-onnx-node")):
                self.detail = "riconoscimento vocale (libreria)"
                r = self._npm(["install", STT_PKG])
                if r.returncode != 0:
                    print("Discord: sherpa-onnx non installato:\n" + (r.stderr + r.stdout)[-2000:], flush=True)
                    raise RuntimeError(_npm_error(r.stderr + "\n" + r.stdout))
            if not self._find_whisper():
                self.detail = "riconoscimento vocale"
                tb = os.path.join(self.root, "whisper.tar.bz2")
                _download(WHISPER_URL, tb, 600)
                os.makedirs(self.models, exist_ok=True)
                with tarfile.open(tb) as tf:
                    _safe_extract(tf, self.models)
                os.remove(tb)
            self.whisper = self._find_whisper()
            print("Discord: riconoscimento vocale", "pronto" if self.whisper else "non trovato", flush=True)
            for cb in list(self.on_whisper):
                try:
                    cb()
                except Exception as e:
                    print("Discord:", e, flush=True)
        except Exception as e:
            print("Discord: riconoscimento vocale non installato:", e, flush=True)

    def _find_whisper(self):
        if not os.path.isdir(self.models):
            return None
        files = []
        for d, _, fs in os.walk(self.models):
            files += [os.path.join(d, f) for f in fs]
        pick = lambda key: (sorted([f for f in files if key in os.path.basename(f) and f.endswith(".onnx")],
                                   key=lambda f: ("int8" not in f, f)) or [None])[0]
        tokens = next((f for f in files if f.endswith("tokens.txt")), None)
        enc, dec = pick("encoder"), pick("decoder")
        return {"encoder": enc, "decoder": dec, "tokens": tokens} if enc and dec and tokens else None


# ------------------------------------------------------------------ il bot di un utente
WAKE = re.compile(r"^\W*(?:ehi\s+|ok\s+|hey\s+)?(radio|box radio|ingegnere)\b[\s,.:!?-]*", re.I)


def _norm(t):
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")


class DiscordLink:
    def __init__(self, tenant, runtime):
        self.t, self.rt = tenant, runtime
        self.proc = None
        self.cfg_key = None
        self.state = "spento"         # spento, avvio, connesso, nel_canale, errore
        self.detail = ""
        self.bot_name = self.channel = self.guild = self.bot_id = None
        self.voice_in = None          # None = non so ancora, True/False
        self.heard = []               # ultime frasi sentite (per la pagina di diagnosi)
        self.out_q = queue.Queue(maxsize=20)
        self.lock = threading.Lock()
        runtime.on_whisper.append(lambda: self.configure(self.t.load_cfg()))
        threading.Thread(target=self._speaker, daemon=True).start()

    # ---- configurazione
    def configure(self, cfg):
        """Avvia, riavvia o ferma il bot secondo le impostazioni dell'utente."""
        token = (cfg.get("discord_token") or "").strip()
        channel = (cfg.get("discord_channel") or "").strip()
        mode = cfg.get("audio_out") if cfg.get("audio_out") in MODES else "telefono"
        want = bool(token and channel) and mode != "telefono"
        key = (token, channel, (cfg.get("discord_user") or "").strip(), bool(self.rt.whisper)) if want else None
        if key == self.cfg_key and (not want or self.proc and self.proc.poll() is None):
            return
        self.stop()
        self.cfg_key = key
        if not want:
            self.state, self.detail = "spento", ""
            return
        self.rt.ensure()
        threading.Thread(target=self._start_when_ready, args=(key,), daemon=True).start()

    def _start_when_ready(self, key):
        self.state, self.detail = "avvio", "installo Discord sul server"
        while self.cfg_key == key and not self.rt.ready():
            if self.rt.state in ("errore", "non_supportato"):
                self.state, self.detail = "errore", self.rt.status()["text"]
                return
            time.sleep(2)
        if self.cfg_key != key:
            return
        # Whisper si installa dopo Node: se arriva mentre il bot gira, il bot ripartirà con la voce in ingresso
        token, channel, user, _ = key
        for f in ("bot.mjs", "stt-worker.mjs"):
            shutil.copy(os.path.join(HERE, "discord", f), os.path.join(self.rt.root, f))
        bot_cfg = {"token": token, "channel": channel, "user": user, "whisper": self.rt.whisper}
        env = dict(os.environ, BOT_CFG=json.dumps(bot_cfg), HOME=self.rt.root)
        lp = self.rt.lib_path()
        if lp:
            env["LD_LIBRARY_PATH"] = lp + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
        self.state, self.detail = "avvio", "collego il bot"
        try:
            self.proc = subprocess.Popen([self.rt.node, os.path.join(self.rt.root, "bot.mjs")], cwd=self.rt.root,
                                         env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1)
        except Exception as e:
            self.state, self.detail = "errore", str(e)
            return
        threading.Thread(target=self._reader, args=(self.proc, key), daemon=True).start()

    def stop(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.stdin.write('{"op":"quit"}\n')
                p.stdin.flush()
                p.wait(3)
            except Exception:
                p.kill()
        self.bot_name = self.channel = None

    # ---- eventi dal bot
    def _reader(self, p, key):
        for line in p.stdout:
            line = line.strip()
            try:
                ev = json.loads(line)
            except ValueError:
                if line:
                    print(f"[{self.t.name}] discord: {line[:200]}", flush=True)
                continue
            self._event(ev)
        if self.cfg_key == key and self.proc is p:
            # il bot si è fermato: riprovo tra un po' (a meno che il token sia sbagliato)
            self.proc = None
            if self.state != "errore":
                self.state, self.detail = "avvio", "il bot si è fermato, riprovo"
                time.sleep(10)
                if self.cfg_key == key:
                    self._start_when_ready(key)

    def _event(self, ev):
        e = ev.get("ev")
        if e == "ready":
            self.state, self.bot_name, self.detail = "connesso", ev.get("name"), "entro nel canale"
            self.bot_id = ev.get("id")
        elif e == "joined":
            self.state, self.channel, self.guild, self.detail = "nel_canale", ev.get("channel"), ev.get("guild"), ""
            print(f"[{self.t.name}] discord: nel canale {self.channel} ({self.guild})", flush=True)
        elif e == "left":
            self.state, self.detail = "connesso", "uscito dal canale, rientro"
        elif e == "error":
            self.state, self.detail = "errore", ev.get("msg", "")
            print(f"[{self.t.name}] discord: {self.detail}", flush=True)
        elif e == "stt":
            self.voice_in = bool(ev.get("ok"))
            if not ev.get("ok"):
                print(f"[{self.t.name}] discord: niente voce in ingresso ({ev.get('msg')})", flush=True)
        elif e == "heard":
            self._heard(ev.get("text", ""), ev.get("ms"))
        elif e == "log":
            print(f"[{self.t.name}] discord: {ev.get('msg')}", flush=True)

    def _heard(self, text, ms=None):
        """Il pilota ha parlato su Discord: rispondo solo se la frase comincia con "Radio"."""
        self.heard = (self.heard + [{"t": time.time(), "text": text, "ms": ms}])[-8:]
        m = WAKE.match(_norm(text))
        if not m:
            return                                  # sta parlando con l'amico, non con me
        q = _norm(text)[m.end():].strip(" ,.!?")
        self.t.emit("pilota", f"🎙️ {text.strip()}", False, 1)
        if not q:
            self.t.emit("ingegnere", "Dimmi.", True, 2)
            return
        threading.Thread(target=self.t.ask, args=(q,), daemon=True).start()

    # ---- voce in uscita
    def active(self):
        return self.state == "nel_canale" and self.proc is not None and self.proc.poll() is None

    def say(self, text, kind, prio):
        if not self.active():
            return
        try:
            self.out_q.put_nowait((text, kind, prio))
        except queue.Full:
            pass

    def _speaker(self):
        n = 0
        while True:
            text, kind, prio = self.out_q.get()
            try:
                wav, why, _ = self.t.tts({"text": text, "kind": kind, "prio": prio, "fallback": True})
                if not wav:
                    print(f"[{self.t.name}] discord: niente audio ({why})", flush=True)
                    continue
                pcm = wav_to_discord(wav)
                n += 1
                path = os.path.join(self.rt.root, "tmp", f"{self.t.name}-{os.getpid()}-{n}.pcm")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(pcm)
                p = self.proc
                if p and p.poll() is None:
                    p.stdin.write(json.dumps({"op": "play", "file": path, "urgent": prio >= 2}) + "\n")
                    p.stdin.flush()
            except Exception as e:
                print(f"[{self.t.name}] discord: audio non inviato ({e})", flush=True)

    def status(self):
        txt = {"spento": "spento", "avvio": self.detail or "avvio…", "connesso": f"bot {self.bot_name} collegato, {self.detail}",
               "nel_canale": f"nel canale «{self.channel}» di {self.guild}", "errore": self.detail}
        return {"state": self.state, "text": txt.get(self.state, self.state), "bot": self.bot_name,
                "channel": self.channel, "guild": self.guild, "voice_in": self.voice_in,
                "runtime": self.rt.status(), "heard": self.heard[-5:],
                # link per invitare il bot nel tuo server: vedere i canali, entrare, parlare, attività vocale
                "invite": f"https://discord.com/oauth2/authorize?client_id={self.bot_id}&scope=bot&permissions=36701184"
                if self.bot_id else None}
