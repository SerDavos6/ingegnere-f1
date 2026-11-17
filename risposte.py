"""Risposte gratuite e istantanee alle domande tipiche di gara, senza internet.

Riconosce le parole chiave della domanda (box, meteo, gomme, distacchi...) e risponde
con i dati della telemetria. Se la domanda non rientra in nessun caso restituisce None
e la domanda passa all'AI (Gemini o Claude), se configurata.
"""
import unicodedata

from parlato import num, plural
from engineer_ai import rule_based_debrief

SLICKS = {"supersoft", "soft", "medie", "dure"}
WX = {0: "sereno", 1: "poco nuvoloso", 2: "coperto", 3: "pioggia leggera", 4: "pioggia forte", 5: "temporale"}


def _norm(text):
    t = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in t if unicodedata.category(ch) != "Mn")


def _has(q, *words):
    return any(w in q for w in words)


class Answers:
    def __init__(self, coach):
        self.c = coach

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
            ans = "Sì, con la safety car perdi meno tempo ai box."
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
            parts.append(f"Davanti {r.name(a['idx'])} a {num(r.gap_between(a, me))} secondi.")
        if b:
            parts.append(f"Dietro {r.name(b['idx'])} a {num(r.gap_between(me, b))} secondi.")
        return " ".join(parts)

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
        ("fuel", ("carburant", "benzin", "fuel")),
        ("laps", ("quanti giri", "mancano", "giri mancanti")),
        ("position", ("posizione", "che posto", "in che posto")),
        ("damage", ("dann", "ala ", "alettone", "rotto")),
        ("driving", ("dove perdo", "come vado", "staccat", "consigl", "miglior", "frenat", "debrief")),
    ]

    def answer(self, question):
        q = _norm(question) + " "
        found = []
        for name, words in self.INTENTS:
            if _has(q, *words) and name not in found:
                if name == "pit" and ("rejoin" in found or "strategy" in found):
                    continue
                found.append(name)
        if not found:
            return None
        return " ".join(getattr(self, n)() for n in found[:2])

    HELP = ("Puoi chiedermi: mi fermo ai box?, qual è la strategia?, dove rientro?, piove?, come stanno le gomme?, "
            "distacchi, carburante, quanti giri mancano, dove perdo tempo, consigli d'assetto.")
