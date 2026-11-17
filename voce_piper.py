"""Voce dell'ingegnere generata sul server con Piper: gratis, senza chiavi e senza limiti di utilizzo.

Piper è un programma di sintesi vocale che gira senza internet (https://github.com/rhasspy/piper).
Al primo avvio il server lo scarica da solo nella cartella piper/ (circa 25 MB) insieme alle voci
italiane (circa 60 MB l'una): non serve fare niente. Funziona su Linux a 64 bit (server Oracle,
Raspberry Pi, PC); su Termux (Android) e sul Mac non è disponibile e resta la voce del telefono.

Per non ricaricare il modello a ogni frase (sul server gratuito ci vorrebbe troppo) tengo acceso un
processo Piper per personaggio, che riceve le frasi una alla volta. Il tono si cambia "come un
nastro": scrivo nel file WAV una frequenza un po' più alta o più bassa, e compenso la velocità
con il parametro length_scale di Piper.
"""
import hashlib
import json
import os
import platform
import re
import select
import shutil
import struct
import subprocess
import tarfile
import threading
import time
import urllib.request

import voci

RELEASE = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_{arch}.tar.gz"
HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/"
# nome breve -> (file, indirizzo). "serena" è una voce della comunità (facoltativa: se non si
# scarica, i personaggi usano paola)
VOCI = {
    "paola": ("it_IT-paola-medium", HF + "paola/medium/it_IT-paola-medium.onnx"),
    "riccardo": ("it_IT-riccardo-x_low", HF + "riccardo/x_low/it_IT-riccardo-x_low.onnx"),
    "serena": ("it_IT-serena-medium", "https://huggingface.co/committa/it_IT-serena-medium/resolve/main/it_IT-serena-medium.onnx"),
}
OBBLIGATORIE = ("paola", "riccardo")


