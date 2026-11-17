"""Registrazione dei dati grezzi che manda la PS5, per rivedere (e correggere) una sessione vera.

Dettagli → ⏺ Registra: ogni pacchetto UDP viene salvato così com'è, con il momento in cui è
arrivato, in registrazioni/AAAAMMGG-HHMMSS.f1rec.gz (compresso). Poi lo scarichi dal telefono e
lo puoi mandare: con `python3 simulator.py --replay file.f1rec.gz` la sessione si rivede identica,
come se arrivasse dalla PS5.

Formato: gzip di record [float64 secondi dall'inizio][uint16 lunghezza][pacchetto].
Si ferma da solo dopo MAX_MIN minuti o MAX_MB di dati compressi, per non riempire il disco.
"""
import gzip
import os
import re
import struct
import threading
import time

MAX_MIN = 90
MAX_MB = 300
SAFE = re.compile(r"^[0-9]{8}-[0-9]{6}\.f1rec\.gz$")


class SessionRecorder:
    def __init__(self, folder):
        self.folder = folder
        self.lock = threading.Lock()
        self.f = None
        self.raw = None
        self.name = None
        self.t0 = 0.0
        self.packets = 0
        self.reason = ""

    @property
    def active(self):
        return self.f is not None

    def start(self):
        with self.lock:
            if self.f:
                return self.status()
            os.makedirs(self.folder, exist_ok=True)
            self.name = time.strftime("%Y%m%d-%H%M%S") + ".f1rec.gz"
            self.raw = open(os.path.join(self.folder, self.name), "wb")
            self.f = gzip.GzipFile(fileobj=self.raw, mode="wb", compresslevel=6)
            self.t0 = time.time()
            self.packets = 0
            self.reason = ""
            return self.status()

    def stop(self, reason=""):
        with self.lock:
            if self.f:
                try:
                    self.f.close()
                    self.raw.close()
                except OSError:
                    pass
                self.f = self.raw = None
                self.reason = reason
            return self.status()

    def add(self, data):
        if self.f is None:
            return
        stop = None
        with self.lock:
            if self.f is None:
                return
            t = time.time() - self.t0
            try:
                self.f.write(struct.pack("<dH", t, len(data)) + data)
            except OSError:
                stop = "errore di scrittura"
            self.packets += 1
            if t > MAX_MIN * 60:
                stop = f"fermata da sola dopo {MAX_MIN} minuti"
            elif self.packets % 500 == 0 and self.raw.tell() > MAX_MB * 1024 * 1024:
                stop = f"fermata da sola a {MAX_MB} MB"
        if stop:
            self.stop(stop)

    def status(self):
        size = 0
        if self.raw:
            try:
                size = self.raw.tell()
            except (OSError, ValueError):
                pass
        return {"active": self.f is not None, "name": self.name,
                "seconds": round(time.time() - self.t0) if self.f else 0,
                "packets": self.packets, "mb": round(size / 1048576, 1), "reason": self.reason,
                "files": self.list()}

    def list(self):
        out = []
        try:
            for n in sorted(os.listdir(self.folder), reverse=True):
                if SAFE.match(n):
                    p = os.path.join(self.folder, n)
                    out.append({"name": n, "mb": round(os.path.getsize(p) / 1048576, 1)})
        except OSError:
            pass
        return out[:20]

    def path(self, name):
        if not SAFE.match(name or "") or (self.f and name == self.name):
            return None
        p = os.path.join(self.folder, name)
        return p if os.path.isfile(p) else None

    def delete(self, name):
        p = self.path(name)
        if p:
            os.remove(p)
        return self.status()


def read(path):
    """Legge una registrazione: genera (secondi, pacchetto)."""
    with gzip.open(path, "rb") as f:
        while True:
            head = f.read(10)
            if len(head) < 10:
                return
            t, n = struct.unpack("<dH", head)
            data = f.read(n)
            if len(data) < n:
                return
            yield t, data
