"""Voce AI dell'ingegnere con Gemini TTS (facoltativa, usa la stessa chiave Gemini delle domande).

Rispetto alla voce del telefono è molto più naturale: tono, enfasi e pronuncia (nomi dei piloti,
parole inglesi come "safety car") li gestisce il modello. Ogni messaggio ha uno stile:
deciso e urgente per box/safety car/penalità, entusiasta per sorpassi e giri migliori, calmo per i consigli.

Se la voce AI non risponde in tempo, finisce la quota gratuita o manca la chiave, il telefono usa
la sua voce: l'ingegnere non resta mai muto. Gli audio già creati restano in cache (voce_cache/),
così le frasi che si ripetono ("Giro invalidato", "Dimmi") partono subito.
"""
import base64
import hashlib
import json
import os
import re
import struct
import time
import urllib.error
import urllib.request

# il primo ha il piano gratuito; i successivi sono le versioni più nuove, se il primo viene ritirato
MODELS = ("gemini-3.1-flash-tts-preview", "gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"

# voci tra cui scegliere (nomi delle voci Gemini) con una descrizione per la pagina impostazioni
VOICES = {
    "Fenrir": "energica", "Orus": "decisa", "Charon": "chiara e informativa", "Puck": "vivace",
    "Algenib": "profonda, un po' roca", "Kore": "decisa", "Aoede": "leggera", "Zephyr": "brillante",
}
DEFAULT_VOICE = "Fenrir"

STYLES = {
    "urgente": "da ingegnere di Formula 1 alla radio, deciso e urgente, ritmo veloce, enfasi sulle parole chiave",
    "entusiasta": "da ingegnere di Formula 1 alla radio, entusiasta e carico, con energia",
    "calmo": "da ingegnere di Formula 1 alla radio, calmo, chiaro e sicuro",
    "normale": "da ingegnere di Formula 1 alla radio, professionale, sicuro e coinvolto, non monotono",
}
EXCITED = re.compile(r"bel sorpasso|giro migliore|battuto|bandiera a scacchi|bella gara|guadagni|nuovo riferimento", re.I)
URGENT = re.compile(r"box, box|safety car|bandiera rossa|penalit|drive through|stop and go|squalific|"
                    r"dentro il secondo|pioggia|piovere|danneggiat|surriscald|ultimo giro|attenzione", re.I)


def style_for(text, kind, prio):
    if EXCITED.search(text):
        return "entusiasta"
    if prio >= 2 and URGENT.search(text) or kind in ("avviso",) and prio >= 2:
        return "urgente"
    if kind in ("consiglio", "assetto", "ingegnere"):
        return "calmo"
    return "normale"


class QuotaError(Exception):
    pass


def _wav(pcm, rate=24000):
    """Aggiunge l'intestazione WAV ai dati PCM 16 bit mono che manda il modello."""
    head = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm))
    return head + pcm


def _request(model, text, style, voice, key, timeout, persona=""):
    if persona:                         # personaggio scelto: come deve recitare, oltre al tono del messaggio
        style = f"{style}; il personaggio: {persona}"
    if model.startswith("gemini-3.8"):
        body = {"contents": [{"role": "user", "parts": [{"text": text, "speech_metadata": {"style": style}}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"voice": voice}}}}
    else:
        body = {"contents": [{"parts": [{"text": f"Leggi in italiano, con tono {style}: {text}"}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    req = urllib.request.Request(URL.format(model), data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.load(r)
    part = j["candidates"][0]["content"]["parts"][0]["inlineData"]
    data = base64.b64decode(part["data"])
    if data[:4] == b"RIFF":
        return data
    m = re.search(r"rate=(\d+)", part.get("mimeType", ""))
    return _wav(data, int(m.group(1)) if m else 24000)


class VoiceAI:
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        self.model_idx = 0
        self.pause_until = 0.0          # dopo un errore di quota aspetto prima di riprovare

    def synth(self, text, style_key, voice, key, timeout=8.0, persona=""):
        voice = voice if voice in VOICES or persona else DEFAULT_VOICE
        style = STYLES.get(style_key, STYLES["normale"])
        h = hashlib.sha1(f"{voice}|{style_key}|{persona}|{text}".encode()).hexdigest()
        path = os.path.join(self.cache_dir, h + ".wav")
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            pass
        if not key:
            raise QuotaError("manca la chiave Gemini")
        if time.time() < self.pause_until:
            raise QuotaError("quota gratuita finita, riprovo tra poco")
        last = None
        while self.model_idx < len(MODELS):
            try:
                wav = _request(MODELS[self.model_idx], text, style, voice, key, timeout, persona)
                break
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 404:                # modello ritirato: passo al successivo
                    self.model_idx += 1
                    continue
                if e.code == 429:
                    # può essere il limite al minuto o quello del giorno: Google lo scrive nel messaggio
                    try:
                        msg = json.loads(e.read().decode()).get("error", {}).get("message", "")
                    except Exception:
                        msg = ""
                    daily = bool(re.search(r"per ?day|daily|PerDay", msg, re.I))
                    self.pause_until = time.time() + (1800 if daily else 60)
                    print(f"Voce AI, limite di Google: {msg[:300]}", flush=True)
                    raise QuotaError("quota gratuita del giorno finita, per oggi parla il telefono" if daily else
                                     "limite di richieste al minuto, riprovo tra 1 minuto")
                raise
        else:
            raise last or RuntimeError("nessun modello disponibile")
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(path, "wb") as f:
                f.write(wav)
            self._trim()
        except OSError:
            pass
        return wav

    def _trim(self, keep=400):
        try:
            files = sorted((os.path.join(self.cache_dir, n) for n in os.listdir(self.cache_dir)),
                           key=os.path.getmtime)
            for p in files[:-keep]:
                os.remove(p)
        except OSError:
            pass
