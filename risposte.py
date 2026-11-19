"""Risposte gratuite e istantanee alle domande tipiche di gara, senza internet.

Riconosce le parole chiave della domanda (box, meteo, gomme, distacchi...) e risponde
con i dati della telemetria. Se la domanda non rientra in nessun caso restituisce None
e la domanda passa all'AI (Gemini o Claude), se configurata.
"""
import difflib
import re
import unicodedata

from parlato import fmt_lap_voice, num, plural, tenths
from engineer_ai import rule_based_debrief

SLICKS = {"supersoft", "soft", "medie", "dure"}
WX = {0: "sereno", 1: "poco nuvoloso", 2: "coperto", 3: "pioggia leggera", 4: "pioggia forte", 5: "temporale"}


def _norm(text):
    t = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")


def _t_now():
    import time
    return time.time()


def _has(q, *words):
    return any(w in q for w in words)


# parole dette in modo diverso → la parola che conosco (prima di cercare la domanda)
SYNONYMS = [
    (r"\b(copertoni|ruote|pneus|mescol\w*)\b", " gomme "),
    (r"\b(benza|serbatoio)\b", " benzina "),
    (r"\b(pit ?stop|pitstop)\b", " box "),
    (r"\b(acqua|piovera|nuvol\w*|cielo|previsioni|tempo fuori|che tempo fa|tempo fa)\b", " meteo "),
    (r"\b(vantaggio|margine)\b", " distacco "),
    (r"\b(classifica|piazzamento)\b", " posizione "),
    (r"\b(tempi sul giro|cronometro|i miei tempi)\b", " passo "),
    (r"\b(carica|kers|ibrido|boost)\b", " batteria "),
    (r"\b(musetto|alettone|rottur\w*|danneggiat\w*|ho rotto)\b", " danni "),
    (r"\b(cosa sbaglio|dove sbaglio|come miglioro|suggeriment\w*|dritte?)\b", " dove perdo "),
    (r"\b(alla fine della gara|quanto manca alla fine|fine gara)\b", " quanti giri "),
    (r"\b(tattica|programma)\b", " strategia "),
    (r"\b(chi c e davanti|chi ho davanti)\b", " davanti "),
    (r"\b(chi c e dietro|chi ho dietro|chi mi segue)\b", " dietro "),
]
# parole che il riconoscimento vocale a volte storpia: le riconosco per somiglianza
VOCAB = {"gomme": "tyres", "usura": "tyres", "distacco": "gaps", "distacchi": "gaps", "davanti": "gaps",
         "dietro": "gaps", "benzina": "fuel", "carburante": "fuel", "batteria": "energy", "energia": "energy",
         "pioggia": "weather", "meteo": "weather", "strategia": "strategy", "posizione": "position",
         "assetto": "setup", "danni": "damage", "sosta": "pit", "fermarmi": "pit", "passo": "pace",
         "ritmo": "pace", "rientro": "rejoin", "record": "best", "frenata": "driving", "staccata": "driving",
         "situazione": "summary"}
LABELS = {"tyres": "le gomme", "gaps": "i distacchi", "fuel": "la benzina", "energy": "la batteria",
          "weather": "il meteo", "strategy": "la strategia", "position": "la posizione", "setup": "l'assetto",
          "damage": "i danni", "pit": "la sosta ai box", "pace": "il passo", "rejoin": "dove rientri dopo la sosta",
          "best": "il giro migliore", "driving": "dove perdi tempo", "summary": "come stiamo andando",
          "laps": "quanti giri mancano"}
YES = {"si", "esatto", "giusto", "certo", "ok", "proprio", "quello"}


def _expand(q):
    """Sinonimi e parole storpiate → le parole delle domande che conosco."""
    out = " " + q + " "
    for pat, rep in SYNONYMS:
        out = re.sub(pat, rep, out)
    words = []
    for w in out.split():
        if len(w) >= 4 and w not in VOCAB:
            close = difflib.get_close_matches(w, list(VOCAB), n=1, cutoff=0.8)
            if close:
                w = close[0]
        words.append(w)
    return " ".join(words) + " "


