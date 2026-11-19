"""«Radio, segui Norris»: un avversario da tenere d'occhio per tutta la gara, anche se non è vicino.

L'ingegnere ti aggiorna ogni 2 giri (posizione, distacco e se recuperi), quando entra ai box,
che gomme monta e dove rientra, e se si ritira. «Radio, basta seguire» per smettere.
Puoi anche chiedere di qualsiasi pilota: «Radio, dov'è Leclerc?».
"""
import difflib
import re

from parlato import plural, tenths


def _n(s):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).split())


class Follow:
    EVERY = 2               # giri tra un aggiornamento e l'altro
    PIT_CONFIRM_S = 6       # come per i rivali vicini: chi si ritira spesso passa dalla pit lane

    def __init__(self, race):
        self.r = race
        self.reset()

    def reset(self):
        self.idx = None
        self.said_lap = -10
        self.gaps = {}          # giro -> distacco (positivo = lui davanti)
        self.prev_pit = None
        self.pit_in = None      # momento in cui è entrato in pit lane
        self.in_pit = False
        self.stops = None
        self.out = False        # ritirato: già detto

    @property
    def c(self):
        return self.r.c

    # ------------------------------------------------------------ chi è
    def candidates(self):
        """{nome detto a voce in minuscolo: indice auto} per tutti i piloti in gara."""
        out = {}
        for idx in self.r.by_idx:
            if idx == self.c.player_idx:
                continue
            full = _n(self.r.name(idx, voice=False))
            spoken = _n(self.r.name(idx))
            for k in {full, spoken, *full.split(), *spoken.split()}:
                if len(k) >= 3 and k not in ("il", "tuo", "amico", "compagno", "numero", "della"):
                    out.setdefault(k, idx)
        return out

    def find(self, text):
        """Il pilota nominato nella frase (anche se il riconoscimento vocale storpia un po' il nome)."""
        t = _n(text)
        if not t:
            return None
        m = re.search(r"\bp\s?(\d{1,2})\b", t)
        if m:
            car = next((c for c in self.r.cars if c["position"] == int(m.group(1))), None)
            if car and car["idx"] != self.c.player_idx:
                return car["idx"]
        if re.search(r"\b(amico|compagno)\b", t):
            mates = self.r.mates()
            if mates:
                return mates[0][1]
        if re.search(r"\b(chi e |quello )?davanti\b", t) and self.r.rel(-1):
            return self.r.rel(-1)["idx"]
        if re.search(r"\b(chi e |quello )?dietro\b", t) and self.r.rel(1):
            return self.r.rel(1)["idx"]
        # «quello della Ferrari»: la macchina di quella squadra più vicina a te
        from race import TEAMS
        me = self.r.me()
        for tid, team in TEAMS.items():
            tn = _n(team)
            if tn and re.search(r"\b" + re.escape(tn) + r"\b", t):
                cars = [c for c in self.r.cars if c["idx"] != self.c.player_idx
                        and c.get("result_status") in (2, 3, None)
                        and (self.r.names.get(c["idx"]) or {}).get("team") == tid]
                if cars:
                    pos = me["position"] if me else 0
                    return min(cars, key=lambda c: abs(c["position"] - pos))["idx"]
        names = self.candidates()
        if t in names:
            return names[t]
        for w in t.split():
            if w in names:
                return names[w]
        for w in t.split():
            if len(w) >= 4:
                close = difflib.get_close_matches(w, list(names), n=1, cutoff=0.75)
                if close:
                    return names[close[0]]
        return None

    def asked(self, q):
        """Indice del pilota nominato in una domanda (solo nomi esatti, niente somiglianze)."""
        names = self.candidates()
        words = _n(q).split()
        for w in words:
            if len(w) >= 4 and w in names:
                return names[w]
        if self.idx is not None and re.search(r"\b(lui|seguito|quello che segui)\b", _n(q)) and len(words) <= 5:
            return self.idx
        return None

    # ------------------------------------------------------------ comandi
    def command(self, q):
        """None se la frase non è un comando "segui"; altrimenti la risposta."""
        s = q.strip()
        if re.match(r"^(basta|smetti|stop|lascia)( di)? (seguire|perdere)\b|^non seguire\b", s):
            if self.idx is None:
                return "Non stavo seguendo nessuno."
            n = self.r.name(self.idx)
            self.reset()
            return f"Ok, smetto di seguire {n}."
        m = re.match(r"^(?:segui|seguimi|tieni d ?occhio|tienimi d ?occhio|controlla|marca)\s+(.+)$", s)
        if not m:
            return None
        if not self.c.in_race:
            return "Posso seguire un pilota solo in gara."
        idx = self.find(m.group(1))
        if idx is None:
            return f"Non trovo {m.group(1).strip()} in gara. Prova con il cognome o con la posizione, per esempio «segui la P3»."
        self.reset()
        self.idx = idx
        me = self.r.me()
        if me:
            self.said_lap = me["lap_num"]
            g, car = self.gap(me), self.r.by_idx.get(idx)
            if g is not None and car:
                self.gaps[me["lap_num"]] = (g, (car.get("pit_stops") or 0, me["pit_stops"]))
        st = self.status(short=False)
        n = self.r.name(idx)
        rest = st[len(n):].lstrip(" ,") if st.startswith(n) else st
        return f"Ricevuto, seguo {n}. " + (rest[0].upper() + rest[1:] if rest else "")

    # ------------------------------------------------------------ dati
    def car(self):
        return self.r.by_idx.get(self.idx) if self.idx is not None else None

    def gap(self, me, idx=None):
        """Distacco in secondi, positivo se lui è davanti."""
        car = self.r.by_idx.get(self.idx if idx is None else idx)
        if not car or not me:
            return None
        if car["position"] < me["position"]:
            g = self.r.timer.gap(car["idx"], me["idx"])
            return g if g is not None and g < 200 else None
        g = self.r.timer.gap(me["idx"], car["idx"])
        return -g if g is not None and g < 200 else None

    def status(self, short=True, idx=None):
        """«Norris è P4, a 6 secondi davanti, medie di 12 giri, una sosta.»"""
        idx = self.idx if idx is None else idx
        car, me = self.r.by_idx.get(idx), self.r.me()
        if not car:
            return "non lo vedo più in gara."
        n = self.r.name(idx)
        if car.get("result_status") not in (2, 3, None):
            return f"{n} non è più in gara."
        bits = [f"{n} è P{car['position']}"]  # il nome deve restare in testa (lo usa command)
        g = self.gap(me, idx)
        if g is not None and abs(g) >= 0.05:
            bits[0] += f", a {self.c.gap_text(abs(g))} {'davanti' if g > 0 else 'dietro'}"
        if car["pit_status"]:
            bits.append("è ai box adesso")
        t = self.r.tyres.get(idx)
        if t and t.get("compound") and not short:
            bits.append(f"{t['compound']} di {plural(t['tyre_age'], 'giro', 'giri')}")
        st = car.get("pit_stops") or 0
        if not short:
            bits.append("non si è ancora fermato" if st == 0 else plural(st, "sosta", "soste"))
        return ", ".join(bits) + "."

    # ------------------------------------------------------------ in diretta
    def on_laps(self, me):
        car = self.car()
        if not car or not me:
            return
        say, n = self.c.say, self.r.name(self.idx)
        if car.get("result_status") not in (2, 3, None) or self.idx in self.r.retired:
            if not self.out:
                self.out = True
                if self.idx not in self.r.retired:     # se è un ritiro l'ha già detto la gara
                    say(f"{n[0].upper() + n[1:]} è fuori dalla gara. Smetto di seguirlo.", "seguito", level=1)
                self.idx = None
            return
        if self.stops is None:
            self.stops = car.get("pit_stops") or 0
        pit = car["pit_status"]
        if pit and not self.prev_pit and not self.in_pit:
            self.pit_in = self.c.now
        if self.pit_in is not None and pit and not self.in_pit and self.c.now - self.pit_in >= self.PIT_CONFIRM_S:
            self.in_pit = True
            say(f"{n[0].upper() + n[1:]} è entrato ai box.", "seguito", level=1, urgent=True)
        if not pit and self.prev_pit:
            self.pit_in = None
            stops = car.get("pit_stops") or 0
            if self.in_pit or stops > self.stops:
                t = self.r.tyres.get(self.idx) or {}
                txt = f"{n[0].upper() + n[1:]} è uscito dai box"
                if t.get("compound"):
                    txt += f" con le {t['compound']}"
                txt += f", rientra P{car['position']}"
                g = self.gap(me)
                if g is not None:
                    txt += f", a {self.c.gap_text(abs(g))} {'davanti' if g > 0 else 'dietro'} di te"
                say(txt + ".", "seguito", level=1, urgent=True)
            self.in_pit = False
            self.stops = stops
        self.prev_pit = pit

    def on_lap(self, me, remaining):
        """A fine giro: un aggiornamento ogni EVERY giri, con la tendenza."""
        car = self.car()
        if not car or not me or car["pit_status"] or me["pit_status"]:
            return
        lap = me["lap_num"]
        g = self.gap(me)
        stint = (car.get("pit_stops") or 0, me["pit_stops"])
        if g is not None:
            self.gaps[lap] = (g, stint)
        if lap - self.said_lap < self.EVERY or remaining == 1:
            return
        self.said_lap = lap
        txt = self.status()
        old, old_stint = self.gaps.get(lap - self.EVERY) or (None, None)
        if g is not None and old is not None and old_stint == stint and (old > 0) == (g > 0):
            per = (abs(old) - abs(g)) / self.EVERY      # positivo: il distacco si riduce
            if g > 0 and per >= 0.1:
                txt += f" Recuperi {tenths(per)} al giro."
            elif g > 0 and per <= -0.1:
                txt += f" Perdi {tenths(-per)} al giro."
            elif g < 0 and per >= 0.1:
                txt += f" Si avvicina di {tenths(per)} al giro."
            elif g < 0 and per <= -0.1:
                txt += f" Lo stai staccando di {tenths(-per)} al giro."
        self.c.say(txt[0].upper() + txt[1:], "seguito", level=2)

    def snapshot(self):
        car, me = self.car(), self.r.me()
        if not car:
            return None
        t = self.r.tyres.get(self.idx) or {}
        g = self.gap(me)
        return {"name": self.r.name(self.idx, voice=False), "pos": car["position"],
                "gap": None if g is None else round(g, 1), "compound": t.get("compound"),
                "age": t.get("tyre_age"), "stops": car.get("pit_stops") or 0, "pit": bool(car["pit_status"])}
