"""Ingegnere AI per le domande libere.

Servizi gratuiti, provati in fila finché uno risponde: Gemini → Groq → Cerebras → OpenRouter → Mistral.
Quando uno finisce le domande gratuite (errore 429) viene saltato per un po' e si passa al successivo.
Claude (a pagamento) è facoltativo. Senza nessuna chiave restano le risposte a regole.
Le chiavi si inseriscono dalla dashboard (🧠 Domande libere) e vengono salvate in config.json.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

SYSTEM = (
    "Sei l'ingegnere di pista di un pilota che corre su EA F1 con la PS5, e gli parli via radio. "
    "Ricevi un JSON con la telemetria riassunta (giri, staccate, gomme, carburante, meteo, distacchi, "
    "stima di rientro dal pit, piloti in gara). Rispondi in italiano: massimo 3 frasi brevi, niente elenchi, "
    "niente simboli, numeri scritti in modo che si possano leggere ad alta voce. "
    "In gara dai indicazioni di strategia (se fermarsi, con che gomma, dove rientra), meteo, "
    "distacchi e avversari; in prova e qualifica indicazioni di guida e assetto. "
    "Basati solo sui dati: se mancano, dillo. "
    "Se il pilota ti chiede di FARE qualcosa che corrisponde a uno dei comandi qui sotto, la prima riga della "
    "risposta deve essere esattamente «COMANDO: » seguito dal comando, con i nomi presi dai dati "
    "(per esempio «quello della Ferrari» diventa il cognome del pilota Ferrari più vicino), poi niente altro. "
    "Comandi: segui <cognome> | segui la P<numero> | basta seguire | silenzio | parla di nuovo | parla di più | "
    "parla di meno | ripeti | segnala | lavoriamo sulla frenata | lavoriamo sul centro curva | "
    "lavoriamo sull'uscita | lavoriamo su tutto | chiedi a <amico> <domanda> | di' a <amico> che <messaggio> | "
    "confermo (risposta sì alla chiamata box) | allunghiamo (risposta no alla chiamata box). "
    "Se è una domanda e non un'azione, rispondi normalmente senza la riga COMANDO."
)

# prezzi in dollari per milione di token (ingresso, uscita): per il conto delle spese nell'app
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}
CLAUDE_DEFAULT = "claude-haiku-5-5"          # il più veloce: alla radio conta il secondo
GEMINI_MODELS = ("gemini-flash-lite-latest", "gemini-flash-latest")   # lite: più domande gratis al giorno
TIMEOUT = 7                                  # per servizio; oltre, alla radio è inutile
DEADLINE = 14                                # tempo massimo per tutta la catena

# Servizi gratuiti con API compatibile OpenAI: (nome, chiave in config, indirizzo, modelli in ordine di preferenza).
# Se nessun modello della lista esiste più, si sceglie da solo dall'elenco dei modelli del servizio.
FREE = (
    ("Groq", "groq_key", "https://api.groq.com/openai/v1",
     ("llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.1-8b-instant")),
    ("Cerebras", "cerebras_key", "https://api.cerebras.ai/v1",
     ("gpt-oss-120b", "llama-3.3-70b", "qwen-3-32b", "llama3.1-8b")),
    ("OpenRouter", "openrouter_key", "https://openrouter.ai/api/v1",
     ("meta-llama/llama-3.3-70b-instruct:free", "openai/gpt-oss-120b:free", "google/gemma-3-27b-it:free",
      "mistralai/mistral-small-3.2-24b-instruct:free", "deepseek/deepseek-chat-v3-0324:free")),
    ("Mistral", "mistral_key", "https://api.mistral.ai/v1",
     ("mistral-small-latest", "mistral-medium-latest", "open-mistral-nemo")),
)
FREE_BY_NAME = {f[0]: f for f in FREE}
KEYS = {"Claude": "anthropic_key", "Gemini": "gemini_key", **{f[0]: f[1] for f in FREE}}
ORDER_FREE = ["Gemini"] + [f[0] for f in FREE]
ALL = ORDER_FREE + ["Claude"]
COOLDOWN_429 = 120                           # secondi in cui salto un servizio che ha finito le domande
UA = "ingegnere-f1/1.0"                      # Cloudflare (Groq, OpenRouter) blocca lo user-agent di Python


def load_config(path=CONFIG_PATH):
    cfg = {}
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        pass
    cfg.setdefault("gemini_key", os.environ.get("GEMINI_API_KEY", ""))
    cfg.setdefault("anthropic_key", os.environ.get("ANTHROPIC_API_KEY", ""))
    for name, key, _, _ in FREE:
        cfg.setdefault(key, os.environ.get(name.upper() + "_API_KEY", ""))
    cfg.setdefault("gemini_model", GEMINI_MODELS[0])
    # le vecchie versioni salvavano Sonnet come predefinito: passo a Haiku (più veloce e costa 20 volte meno)
    if cfg.get("claude_model") in (None, "", "claude-sonnet-5-5") and not cfg.get("claude_model_scelto"):
        cfg["claude_model"] = CLAUDE_DEFAULT
    cfg.setdefault("ai_provider", "auto")     # auto: Claude se c'è la chiave, poi i gratuiti; gemini: gratuiti prima
    cfg.setdefault("pit_loss", 22.0)
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)


_cool = {}          # servizio -> istante fino a cui lo salto (domande finite)
_model_cache = {}   # servizio -> modello che funziona


def providers(cfg=None):
    """In che ordine provare i servizi che hanno una chiave."""
    cfg = cfg or load_config()
    pref = cfg.get("ai_provider", "auto")
    if pref == "off":
        return []
    order = (["Claude"] + ORDER_FREE) if pref == "auto" else (ORDER_FREE + ["Claude"])
    return [p for p in order if cfg.get(KEYS[p])]


def provider(cfg=None):
    p = providers(cfg)
    return p[0] if p else None


def _post(url, body, headers, timeout=TIMEOUT):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "user-agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _ask_gemini(cfg, prompt):
    first = cfg.get("gemini_model") or GEMINI_MODELS[0]
    models = [first] + [m for m in GEMINI_MODELS if m != first]
    last = None
    for model in models:
        try:
            out = _post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                {"system_instruction": {"parts": [{"text": SYSTEM}]},
                 "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                 "generationConfig": {"maxOutputTokens": 400, "temperature": 0.4}},
                {"x-goog-api-key": cfg["gemini_key"]})
            parts = out["candidates"][0]["content"]["parts"]
            u = out.get("usageMetadata") or {}
            return ("".join(p.get("text", "") for p in parts).strip(), model,
                    u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0))
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (404, 429):   # modello non disponibile o domande finite: provo il successivo
                continue
            raise
    raise last


def _ask_claude(cfg, prompt):
    model = cfg.get("claude_model") or CLAUDE_DEFAULT
    out = _post("https://api.anthropic.com/v1/messages",
                {"model": model, "max_tokens": 300, "system": SYSTEM,
                 "messages": [{"role": "user", "content": prompt}]},
                {"x-api-key": cfg["anthropic_key"], "anthropic-version": "2023-06-01"})
    u = out.get("usage") or {}
    return ("".join(b.get("text", "") for b in out.get("content", [])).strip(), model,
            u.get("input_tokens", 0), u.get("output_tokens", 0))


def _get(url, headers, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"user-agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _pick_model(name, base, key):
    """Nessun modello della lista esiste più: ne scelgo uno dall'elenco del servizio."""
    try:
        ids = [m.get("id", "") for m in _get(base + "/models", {"authorization": "Bearer " + key}).get("data", [])]
    except Exception:
        return None
    if name == "OpenRouter":
        ids = [i for i in ids if i.endswith(":free")]
    bad = ("embed", "whisper", "tts", "guard", "vision", "image", "audio", "moderation", "ocr", "codestral")
    ids = [i for i in ids if not any(b in i.lower() for b in bad)]
    for pref in ("llama-3.3-70b", "gpt-oss-120b", "small", "gemma", "llama", "qwen", "mistral"):
        for i in ids:
            if pref in i.lower():
                return i
    return ids[0] if ids else None