class Answers:
    def __init__(self, coach):
        self.c = coach
        self._guess = None            # (intento, momento) dopo «intendevi…?»
        self.ai_on = lambda: False    # il server mi dice se c'è un'AI per le domande più complicate

    # ------------------------------------------------------------ dati comodi
    def _me(self):
        return self.c.race.me()

    def _remaining(self):
        me, total = self._me(), self.c.session.get("total_laps", 0)
        if not me or not total or not self.c.in_race:
            return None
        return max(0, total - me["lap_num"] + 1)

    def _wear(self):
        w = self.c.damage.get("tyre_wear")
        return max(w) if w else None

    def _next_rain(self):
        return next((f for f in self.c.session.get("forecast", [])
                     if f["min"] > 0 and (f["weather"] >= 3 or f["rain"] >= 50)), None)

    def _laps_to_limit(self):
        h = self.c.race.wear_hist
        wear = self._wear()
        if len(h) < 2 or wear is None:
            return None
        rate = (h[-1][1] - h[0][1]) / max(1, h[-1][0] - h[0][0])
        return None if rate <= 0.3 else max(0, (70 - wear) / rate)

    # --------------------------------------------------------------- risposte
    def rejoin(self):
        est = self.c.race.rejoin_estimate(self.c.pit_loss)
        if not est:
            return "La stima di rientro funziona solo in gara."
        txt = f"Se ti fermi adesso rientri circa in P{est['pos']}"
        if est.get("ahead") and est.get("behind"):
            txt += f", tra {est['ahead']['name']} e {est['behind']['name']}"
        if est.get("behind"):
            g = est["behind"]["gap"]
            txt += f", con {num(g)} secondi di margine su chi ti segue" if g >= 1 else ", ma molto vicino a chi arriva"
        n = est.get("to_stop") or 0
        if n:
            txt += f". Di quelli davanti, {n} {'deve' if n == 1 else 'devono'} ancora fermarsi"
        return txt + "."

    def pit(self):
        s, st = self.c.session, self.c.status
        me, rem, wear = self._me(), self._remaining(), self._wear()
        comp = st.get("compound")
        slick = comp in SLICKS if comp else True
        w = s.get("weather", 0)
        rain = self._next_rain()
        if not self.c.in_race:
            return "Non siamo in gara: fermati quando vuoi, la strategia conta solo in gara."
        if w >= 3 and slick:
            ans = "Sì, box: sta piovendo e sei sulle slick. Monta le intermedie."
        elif w <= 1 and not slick and comp:
            ans = "Sì, la pista è asciutta: box per le slick."
        elif rain and slick and rain["min"] <= 10 and (wear or 0) < 65:
            return (f"Aspetta: tra {rain['min']} minuti è prevista pioggia al {rain['rain']} per cento. "
                    "Conviene fermarsi direttamente per le intermedie.")
        elif s.get("safety_car") in (1, 2):
            # il conto vero: quanto si perde sotto safety car e dove rientri
            adv = self.c.race.strategy.sc_advice(s.get("safety_car"))
            return adv or "Sì, con la safety car perdi meno tempo ai box."
        elif wear is not None and wear >= 65:
            ans = f"Sì, le gomme sono al {wear:.0f} per cento di usura."
        elif rem is not None and rem <= 3:
            return f"No, mancano {plural(rem, 'giro', 'giri')}: resta fuori e porta a casa la posizione."
        elif me and s.get("pit_ideal") and me["pit_stops"] == 0 and me["lap_num"] < s["pit_ideal"] - 1:
            left = self._laps_to_limit()
            ans = f"Non ancora: la finestra si apre al giro {s['pit_ideal']}."
            if wear is not None:
                ans += f" Usura al {wear:.0f} per cento"
                ans += f", circa {round(left)} giri prima del limite." if left else "."
            return ans
        elif me and s.get("pit_ideal") and me["pit_stops"] == 0:
            ans = f"Sì, siamo nella finestra pit, entro il giro {s['pit_latest']}."
        else:
            return f"Per ora non serve. Usura al {wear:.0f} per cento." if wear is not None else "Per ora non serve."
        if rem and slick and w < 3:
            st = self.c.race.strategy
            comp_now = self.c.status.get("compound")
            must = me and st.must_change_compound(me)
            nxt, ok = st.pick_compound(rem, st.wear_rate(), comp_now, (comp_now,) if must else ())
            if st.wear_rate() is None:
                nxt = "dure" if rem > 20 else ("medie" if rem > 10 else "soft")
                if must and nxt == comp_now:
                    nxt = "medie" if comp_now != "medie" else "dure"
            ans += f" Per gli ultimi {rem} giri metti le {nxt}."
            if not ok and st.wear_rate() is not None:
                ans += " Ma probabilmente servirà un'altra sosta."
        return ans + " " + self.rejoin()

    def strategy(self):
        return self.c.race.strategy.describe()

    def weather(self):
        s = self.c.session
        if not s:
            return "Non ho ancora i dati del meteo."
        txt = f"Ora {WX.get(s.get('weather'), 'sconosciuto')}, pista a {s.get('track_temp')} gradi, aria a {s.get('air_temp')}."
        rain = self._next_rain()
        if rain and s.get("weather", 0) < 3:
            txt += f" Pioggia prevista tra {rain['min']} minuti, al {rain['rain']} per cento."
        elif s.get("weather", 0) >= 3:
            dry = next((f for f in s.get("forecast", []) if f["min"] > 0 and f["weather"] <= 2), None)
            txt += f" Dovrebbe smettere tra circa {dry['min']} minuti." if dry else " Non è previsto che smetta a breve."
        else:
            txt += " Nessuna pioggia prevista."
        return txt

    def tyres(self):
        st, tel = self.c.status, self.c.telemetry
        wear = self.c.damage.get("tyre_wear")
        if not st:
            return "Non ho ancora i dati delle gomme."
        txt = f"Hai le {st.get('compound')} di {plural(st.get('tyre_age', 0), 'giro', 'giri')}."
        if wear:
            txt += f" Usura massima {max(wear):.0f} per cento."
            left = self._laps_to_limit()
            if left is not None:
                txt += f" Al ritmo attuale arrivi al limite tra circa {plural(round(left), 'giro', 'giri')}."
        temps = tel.get("tyre_surface")
        if temps:
            txt += f" Temperature tra {min(temps)} e {max(temps)} gradi."
        return txt

    def gaps(self):
        r = self.c.race
        me = r.me()
        if not me or not self.c.in_race:
            return "I distacchi li ho solo in gara."
        parts = [f"Sei P{me['position']}."]
        a, b = r.rel(-1), r.rel(1)
        if a:
            parts.append(f"Davanti {r.name(a['idx'])} a {self.c.gap_text(r.gap_between(a, me))}.")
        if b:
            parts.append(f"Dietro {r.name(b['idx'])} a {self.c.gap_text(r.gap_between(me, b))}.")
        return " ".join(parts)

    def summary(self):
        """«Radio, come stiamo andando?»: tutto in una risposta corta, come al muretto."""
        c, r = self.c, self.c.race
        me = r.me()
        if not me or not c.in_race:
            return self.driving() if not c.in_race else "Ancora niente dati della gara."
        rem = self._remaining()
        parts = [f"Sei P{me['position']}" + (f", mancano {plural(rem, 'giro', 'giri')}" if rem else "")]
        a, b = r.rel(-1), r.rel(1)
        g = []
        if a:
            g.append(f"{r.name(a['idx'])} davanti a {c.gap_text(r.gap_between(a, me))}")
        if b:
            g.append(f"{r.name(b['idx'])} dietro a {c.gap_text(r.gap_between(me, b))}")
        if g:
            parts.append(", ".join(g))
        p = c.race_pace()
        last = next((h for h in reversed(c.history) if h.get("race") and h["valid"]), None)
        if p and last:
            d = (last["ms"] - p) / 1000
            parts.append("ritmo costante" if abs(d) < 0.2 else f"ultimo giro {tenths(d)} {'sopra' if d > 0 else 'sotto'} il passo")
        wear = c.damage.get("tyre_wear")
        if wear:
            parts.append(f"gomme al {max(wear):.0f} per cento")
        txt = ". ".join(x[0].upper() + x[1:] for x in parts) + "."
        plan = r.strategy.last_plan
        if plan and plan[0] and plan[1]:
            txt += f" Il piano resta box verso il giro {plan[1]}" + (f", poi {plan[2]}." if plan[2] else ".")
        return txt

    def position(self):
        me, rem = self._me(), self._remaining()
        if not me:
            return "Non ho ancora la posizione."
        return f"Sei P{me['position']}" + (f", mancano {plural(rem, 'giro', 'giri')}." if rem else ".")

    def fuel(self):
        fl = self.c.status.get("fuel_laps")
        if fl is None:
            return "Non ho ancora il dato del carburante."
        if fl >= 0:
            return f"Carburante ok, hai {num(fl)} giri di margine."
        return f"Sei sotto di {num(fl)} giri: alza e lascia scorrere prima delle frenate."

    def best(self):
        """Il giro più veloce: in gara lo dico solo se me lo chiedi."""
        c = self.c
        mine = [h for h in c.history if h["valid"] and h.get("race", False) == bool(c.in_race)]
        parts = []
        if mine:
            b = min(mine, key=lambda h: h["ms"])
            parts.append(f"Il tuo giro più veloce {'in gara' if c.in_race else 'in questa sessione'} è "
                         f"{fmt_lap_voice(b['ms'])}, al giro {b['lap']}.")
        if c.record_past and c.record_ms:
            parts.append(f"Il tuo record a {c.track_name} è {fmt_lap_voice(c.record_ms)}.")
        return " ".join(parts) or "Non ho ancora un giro valido completo."

    def pace(self):
        if not self.c.in_race:
            return self.driving()
        p = self.c.race_pace()
        if not p:
            return "Il passo te lo dico dopo 3 giri puliti in gara."
        last = next((h for h in reversed(self.c.history) if h.get("clean")), None)
        txt = f"Il tuo passo è {fmt_lap_voice(p)}."
        if last:
            d = (last["ms"] - p) / 1000
            txt += (" L'ultimo giro è in linea." if abs(d) < 0.2 else
                    f" L'ultimo giro è {tenths(d)} {'sopra' if d > 0 else 'sotto'}.")
        return txt

    def laps(self):
        rem = self._remaining()
        return f"Mancano {plural(rem, 'giro', 'giri')}." if rem else "Non è una gara a giri, o non ho ancora il dato."

    def damage(self):
        wing = self.c.damage.get("front_wing")
        if wing is None:
            return "Non ho dati sui danni."
        return "Nessun danno all'ala anteriore." if wing < 5 else f"Ala anteriore danneggiata al {wing} per cento."

    def setup(self):
        return self.c.setup.text()

    def energy(self):
        return self.c.energy.answer()

    def driving(self):
        return rule_based_debrief(self.c.context_for_ai())

    # ------------------------------------------------------------ smistamento
    INTENTS = [
        ("setup", ("assetto", "setup", "sottoster", "sovraster", "ala anter", "ala poster", "differenzial",
                   "antirollio", "bilanciamento fren", "bloccag")),
        ("strategy", ("strategi", "piano", "quante soste", "quando mi fermo", "quando devo fermarmi")),
        ("rejoin", ("dove esco", "dove rientro", "dove rientr", "in che posizione esco", "in che posizione rientro",
                    "posizione dopo il pit", "se mi fermo dove")),
        ("pit", ("box", "mi fermo", "fermarmi", "fermare", "sosta", "pit", "cambio gomm", "cambiare gomm")),
        ("weather", ("piov", "pioggia", "meteo", "che tempo", "bagnat", "asciug", "temporale")),
        ("tyres", ("gomm", "usura", "pneumatic", "temperatur")),
        ("gaps", ("davanti", "dietro", "distacc", "gap", "quanto ho", "secondi da")),
        ("energy", ("batteri", "energia", " ers ", "elettric", "overtake", "ricaric")),
        ("fuel", ("carburant", "benzin", "fuel")),
        ("best", ("giro piu veloce", "giro migliore", "miglior giro", "record", "il mio migliore", "giro veloce")),
        ("summary", ("come stiamo andando", "come sto andando", "come va", "situazione", "riassunto", "come siamo messi",
                     "come vado")),
        ("pace", ("passo", "ritmo")),
        ("laps", ("quanti giri", "mancano", "giri mancanti")),
        ("position", ("posizione", "che posto", "in che posto")),
        ("damage", ("dann", "ala ", "alettone", "rotto")),
        ("driving", ("dove perdo", "staccat", "consigl", "miglioro", "migliorare", "frenat", "debrief")),
    ]

    # ------------------------------------------------------------ comandi della radio
    COMMANDS = [
        ("help", ("aiuto", "cosa posso chiederti", "cosa posso chiedere", "che domande", "comandi")),
        ("training", ("allenamento",)),
        ("mute", ("silenzio", "stai zitto", "zitto", "basta parlare", "non parlare", "taci")),
        ("more", ("parla di piu", "parla piu", "piu messaggi", "piu informazioni")),
        ("less", ("parla di meno", "parla meno", "meno messaggi", "meno informazioni")),
        ("unmute", ("parla di nuovo", "riprendi a parlare", "torna a parlare", "puoi parlare", "riprendi")),
        ("repeat", ("ripeti", "cosa hai detto", "puoi ripetere", "ridillo")),
        ("report", ("segnala", "segnalazione", "era sbagliat", "hai sbagliato", "non e vero", "questo e un errore")),
        ("check", ("mi senti", "mi sentite", "prova radio", "radio check", "check radio", "ci sei", "sei li", "prova prova")),
    ]
    LEVELS = {1: "poco", 2: "normale", 3: "tanto"}

    def command(self, q):
        c = self.c
        # "lavoriamo sulla frenata", "concentriamoci sull'uscita", "lavoriamo su tutto"
        if _has(q, "lavori", "concentr"):
            for key, words, said in (("frenata", ("frenat", "staccat", "freno"), "sulle frenate"),
                                     ("centro", ("centro", "percorrenz", "velocita in curva", "ingresso"), "sulla velocità a centro curva"),
                                     ("uscita", ("uscita", "gas", "trazione", "acceleratore"), "sull'uscita e il gas"),
                                     ("tutto", ("tutto", "tutte", "normale"), "su tutto")):
                if _has(q, *words):
                    c.work_on = key
                    return (f"Ok, lavoriamo {said}." + ("" if key == "tutto" else
                            " Gli altri consigli te li lascio scritti."))
        name = next((n for n, words in self.COMMANDS if _has(q, *words)), None)
        if name is None and q.strip() in ("parla", "parla pure", "vai"):
            name = "unmute"
        if name == "report":
            n = c.on_report(q)
            return (f"Segnalato, grazie. È la segnalazione numero {n}: la trovi nelle Impostazioni." if n
                    else "Segnalato.")
        if name == "check":
            import random
            return random.choice(("Sì, forte e chiaro.", "Sì, forte e chiaro.", "Forte e chiaro, ci sono.", "Ti sento forte e chiaro."))
        if name == "mute":
            c.muted = True
            return ("Ricevuto, silenzio radio. Ti parlo solo per le cose importanti: safety car, box, bandiere e "
                    "penalità. Dimmi «Radio, parla» quando vuoi che riprenda.")
        if name == "unmute":
            c.muted = False
            return "Ok, riprendo a parlarti normalmente."
        if name in ("more", "less"):
            c.muted = False
            old = c.verbosity
            c.verbosity = min(3, old + 1) if name == "more" else max(1, old - 1)
            if c.verbosity == old:
                return f"Sono già al livello {self.LEVELS[old]}."
            return {1: "Ok, parlo di meno: solo le cose importanti.",
                    2: "Ok, livello normale: anche i distacchi a ogni giro.",
                    3: "Ok, parlo di più: anche i consigli di guida in gara."}[c.verbosity]
        if name == "help":
            return self.HELP
        if name == "repeat":
            return c.last_spoken or "Non ho ancora detto niente."
        if name == "training":
            t = c.training
            if "spegni" in q or "basta" in q or "stop" in q or "ferma" in q:
                t.set_enabled(False)
                return "Allenamento mirato spento: ti parlo di tutte le staccate."
            if "accendi" in q or "attiva" in q or "riprendi" in q or "inizia" in q:
                t.set_enabled(True)
                return "Allenamento mirato acceso: in prova lavoriamo su una staccata alla volta."
            return t.status_text()
        return None

    def answer(self, question):
        q = _norm(question) + " "
        cmd = self.command(q)
        if cmd is not None:
            return cmd
        fol = self.c.race.follow.command(q)       # «segui Norris» / «basta seguire»
        if fol:
            return fol
        # stai rispondendo alla chiamata box?
        if self.c.race.strategy.pending_box():
            words = set(re.findall(r"[a-z]+", q))
            if words & {"si", "ok", "confermo", "confermato", "entro", "vado", "daccordo", "accordo", "certo", "ricevuto", "copiato"} \
                    or "va bene" in q:
                return self.c.race.strategy.answer_box(True)
            if words & {"no", "allunghiamo", "allunga", "restiamo", "resto", "aspetta", "aspettiamo"} or "non ancora" in q \
                    or "ancora un giro" in q:
                return self.c.race.strategy.answer_box(False)
        # risposta a «intendevi le gomme?»
        g = self._guess
        if g and _t_now() - g[1] < 25:
            words = set(re.findall(r"[a-z]+", q))
            self._guess = None
            if words & YES and len(words) <= 4:
                self.c.note_question(g[0])
                return getattr(self, g[0])()
            if words & {"no", "niente", "lascia"} and len(words) <= 3:
                return "Ok, ripeti la domanda con altre parole."
        rel = self.friend_message(q)
        if rel:
            return rel
        short = self.short(q)
        if short:
            return short
        import time as _t
        last = getattr(self, "_last", None)
        follow = (last and _t.time() - last[1] < 40 and len(q.split()) <= 5     # "e lui?", "e adesso?", "quanto?"
                  and (q.startswith("e ") or re.search(r"\b(adesso|ora|quanto|lui|lei|invece|aggiorna\w*)\b", q)))
        fs = self.c.race.friend.asked(q) or (last[2] if follow and last[2] and not
                                             any(_has(q, *w) for _, w in self.INTENTS) else None)
        if fs:
            txt = self.c.race.friend.answer(fs)
            if txt:
                self._last = ([], _t.time(), fs)
                return txt
        # «dov'è Leclerc?», «come va Norris?»: un pilota qualsiasi della gara, chiamato per nome
        if self.c.in_race:
            idx = self.c.race.follow.asked(q)
            simple = len(q.split()) <= 5 or re.search(r"\b(dov|come va|come sta|a che punto|quanto ha|che gomme)", q)
            if idx is not None and (simple or not self.ai_on()):
                self._last = ([], _t.time(), None)
                return self.c.race.follow.status(short=False, idx=idx)
        found = []
        for name, words in self.INTENTS:
            if _has(q, *words) and name not in found:
                if name == "pit" and ("rejoin" in found or "strategy" in found):
                    continue
                found.append(name)
        if not found:
            qx = _expand(q)                      # detta con altre parole, o capita un po' storpiata
            for name, words in self.INTENTS:
                if _has(qx, *words) and name not in found:
                    if name == "pit" and ("rejoin" in found or "strategy" in found):
                        continue
                    found.append(name)
        if not found and follow and last[0]:
            found = list(last[0])                # stessa domanda di prima, aggiornata
        if not found:
            return None
        self._last = (found[:2], _t.time(), None)
        for n in found[:2]:
            self.c.note_question(n)
        return " ".join(getattr(self, n)() for n in found[:2])

    def guess(self, question):
        """Domanda non capita: se somiglia a una che conosco, chiedo «intendevi…?» (None se non somiglia a niente)."""
        q = _norm(question)
        best = None
        for w in re.findall(r"[a-z]+", q):
            if len(w) < 4:
                continue
            m = difflib.get_close_matches(w, list(VOCAB), n=1, cutoff=0.62)
            if m:
                score = difflib.SequenceMatcher(None, w, m[0]).ratio()
                if not best or score > best[0]:
                    best = (score, VOCAB[m[0]])
        if not best:
            return None
        self._guess = (best[1], _t_now())
        return f"Intendevi {LABELS.get(best[1], best[1])}? Dimmi sì."

    SHORT = {"gap": "gap", "gaps": "gap", "distacco": "gap", "distacchi": "gap", "gomme": "tyres", "gomma": "tyres",
             "batteria": "energy", "energia": "energy", "benzina": "fuel", "carburante": "fuel", "posizione": "pos",
             "passo": "pace", "ritmo": "pace", "meteo": "wx", "pioggia": "wx", "giri": "laps"}

    def short(self, q):
        """«Radio, gap» / «Radio, gomme»: una frase sola, da battaglia."""
        w = q.split()
        if not w or len(w) > 2 or w[0] not in self.SHORT:
            return None
        k, c, r = self.SHORT[w[0]], self.c, self.c.race
        me = r.me()
        if k == "gap":
            if not me or not c.in_race:
                return "Niente distacchi fuori gara."
            a, b = r.rel(-1), r.rel(1)
            bits = []
            if a:
                bits.append(f"davanti {c.gap_text(r.gap_between(a, me))}")
            if b:
                bits.append(f"dietro {c.gap_text(r.gap_between(me, b))}")
            return (", ".join(bits) or "nessuno vicino").capitalize() + "."
        if k == "tyres":
            wr = c.damage.get("tyre_wear")
            st = c.status
            c.note_question("tyres")
            return (f"{st.get('compound', 'gomme').capitalize()} al {max(wr):.0f} per cento, "
                    f"{plural(st.get('tyre_age', 0), 'giro', 'giri')}.") if wr else "Niente dati gomme."
        if k == "energy":
            c.note_question("energy")
            p = c.energy.now_pct
            return f"Batteria al {p:.0f} per cento." if p is not None else "Niente dati batteria."
        if k == "fuel":
            c.note_question("fuel")
            fl = c.status.get("fuel_laps")
            return f"Carburante {'più' if fl >= 0 else 'meno'} {num(abs(fl))} giri." if fl is not None else "Niente dati carburante."
        if k == "pos":
            rem = self._remaining()
            return f"P{me['position']}" + (f", {rem} alla fine." if rem else ".") if me else "Niente posizione."
        if k == "pace":
            p = c.race_pace() if c.in_race else None
            last = next((h for h in reversed(c.history) if h.get("race") and h["valid"]), None)
            if not p or not last:
                return "Passo non ancora pronto."
            d = (last["ms"] - p) / 1000
            return "In linea col passo." if abs(d) < 0.2 else f"{tenths(d).capitalize()} {'sopra' if d > 0 else 'sotto'} il passo."
        if k == "box":
            lp = r.strategy.last_plan
            return (f"Box giro {lp[1]}, poi {lp[2]}." if lp[2] else f"Box giro {lp[1]}.") if lp and lp[0] and lp[1] \
                else "Nessuna sosta in programma."
        if k == "wx":
            rain = self._next_rain()
            return f"Pioggia tra {rain['min']} minuti." if rain else "Asciutto, niente pioggia in arrivo."
        if k == "laps":
            rem = self._remaining()
            return f"{plural(rem, 'giro', 'giri').capitalize()} alla fine." if rem else None
        return None

    def friend_message(self, q):
        """«Radio, di' a Marco che lo lascio passare» → il suo ingegnere glielo dice in cuffia.
        «Radio, chiedi a Marco quando si ferma» → rispondo con i dati della sua macchina."""
        fr = self.c.race.friend
        fs = fr.friends()
        to = re.match(r"^(?:chiedi|di|dici|avvisa|riferisci|manda|scrivi) a ([a-z0-9_]+)\b", q.strip())
        if not fs:
            return f"Non vedo {to.group(1).capitalize()} in gara con te adesso." if to else None
        names = {}
        for f in fs:
            n = _norm(f["name"]).strip()
            names[n] = f
            names.setdefault(n.split()[0], f) if n else None
        m = re.match(r"^(chiedi|di|dici|avvisa|riferisci|manda|scrivi)\s+(?:a\s+)?([a-z0-9_]+)\s*(?:che\s+)?(.*)$", q.strip())
        if not m or m.group(2) not in names:
            return f"Non vedo {to.group(1).capitalize()} in gara con te adesso." if to else None
        verb, f, rest = m.group(1), names[m.group(2)], m.group(3).strip(" ,.!?")
        if verb == "chiedi":
            me = self.c.race.me()
            if _has(" " + rest + " ", "ferm", "box", "sosta", "pit"):
                if f.get("plan_lap") and me and f["plan_lap"] >= me["lap_num"]:
                    txt = f"{f['name']} pensa di fermarsi verso il giro {f['plan_lap']}"
                elif (f.get("stops") or 0) > 0:
                    txt = f"{f['name']} si è già fermato"
                else:
                    txt = f"{f['name']} per ora non ha una sosta in programma"
                if f.get("wear") is not None:
                    txt += f", gomme al {f['wear']:.0f} per cento"
                return txt + "."
            return fr.answer([f]) or f"Non ho dati di {f['name']}."
        if not rest:
            return f"Cosa devo dire a {f['name']}?"
        ok = self.c.send_to_friend(f["name"], rest)
        return f"Fatto, l'ho detto a {f['name']}." if ok else f"Non riesco a raggiungere {f['name']} adesso."

    HELP = ("Comandi: mi senti?, segnala, aiuto, silenzio, parla, parla di più, parla di meno, ripeti, allenamento, lavoriamo sulla frenata (o centro curva, uscita, tutto). Puoi chiedermi: mi fermo ai box?, qual è la strategia?, dove rientro?, piove?, come stanno le gomme?, "
            "distacchi, batteria, carburante, qual è il mio passo, qual è il mio giro più veloce, come sta il mio amico, chiedi a Marco quando si ferma, di' a Marco che…, segui Norris (o segui la P3), basta seguire, dov'è Leclerc, come stiamo andando, quanti giri mancano, dove perdo tempo, consigli d'assetto.")