def _arch():
    if "ANDROID_ROOT" in os.environ or "com.termux" in os.environ.get("PREFIX", ""):
        return None
    if platform.system() != "Linux":
        return None
    m = platform.machine().lower()
    return {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(m)


def set_pitch(wav, factor):
    """Alza o abbassa il tono (e la velocità) cambiando la frequenza scritta nel file WAV."""
    if abs(factor - 1) < 0.005 or wav[:4] != b"RIFF":
        return wav
    i = 12
    while i + 8 <= len(wav):
        cid, size = wav[i:i + 4], struct.unpack("<I", wav[i + 4:i + 8])[0]
        if cid == b"fmt ":
            ch, rate = struct.unpack("<HI", wav[i + 10:i + 16])
            bits = struct.unpack("<H", wav[i + 22:i + 24])[0]
            new = int(round(rate * factor))
            return (wav[:i + 12] + struct.pack("<II", new, new * ch * bits // 8) + wav[i + 20:])
        i += 8 + size + (size & 1)
    return wav


def _pcm(wav):
    """(frequenza, canali, bit, dati PCM) di un file WAV."""
    i, fmt, data = 12, None, None
    while i + 8 <= len(wav):
        cid, size = wav[i:i + 4], struct.unpack("<I", wav[i + 4:i + 8])[0]
        if cid == b"fmt ":
            ch, rate = struct.unpack("<HI", wav[i + 10:i + 16])
            fmt = (rate, ch, struct.unpack("<H", wav[i + 22:i + 24])[0])
        elif cid == b"data":
            data = wav[i + 8:i + 8 + size]
        i += 8 + size + (size & 1)
    if not fmt or data is None:
        raise ValueError("WAV non valido")
    return fmt + (data,)


def join_wavs(wavs, pause_s=0.12):
    """Unisce le frasi (stessa voce) in un solo WAV, con una breve pausa fra una e l'altra."""
    if len(wavs) == 1:
        return wavs[0]
    parts = [_pcm(w) for w in wavs]
    rate, ch, bits = parts[0][:3]
    gap = b"\0" * (int(rate * pause_s) * ch * bits // 8)
    pcm = gap.join(p[3] for p in parts)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits) +
            b"data" + struct.pack("<I", len(pcm)) + pcm)


SENTENCES = re.compile(r"(?<=[.!?])\s+")

# frasi che l'ingegnere dice spesso, preparate in anticipo per la voce del server
# (la cache è per frase: "Sei P4. Leclerc è davanti a 1,2." usa la frase "Sei P4." già pronta)
COMMON = (["Giro invalidato, limiti della pista.", "Dimmi.", "Buona gara!", "Bella gara.", "Ottimo lavoro.",
           "Box, box!", "Box, box questo giro!", "Safety car in pista!", "Virtual safety car, rispetta il delta.",
           "Rispetta il delta e tieni calde le gomme.", "La safety car rientra a fine giro.",
           "Preparati alla ripartenza.", "Fine virtual safety car, via!",
           "Bandiera blu: fai passare chi arriva, resta sulla traiettoria e alza in rettilineo.",
           "Ha iniziato a piovere.", "Con le slick valuta le intermedie.", "Ultimo giro utile per la sosta.",
           "Ingegnere collegato.", "Ricarichiamo e riproviamo.", "Ora allunga.", "Scalda bene le gomme.",
           "Restagli attaccato.", "Grande, sei P1!"]
          + [f"Sei P{p}." for p in range(1, 21)] + [f"P{p}." for p in range(1, 21)]
          + [f"Bandiera a scacchi! Hai chiuso P{p}." for p in range(1, 11)])


class _Proc:
    """Un processo Piper acceso con un modello e una velocità: riceve le frasi una alla volta (JSON)."""

    def __init__(self, exe, model, length_scale, outdir):
        self.outdir = outdir
        env = dict(os.environ, LD_LIBRARY_PATH=os.path.dirname(exe))
        self.p = subprocess.Popen(
            [exe, "--model", model, "--length_scale", f"{length_scale:.3f}", "--sentence_silence", "0.12",
             "--json-input", "--output_dir", outdir],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
            cwd=os.path.dirname(exe))
        self.lock = threading.Lock()
        self.used = time.time()

    def alive(self):
        return self.p.poll() is None

    def say(self, text, timeout):
        with self.lock:
            self.used = time.time()
            out = os.path.join(self.outdir, f"{time.time_ns()}.wav")
            self.p.stdin.write((json.dumps({"text": text, "output_file": out}, ensure_ascii=False) + "\n").encode())
            self.p.stdin.flush()
            end = time.time() + timeout
            line = b""
            while not line.endswith(b"\n"):
                left = end - time.time()
                if left <= 0 or not select.select([self.p.stdout], [], [], left)[0]:
                    raise TimeoutError("Piper troppo lento")
                ch = os.read(self.p.stdout.fileno(), 4096)
                if not ch:
                    raise RuntimeError("Piper si è chiuso")
                line += ch
            path = line.decode(errors="replace").strip().splitlines()[-1].strip() or out
            for cand in (path, out):
                if os.path.exists(cand):
                    with open(cand, "rb") as f:
                        data = f.read()
                    os.remove(cand)
                    return data
            raise RuntimeError("Piper non ha creato l'audio")

    def close(self):
        try:
            self.p.kill()
        except OSError:
            pass


class Piper:
    """Unico per tutto il server (le voci e i processi sono condivisi fra gli utenti)."""

    def __init__(self, root):
        self.root = root
        self.dir_voci = os.path.join(root, "voci")
        self.cache = os.path.join(root, "cache")
        self.tmp = os.path.join(root, "tmp")
        self.state, self.detail = "assente", ""
        self.procs, self.lock = {}, threading.Lock()
        self.oneshot = False             # se la modalità JSON non funziona, un processo per frase
        self.exe = os.environ.get("PIPER_BIN") or os.path.join(root, "piper", "piper")

    # ---- stato e installazione
    def voices(self):
        return {n for n, (f, _) in VOCI.items() if os.path.exists(os.path.join(self.dir_voci, f + ".onnx.json"))}

    def ready(self):
        return self.state == "pronta"

    def status(self):
        txt = {"pronta": "pronta", "scarico": "la sto installando sul server" + (f" ({self.detail})" if self.detail else ""),
               "non_supportato": "non disponibile su questo dispositivo (serve Linux a 64 bit: server Oracle, Raspberry Pi o PC)",
               "errore": "non funziona: " + self.detail, "assente": "non installata"}[self.state]
        return {"state": self.state, "text": txt, "voices": sorted(self.voices())}

    def start(self, warm_chars=None):
        def loop():
            self._install()
            if self.ready() and warm_chars:
                self.warm(warm_chars())
            while self.state == "errore":     # rete assente o download interrotto: riprovo ogni mezz'ora
                time.sleep(1800)
                self._install()
        threading.Thread(target=loop, daemon=True).start()

    def _download(self, url, dest):
        tmp = dest + ".part"
        req = urllib.request.Request(url, headers={"User-Agent": "ingegnere-f1"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 256 * 1024)
        os.replace(tmp, dest)

    def _install(self):
        try:
            if not os.path.exists(self.exe):
                arch = _arch()
                if not arch:
                    self.state = "non_supportato"
                    return
                self.state, self.detail = "scarico", "programma"
                os.makedirs(self.root, exist_ok=True)
                tgz = os.path.join(self.root, "piper.tar.gz")
                self._download(RELEASE.format(arch=arch), tgz)
                with tarfile.open(tgz) as tf:
                    for m in tf.getmembers():       # niente percorsi strani fuori dalla cartella
                        if m.name.startswith(("/", "..")) or ".." in m.name.split("/"):
                            raise RuntimeError("archivio di Piper non valido")
                    tf.extractall(self.root)
                os.remove(tgz)
                os.chmod(self.exe, 0o755)
            os.makedirs(self.dir_voci, exist_ok=True)
            for name, (fname, url) in VOCI.items():
                onnx = os.path.join(self.dir_voci, fname + ".onnx")
                if os.path.exists(onnx + ".json"):
                    continue
                self.state, self.detail = "scarico", f"voce {name}"
                try:
                    self._download(url, onnx)
                    self._download(url + ".json", onnx + ".json")
                except Exception as e:
                    if name in OBBLIGATORIE:
                        raise
                    print(f"Piper: voce facoltativa {name} non scaricata ({e})", flush=True)
            self.state, self.detail = "scarico", "prova"
            self.synth("Prova.", voci.DEFAULT, "normale", timeout=60)
            self.state, self.detail = "pronta", ""
            print("Voce del server (Piper) pronta:", ", ".join(sorted(self.voices())), flush=True)
        except Exception as e:
            self.state, self.detail = "errore", str(e)[:200]
            print("Voce del server (Piper) non disponibile:", e, flush=True)

    # ---- sintesi
    def _model_for(self, char):
        have = self.voices()
        for v in char["piper"]["voce"] + list(OBBLIGATORIE):
            if v in have:
                return v, os.path.join(self.dir_voci, VOCI[v][0] + ".onnx")
        raise RuntimeError("nessuna voce installata")

    def synth(self, text, char_id, style="normale", timeout=8.0):
        """Il messaggio, frase per frase: ogni frase resta salvata, così quelle che tornano
        ("Box, box!", "Sei P4.") sono pronte subito anche dentro messaggi nuovi."""
        char = voci.get(char_id)
        pv = char["piper"]
        # il file WAV "accelerato" del fattore f alza tono e velocità di f; length_scale = tono/velocità
        # compensa, così il personaggio ha il suo tono e la sua velocità. Lo stile (urgente, calmo…)
        # sposta tono e velocità insieme, di poco, senza cambiare processo.
        factor = pv["tono"] * voci.STILE_PIPER.get(style, 1.0)
        length = pv["tono"] / pv["velocita"]
        voice, model = self._model_for(char)
        end = time.time() + timeout
        parts = [p for p in SENTENCES.split(" ".join(text.split())) if p.strip()] or [text]
        wavs = [self._sentence(voice, model, length, p, max(1.0, end - time.time())) for p in parts]
        return set_pitch(join_wavs(wavs), factor)

    def cached(self, char_id, sentence):
        voice, _ = self._model_for(voci.get(char_id))
        pv = voci.get(char_id)["piper"]
        return os.path.exists(self._cache_path(voice, pv["tono"] / pv["velocita"], sentence))

    def _cache_path(self, voice, length, sentence):
        h = hashlib.sha1(f"{voice}|{length:.3f}|{sentence}".encode()).hexdigest()
        return os.path.join(self.cache, h + ".wav")

    def _sentence(self, voice, model, length, sentence, timeout):
        path = self._cache_path(voice, length, sentence)
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            pass
        os.makedirs(self.tmp, exist_ok=True)
        wav = self._run(model, length, sentence, timeout)
        try:
            os.makedirs(self.cache, exist_ok=True)
            with open(path, "wb") as f:
                f.write(wav)
            self._trim()
        except OSError:
            pass
        return wav

    def warm(self, char_ids):
        """Prepara in sottofondo le frasi comuni per i personaggi scelti (una alla volta, piano)."""
        def run():
            for cid in char_ids:
                for s in COMMON:
                    if not self.ready():
                        return
                    try:
                        if not self.cached(cid, s):
                            self.synth(s, cid, timeout=30)
                            time.sleep(0.3)          # lascio spazio alle frasi vere della gara
                    except Exception as e:
                        print("Piper, preparazione frasi interrotta:", e, flush=True)
                        return
            print("Voce del server: frasi comuni pronte per", ", ".join(char_ids), flush=True)
        threading.Thread(target=run, daemon=True).start()

    def _run(self, model, length, text, timeout):
        text = " ".join(text.split())
        if not self.oneshot:
            key = (model, round(length, 3))
            with self.lock:
                pr = self.procs.get(key)
                if pr is None or not pr.alive():
                    # sul server gratuito la memoria è poca: al massimo 2 processi accesi
                    while len(self.procs) >= 2:
                        old = min(self.procs, key=lambda k: self.procs[k].used)
                        self.procs.pop(old).close()
                    pr = self.procs[key] = _Proc(self.exe, model, length, self.tmp)
            try:
                return pr.say(text, timeout)
            except TimeoutError:
                raise
            except Exception as e:
                print("Piper, modalità continua non disponibile, uso un processo per frase:", e, flush=True)
                with self.lock:
                    self.procs.pop(key, None)
                pr.close()
                self.oneshot = True
        out = os.path.join(self.tmp, f"{time.time_ns()}.wav")
        env = dict(os.environ, LD_LIBRARY_PATH=os.path.dirname(self.exe))
        subprocess.run([self.exe, "--model", model, "--length_scale", f"{length:.3f}", "--sentence_silence", "0.12",
                        "--output_file", out], input=text.encode(), stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=timeout, check=True, env=env, cwd=os.path.dirname(self.exe))
        with open(out, "rb") as f:
            data = f.read()
        os.remove(out)
        return data

    def _trim(self, keep=3000):
        try:
            files = sorted((os.path.join(self.cache, n) for n in os.listdir(self.cache)), key=os.path.getmtime)
            for p in files[:-keep]:
                os.remove(p)
        except OSError:
            pass