def _ask_openai(name, cfg, prompt, timeout=TIMEOUT):
    """Groq, Cerebras, OpenRouter, Mistral: stesso formato di richiesta."""
    _, keyname, base, models = FREE_BY_NAME[name]
    key = cfg[keyname]
    own = (cfg.get(name.lower() + "_model") or "").strip()
    order = [m for m in ([own, _model_cache.get(name)] + list(models)) if m]
    order = list(dict.fromkeys(order))
    headers = {"authorization": "Bearer " + key}
    if name == "OpenRouter":
        headers.update({"http-referer": "https://github.com/ingegnere-f1", "x-title": "Ingegnere F1"})
    last, listed = None, False
    i = 0
    while i < len(order):
        model = order[i]
        i += 1
        body = {"model": model, "max_tokens": 500, "temperature": 0.4,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
        if "gpt-oss" in model:
            body["reasoning_effort"] = "low"    # modello che "ragiona": poco, alla radio serve la risposta
        try:
            out = _post(base + "/chat/completions", body, headers, timeout)
        except urllib.error.HTTPError as e:
            if e.code not in (400, 404):
                raise                           # 429, chiave sbagliata...: decide la catena
            last = e                            # modello sparito o non adatto: provo il prossimo
            if i == len(order) and not listed:  # finita la lista: chiedo al servizio i modelli che ha
                listed = True
                m = _pick_model(name, base, key)
                if m and m not in order:
                    order.append(m)
            continue
        msg = (out.get("choices") or [{}])[0].get("message") or {}
        text = (msg.get("content") or "").strip()
        if not text:
            last = ValueError("risposta vuota")
            continue
        _model_cache[name] = model
        u = out.get("usage") or {}
        return text, model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    raise last or ValueError("nessun modello")


def _call(prov, cfg, prompt, timeout):
    if prov == "Claude":
        return _ask_claude(cfg, prompt)
    if prov == "Gemini":
        return _ask_gemini(cfg, prompt)
    return _ask_openai(prov, cfg, prompt, timeout)


# ------------------------------------------------------------------ conto delle domande
_ulock = threading.Lock()


def _usage_path(config_path):
    return os.path.join(os.path.dirname(config_path), "ai_uso.json")


def _ukeys():
    return [p.lower() for p in ALL]


def usage(config_path=CONFIG_PATH):
    """{"month", "day", "claude": {...}, "gemini": {...}}: domande, token e spesa del mese; domande di oggi."""
    try:
        with open(_usage_path(config_path)) as f:
            u = json.load(f)
    except (OSError, ValueError):
        u = {}
    month, day = time.strftime("%Y-%m"), time.strftime("%Y-%m-%d")
    if u.get("month") != month:
        u = {"month": month, "day": day, "prev": {k: u.get(k) for k in _ukeys() if u.get(k)} or None}
    if u.get("day") != day:
        u["day"] = day
        for k in _ukeys():
            if u.get(k):
                u[k]["today"] = 0
        u.pop("limit_said", None)
    for k in _ukeys():
        u.setdefault(k, {"q": 0, "today": 0, "in": 0, "out": 0, "usd": 0.0, "errors": 0})
    return u


def _record(config_path, prov, model, tin, tout, error=False):
    with _ulock:
        u = usage(config_path)
        r = u[prov.lower()]
        if error:
            r["errors"] = r.get("errors", 0) + 1
        else:
            r["q"] += 1
            r["today"] = r.get("today", 0) + 1
            r["in"] += tin
            r["out"] += tout
            pin, pout = PRICES.get(model, (0, 0)) if prov == "Claude" else (0, 0)
            r["usd"] = round(r.get("usd", 0) + (tin * pin + tout * pout) / 1e6, 5)
        try:
            with open(_usage_path(config_path), "w") as f:
                json.dump(u, f)
        except OSError:
            pass


def _limit_note(config_path):
    """Avviso «domande libere finite» una volta sola al giorno."""
    with _ulock:
        u = usage(config_path)
        if u.get("limit_said"):
            return None
        u["limit_said"] = True
        try:
            with open(_usage_path(config_path), "w") as f:
                json.dump(u, f)
        except OSError:
            pass
    return "Le domande libere sono finite per oggi: rispondo solo a quelle che conosco."


def ask(context, question, config_path=CONFIG_PATH):
    """{"text", "command", "provider"} dall'AI, oppure None se non c'è nessuna chiave.
    Se la principale non risponde (domande finite, rete, chiave sbagliata) provo la riserva."""
    cfg = load_config(config_path)
    order = providers(cfg)
    if not order:
        return None
    prompt = f"Dati:\n{json.dumps(context, ensure_ascii=False)}\n\nDomanda del pilota: {question}"
    problems = []
    start = time.time()
    now = start
    ready = [p for p in order if _cool.get(p, 0) <= now] or order   # se sono tutti in pausa li riprovo
    for prov in ready:
        left = DEADLINE - (time.time() - start)
        if left < 2:
            problems.append((prov, "tempo"))
            break
        try:
            text, model, tin, tout = _call(prov, cfg, prompt, min(TIMEOUT, left))
        except urllib.error.HTTPError as e:
            _record(config_path, prov, None, 0, 0, error=True)
            problems.append((prov, e.code))
            if e.code == 429:
                _cool[prov] = time.time() + COOLDOWN_429
            continue
        except Exception as e:  # rete assente, timeout...
            _record(config_path, prov, None, 0, 0, error=True)
            problems.append((prov, type(e).__name__))
            continue
        _record(config_path, prov, model, tin, tout)
        _cool.pop(prov, None)
        command = None
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if lines and lines[0].upper().startswith("COMANDO:"):
            command = lines[0].split(":", 1)[1].strip(" «»\"'.")
            lines = lines[1:]
        return {"text": " ".join(lines).strip(), "command": command or None, "provider": prov}
    # nessuno ha risposto
    codes = [c for _, c in problems]
    if codes and all(c == 429 for c in codes):
        note = _limit_note(config_path)
        return {"text": note, "command": None, "provider": None, "failed": True} if note else None
    if any(c in (400, 401, 403) for c in codes):
        bad = next(p for p, c in problems if c in (400, 401, 403))
        return {"text": f"La chiave di {bad} non funziona, controllala nelle impostazioni.", "command": None,
                "provider": None, "failed": True}
    return {"text": "Non riesco a contattare l'ingegnere AI, controlla la connessione.", "command": None,
            "provider": None, "failed": True}


def rule_based_debrief(context):
    laps = context.get("ultimi_giri") or []
    if not laps:
        return "Ancora nessun giro completo da analizzare."
    totals = {}
    for lap in laps:
        for z in lap["staccate"]:
            totals.setdefault(z["n"], []).append(z["loss"])
    if not totals:
        return f"Ultimo giro {laps[-1]['tempo']}. Serve un giro di riferimento per confrontare le staccate."
    avg = sorted(((sum(v) / len(v), n) for n, v in totals.items()), reverse=True)
    names = {z["n"]: z.get("nome") or f"staccata {z['n']}" for z in context.get("staccate_riferimento") or []}
    worst = [names.get(n, f"staccata {n}") for loss, n in avg[:2] if loss > 0.03]
    if not worst:
        return f"Ultimo giro {laps[-1]['tempo']}. Sei costante su tutte le staccate, continua così."
    return (f"Ultimo giro {laps[-1]['tempo']}. In media perdi di più in " + " e in ".join(worst)
            + ". Concentrati lì nei prossimi giri.")
