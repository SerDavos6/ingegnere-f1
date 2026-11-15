"""Ingegnere AI per le domande libere: Google Gemini (piano gratuito) oppure Claude.

Le chiavi si inseriscono dalla dashboard (⚙️ Impostazioni) e vengono salvate in config.json.
Se c'è la chiave Gemini si usa Gemini; altrimenti Claude; altrimenti solo risposte a regole.
"""
import json
import os
import urllib.error
import urllib.request

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

SYSTEM = (
    "Sei l'ingegnere di pista di un pilota che corre su EA F1 con la PS5, e gli parli via radio. "
    "Ricevi un JSON con la telemetria riassunta (giri, staccate, gomme, carburante, meteo, distacchi, "
    "stima di rientro dal pit). Rispondi in italiano: massimo 3 frasi brevi, niente elenchi, "
    "niente simboli, numeri scritti in modo che si possano leggere ad alta voce. "
    "In gara dai indicazioni di strategia (se fermarsi, con che gomma, dove rientra), meteo, "
    "distacchi e avversari; in prova e qualifica indicazioni di guida e assetto. "
    "Basati solo sui dati: se mancano, dillo."
)


def load_config(path=CONFIG_PATH):
    cfg = {}
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        pass
    cfg.setdefault("gemini_key", os.environ.get("GEMINI_API_KEY", ""))
    cfg.setdefault("anthropic_key", os.environ.get("ANTHROPIC_API_KEY", ""))
    cfg.setdefault("gemini_model", "gemini-flash-latest")
    cfg.setdefault("claude_model", "claude-sonnet-5-5")
    cfg.setdefault("pit_loss", 22.0)
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)


def provider(cfg=None):
    cfg = cfg or load_config()
    if cfg.get("gemini_key"):
        return "Gemini"
    if cfg.get("anthropic_key"):
        return "Claude"
    return None




def _post(url, body, headers, timeout=20):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _ask_gemini(cfg, prompt):
    models = [cfg["gemini_model"]] + [m for m in ("gemini-flash-latest", "gemini-flash-lite-latest")
                                      if m != cfg["gemini_model"]]
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
            return "".join(p.get("text", "") for p in parts).strip()
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:          # modello non disponibile: provo il successivo
                continue
            raise
    raise last


def _ask_claude(cfg, prompt):
    out = _post("https://api.anthropic.com/v1/messages",
                {"model": cfg["claude_model"], "max_tokens": 300, "system": SYSTEM,
                 "messages": [{"role": "user", "content": prompt}]},
                {"x-api-key": cfg["anthropic_key"], "anthropic-version": "2023-06-01"})
    return "".join(b.get("text", "") for b in out.get("content", [])).strip()


def ask(context, question, config_path=CONFIG_PATH):
    """Risposta dell'AI, oppure None se nessuna chiave è configurata."""
    cfg = load_config(config_path)
    prov = provider(cfg)
    if not prov:
        return None
    prompt = f"Dati:\n{json.dumps(context, ensure_ascii=False)}\n\nDomanda del pilota: {question}"
    try:
        return _ask_gemini(cfg, prompt) if prov == "Gemini" else _ask_claude(cfg, prompt)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "Ho finito le domande gratuite per ora, riprova tra un po'."
        if e.code in (400, 401, 403):
            return f"La chiave di {prov} non funziona, controllala nelle impostazioni."
        return f"Errore dall'ingegnere AI, codice {e.code}."
    except Exception as e:  # rete assente, timeout...
        return f"Non riesco a contattare l'ingegnere AI ({type(e).__name__}). Controlla la connessione."


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
    worst = [f"staccata {n}" for loss, n in avg[:2] if loss > 0.03]
    if not worst:
        return f"Ultimo giro {laps[-1]['tempo']}. Sei costante su tutte le staccate, continua così."
    return (f"Ultimo giro {laps[-1]['tempo']}. In media perdi di più alla " + " e alla ".join(worst)
            + ". Concentrati lì nei prossimi giri.")
