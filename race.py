"""Ingegnere di gara: distacchi, avversari, box, safety car, meteo, strategia gomme.

Funziona anche online. Per i giocatori umani l'ingegnere usa il nome che vedi nella
classifica a sinistra (l'ID online), ripulito per la voce. Se il giocatore non ha
attivato "Mostra nomi online", il gioco non manda il nome e l'ingegnere dice
"il numero 16 della Ferrari"; se manca anche quello, "la P4".
"""
import re

from distacchi import GapTimer
from parlato import num, plural, tenths
from strategia import Strategy

# nomi generici che il gioco manda al posto dell'ID online nascosto (varie lingue)
GENERIC = re.compile(r"^(player|giocatore|joueur|spieler|jugador|jogador|gracz|igrok|игрок)\s*\d*$", re.I)

# squadre: solo quelle con lo stesso id e lo stesso nome nelle stagioni 2025 e 2026
TEAMS = {0: "Mercedes", 1: "Ferrari", 2: "Red Bull", 3: "Williams", 4: "Aston Martin",
         5: "Alpine", 6: "Racing Bulls", 7: "Haas", 8: "McLaren"}


def cap(t):
    return t[:1].upper() + t[1:]


def prep(word, name):
    """Preposizione articolata giusta: prep("di", "il numero 16") -> "del numero 16"."""
    forms = {"di": ("del", "della"), "su": ("sul", "sulla"), "a": ("al", "alla"), "da": ("dal", "dalla")}
    if name.startswith("il "):
        return f"{forms[word][0]} {name[3:]}"
    if name.startswith("la "):
        return f"{forms[word][1]} {name[3:]}"
    return f"{word} {name}"


def spoken_name(raw):
    """Nome online leggibile dalla voce: 'xX_Marco_Rossi99_Xx' -> 'Marco Rossi'."""
    n = raw.strip()
    n = re.sub(r"^(?:[xX]{1,2}[_\-. ]+)+|(?:[_\-. ]+[xX]{1,2})+$", "", n)   # xX_ ... _Xx
    n = re.sub(r"(?<=[a-zà-ù])(?=[A-Z])", " ", n)                            # MarcoRossi -> Marco Rossi
    n = re.sub(r"[_\-.|~*]+", " ", n)
    n = re.sub(r"(?<=[A-Za-zÀ-ù])\d{3,}\b|\b\d{3,}(?=[A-Za-zÀ-ù])", "", n)  # numeri lunghi attaccati
    n = re.sub(r"(?<=[a-zà-ù]{2})(?=\d)", " ", n)                           # Rossi99 -> Rossi 99
    n = re.sub(r"[^\w' ]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n if re.search(r"[A-Za-zÀ-ù]", n) else raw.strip()

SLICKS = {16, 17, 18}

DRS_GAP = 1.0          # sotto questo distacco chi ti segue ha il DRS
CLOSE_CONFIRM_S = 2.0  # deve restare sotto il secondo almeno per questo tempo prima dell'avviso

# infrazioni (Event "PENA", campo infringementType) dette in breve
TRACK_LIMITS = {7, 8, 9, 25, 26, 27, 28, 29}
INFRINGEMENTS = {
    3: "collisione", 4: "contatto", 5: "posizione non restituita", 6: "posizioni non restituite",
    7: "taglio di curva", 8: "sorpasso tagliando la curva", 9: "sorpassi tagliando la curva",
    10: "linea di uscita box", 11: "bandiere blu", 12: "bandiere gialle", 17: "velocità in pit lane",
    25: "taglio di curva", 26: "uscita di pista", 27: "limiti della pista", 28: "limiti della pista",
    29: "limiti della pista", 34: "partenza anticipata", 35: "contatto con la safety car",
    36: "sorpasso in regime di safety car", 37: "troppo veloce dietro la safety car",
    38: "troppo veloce in virtual safety car", 43: "troppo lontano dalla safety car", 52: "vantaggio irregolare",
}


class RaceEngineer:
    def __init__(self, coach):
        self.c = coach
        self.reset()

    def reset(self):
        self.names = {}
        self.by_idx = {}
        self.cars = []
        self.tyres = {}
        self.prev_pit = {}
        self.last_sc = 0
        self.last_weather = None
        self.rain_warn = (None, -1e9)
        self.announced = set()
        self.gap_hist = {}            # giro -> {"ahead": (idx, gap), "behind": (idx, gap)}
        self.wear_hist = []           # (giro, usura massima)
        self.my_stops = None
        self.pit_exit_pending = False
        self.drs_warned_lap = -10
        self.close_since = None       # (idx di chi ti segue, da quando è sotto il secondo)
        self.timer = GapTimer()
        self.xz = {}                  # posizione x, z di ogni auto, per la mini mappa
        self.yellow_said = set()      # (giro, settore) delle gialle già annunciate
        self.stint = []               # (giro, tempo s) dei giri puliti con le gomme di adesso
        self.traffic_said = set()
        self.undercut_said = set()
        self.fia_flag = 0
        self.blue_t = -1e9
        self.wear_warned_lap = -10
        self.pos_candidate = (None, 0.0)
        self.pos_announced = None
        self.warnings = 0
        self.strategy = Strategy(self)

    # ---------------------------------------------------------- utilità
    @property
    def is_race(self):
        return bool(self.c.session.get("is_race"))

    def me(self):
        return self.by_idx.get(self.c.player_idx)

    def rel(self, offset):
        me = self.me()
        if not me:
            return None
        want = me["position"] + offset
        return next((c for c in self.cars if c["position"] == want), None)

    def gap_between(self, front, back):
        """Distacco in secondi tra due auto: quello calcolato da noi, se c'è, altrimenti quello del gioco."""
        g = self.timer.gap(front["idx"], back["idx"])
        return g if g is not None else back["delta_front_ms"] / 1000

    def gap_ahead(self):
        me, car = self.me(), self.rel(-1)
        return self.gap_between(car, me) if me and car else None

    def gap_behind(self):
        me, car = self.me(), self.rel(1)
        return self.gap_between(me, car) if me and car else None

    def name(self, idx, voice=True):
        """Come chiamare un pilota. voice=False: nome completo per la dashboard."""
        p = self.names.get(idx) or {}
        n = p.get("name", "")
        if n and not GENERIC.match(n) and (p.get("ai") or p.get("show_name", True)):
            if p.get("ai"):                       # piloti AI: "Charles LECLERC" -> "Leclerc"
                return n.split()[-1].title()
            return spoken_name(n) if voice else n  # giocatori: il nome della classifica
        team, number = TEAMS.get(p.get("team")), p.get("number")
        if number and team:
            return f"il numero {number} della {team}" if voice else f"#{number} {team}"
        if number:
            return f"il numero {number}" if voice else f"#{number}"
        car = self.by_idx.get(idx)
        return f"la P{car['position']}" if car else "un avversario"

    def _in_window(self, me):
        s = self.c.session
        return s.get("pit_ideal", 0) > 0 and s["pit_ideal"] - 1 <= me["lap_num"] <= max(s["pit_latest"], s["pit_ideal"])

    def _once(self, key):
        if key in self.announced:
            return False
        self.announced.add(key)
        return True

    def _wear(self):
        w = self.c.damage.get("tyre_wear")
        return max(w) if w else None

    # ----------------------------------------------------------- ingressi
    def on_participants(self, names):
        self.names = names

    def on_event(self, ev):
        say, me_idx = self.c.say, self.c.player_idx
        code = ev["code"]
        if code == "RDFL":
            say("Bandiera rossa! Rallenta e rientra ai box.", "gara", level=1, urgent=True)
            return
        if code == "FTLP" and self.is_race and ev["car"] != me_idx and ev["time"] > 0:
            m, sec = divmod(ev["time"], 60)
            say(f"Giro veloce {prep('di', self.name(ev['car']))}: {int(m)} e {num(sec)}.", "gara", level=3)
            return
        if code != "PENA" or ev["car"] != me_idx:
            return
        what = INFRINGEMENTS.get(ev["infringement"], "infrazione")
        t = ev["type"]
        if t == 5:                                       # avvertimento
            self.warnings += 1
            if ev["infringement"] in TRACK_LIMITS:
                n = self.warnings
                txt = (f"Avvertimento per {what}. " +
                       ("È il primo." if n == 1 else f"Sono {n} avvertimenti."))
                if n >= 2:
                    txt += " Attento ai limiti della pista, al prossimo rischi una penalità."
            else:
                txt = f"Avvertimento per {what}."
            say(txt, "avviso", level=1, urgent=True)
        elif t == 4:
            say(f"Penalità di {ev['time']} secondi per {what}. Verrà aggiunta al tuo tempo.", "avviso", level=1, urgent=True)
        elif t == 0:
            say(f"Drive through per {what}. Devi scontarlo entro tre giri.", "avviso", level=1, urgent=True)
        elif t == 1:
            say(f"Stop and go per {what}. Devi scontarlo entro tre giri.", "avviso", level=1, urgent=True)
        elif t == 3:
            say("Ricorda: hai una penalità da scontare ai box.", "avviso", level=1, urgent=True)
        elif t == 9:
            say("Attenzione alle regole sulle gomme: devi montare un'altra mescola.", "avviso", level=1, urgent=True)
        elif t == 6:
            say("Squalificato.", "avviso", level=1, urgent=True)
        elif t == 17:
            say("Bandiera nera in arrivo: smetti di tagliare e restituisci le posizioni.", "avviso", level=1, urgent=True)

    def on_motion_all(self, xz):
        self.xz = xz

    def dots(self):
        """Te, chi ti precede e chi ti segue sulla mini mappa."""
        out = {}
        me = self.me()
        idx = self.c.player_idx
        if idx in self.xz:
            out["me"] = [round(v, 1) for v in self.xz[idx]]
        if me and self.is_race:
            for key, off in (("ahead", -1), ("behind", 1)):
                car = self.rel(off)
                if car and car["idx"] in self.xz:
                    out[key] = [round(v, 1) for v in self.xz[car["idx"]]]
        return out

    def on_status_all(self, tyres):
        self.tyres = tyres

    def on_laps(self, cars):
        self.timer.update(cars, self.c.now)
        self.cars = cars
        self.by_idx = {c["idx"]: c for c in cars}
        me = self.me()
        if not me or not self.is_race:
            self.prev_pit = {c["idx"]: c["pit_status"] for c in cars}
            if me and self.c.session.get("session_type") != 18:
                self._traffic(me)
            return
        say = self.c.say

        # avversari vicini che rientrano ai box
        for off in (-1, 1):
            car = self.rel(off)
            if not car:
                continue
            if car["pit_status"] == 1 and self.prev_pit.get(car["idx"], 1) == 0 and me["pit_status"] == 0:
                where = "davanti" if off < 0 else "dietro"
                msg = f"{cap(self.name(car['idx']))}, {where} a te, è entrato ai box."
                deg, age = self.degradation(), self.c.status.get("tyre_age") or 0
                if off < 0 and deg is not None and deg * age < 0.8 and me["pit_stops"] == car["pit_stops"]:
                    msg += " Le tue gomme tengono ancora: resta fuori e spingi, proviamo l'overcut."
                elif self._in_window(me) and me["pit_stops"] == 0:
                    msg += " Possiamo fermarci al prossimo giro per coprirlo."
                say(msg, "gara", level=1, urgent=True)
        self.prev_pit = {c["idx"]: c["pit_status"] for c in cars}

        # chi ti segue entro un secondo: l'avviso parte solo se resta sotto il secondo
        # per almeno 2 secondi di fila e la tua posizione non è appena cambiata
        # (durante un sorpasso i distacchi non hanno senso)
        behind = self.rel(1)
        stable = self.pos_candidate[0] == me["position"] and self.c.now - self.pos_candidate[1] >= 3
        if behind and me["pit_status"] == 0 and behind["pit_status"] == 0 and me["lap_num"] > 1 and stable:
            gap = self.gap_between(me, behind)
            if 0 < gap < DRS_GAP:
                if not self.close_since or self.close_since[0] != behind["idx"]:
                    self.close_since = (behind["idx"], self.c.now)
                elif (self.c.now - self.close_since[1] >= CLOSE_CONFIRM_S
                      and me["lap_num"] - self.drs_warned_lap >= 2):
                    self.drs_warned_lap = me["lap_num"]
                    say(f"{cap(self.name(behind['idx']))} è a {num(gap)}, dentro il secondo. "
                        "Difendi in uscita dalle curve lente.", "gara", level=1, urgent=True)
            else:
                self.close_since = None
        else:
            self.close_since = None

        # il tuo pit stop
        if self.my_stops is None:
            self.my_stops = me["pit_stops"]
        if me["pit_stops"] > self.my_stops:
            self.my_stops = me["pit_stops"]
            self.pit_exit_pending = True
        if self.pit_exit_pending and me["pit_status"] == 0:
            self.pit_exit_pending = False
            self.wear_hist = []
            say(f"Pit fatto. Rientri in posizione {me['position']}.", "gara", level=1, urgent=True)

        # cambi di posizione (stabili da 3 secondi, per ignorare i sorpassi a metà)
        pos = me["position"]
        cand, since = self.pos_candidate
        if pos != cand:
            self.pos_candidate = (pos, self.c.now)
        elif self.c.now - since >= 3 and pos != self.pos_announced:
            if self.pos_announced is not None and me["pit_status"] == 0:
                if pos < self.pos_announced:
                    say(f"Bel sorpasso, sei P{pos}.", "gara", level=2)
                else:
                    say(f"Hai perso una posizione, sei P{pos}.", "gara", level=2)
            self.pos_announced = pos

    # ------------------------------------------------------------- bandiere
    def _flags(self, s):
        """Bandiera gialla nei prossimi 600 metri (dai settori della pista mandati dal gioco)."""
        zones, me = s.get("zones") or [], self.me()
        length = s.get("track_length") or 0
        if not zones or not me or not length or me["pit_status"] != 0 or me["lap_distance"] < 0:
            return
        frac = (me["lap_distance"] % length) / length
        for i, (start, flag) in enumerate(zones):
            if flag != 3:
                continue
            end = zones[i + 1][0] if i + 1 < len(zones) else 1.0
            inside = start <= frac < end
            ahead_m = ((start - frac) % 1.0) * length
            if not inside and ahead_m > 600:
                continue
            key = (me["lap_num"], i)
            if key in self.yellow_said:
                continue
            self.yellow_said.add(key)
            where = "in questo tratto" if inside else f"tra {int(round(ahead_m, -1))} metri"
            self.c.say(f"Bandiera gialla {where}! Rallenta e niente sorpassi.", "avviso", level=1, urgent=True)

    def on_fia_flag(self, flag):
        """Bandiera mostrata alla tua auto: la blu vuol dire che arriva qualcuno che ti doppia."""
        if flag == 2 and self.fia_flag != 2 and self.c.now - self.blue_t > 20:
            self.blue_t = self.c.now
            self.c.say("Bandiera blu: fai passare chi arriva, resta sulla traiettoria e alza in rettilineo.",
                       "avviso", level=1, urgent=True)
        self.fia_flag = flag

    def on_session(self, s):
        say = self.c.say
        me = self.me()
        if s.get("session_type") != 18:          # niente bandiere in time trial
            self._flags(s)
        # safety car
        sc = s.get("safety_car", 0)
        if sc != self.last_sc and self.is_race:
            if sc in (1, 2):
                msg = "Safety car in pista!" if sc == 1 else "Virtual safety car, rispetta il delta."
                advice = self.strategy.sc_advice(sc)
                msg += " " + (advice or "Rispetta il delta e tieni calde le gomme.")
                say(msg, "strategia", level=1, urgent=True)
            elif sc == 0 and self.last_sc == 1:
                say("La safety car rientra a fine giro. Preparati alla ripartenza.", "gara", level=1, urgent=True)
            elif sc == 0 and self.last_sc == 2:
                say("Fine virtual safety car, via!", "gara", level=1, urgent=True)
        self.last_sc = sc

        # meteo
        w = s.get("weather", 0)
        comp = self.c.status.get("compound_id")
        slick = comp in SLICKS if comp is not None else True
        if w < 3:
            nxt = next((f for f in s.get("forecast", []) if f["min"] > 0 and (f["weather"] >= 3 or f["rain"] >= 50)), None)
            last_min, last_t = self.rain_warn
            if nxt and (last_min is None or abs(nxt["min"] - last_min) >= 10 or self.c.now - last_t > 600):
                self.rain_warn = (nxt["min"], self.c.now)
                say(f"Meteo: pioggia prevista tra {nxt['min']} minuti, probabilità {nxt['rain']} per cento.",
                    "meteo", level=1, urgent=True)
        if self.last_weather is not None:
            if w >= 3 and self.last_weather < 3 and slick:
                say("Ha iniziato a piovere. Con le slick valuta le intermedie.", "meteo", level=1, urgent=True)
            if w <= 1 and self.last_weather >= 3 and not slick:
                say("La pista si sta asciugando: presto le slick saranno più veloci.", "meteo", level=1, urgent=True)
        self.last_weather = w

    def on_lap_complete(self):
        """Chiamato dal coach quando il pilota passa sul traguardo."""
        me = self.me()
        if not me or not self.is_race:
            return
        s, say = self.c.session, self.c.say
        lap = me["lap_num"]            # giro appena iniziato
        total = s.get("total_laps", 0)
        remaining = total - lap + 1 if total else None

        if self.c.race_done:            # bandiera a scacchi
            say(f"Bandiera a scacchi! Hai chiuso P{me['position']}. Bella gara.", "gara", level=1, urgent=True)
            return
        if remaining is not None and remaining <= 0:
            return                      # gara finita
        if remaining == 1:
            say(f"Ultimo giro, sei P{me['position']}. Dai tutto!", "gara", level=1, urgent=True)

        # distacchi e tendenza
        hist = {}
        first = f"P{me['position']}"
        if remaining and remaining > 1:
            first += f", mancano {remaining} giri"
        parts = [first]
        prev = self.gap_hist.get(lap - 1, {})
        ahead, behind = self.rel(-1), self.rel(1)
        if ahead and me["pit_status"] == 0:
            gap = self.gap_between(ahead, me)
            hist["ahead"] = (ahead["idx"], gap)
            txt = f"davanti {self.name(ahead['idx'])} a {num(gap)}"
            p = prev.get("ahead")
            if p and p[0] == ahead["idx"]:
                diff = p[1] - gap
                if diff >= 0.05:
                    txt += f", recuperi {tenths(diff)} al giro"
                elif diff <= -0.05:
                    txt += f", perdi {tenths(diff)} al giro"
            t = self.tyres.get(ahead["idx"])
            mine = self.c.status.get("compound")
            if t and t.get("compound") and mine and t["compound"] != mine:
                txt += f", lui ha le {t['compound']} di {plural(t['tyre_age'], 'giro', 'giri')}"
            parts.append(txt)
        if behind and behind["pit_status"] == 0:
            gap = self.gap_between(me, behind)
            hist["behind"] = (behind["idx"], gap)
            txt = f"dietro {self.name(behind['idx'])} a {num(gap)}"
            p = prev.get("behind")
            if p and p[0] == behind["idx"]:
                diff = p[1] - gap
                if diff >= 0.05:
                    txt += f" e si avvicina di {tenths(diff)}"
            parts.append(txt)
        self.gap_hist[lap] = hist
        if remaining != 1:
            say(". ".join(p[0].upper() + p[1:] for p in parts) + ".", "gara", level=2)

        # finestra pit
        ideal, latest = s.get("pit_ideal", 0), s.get("pit_latest", 0)
        if ideal and me["pit_stops"] == 0:
            if lap == ideal and self._once(("apre", ideal)):
                est = self.rejoin_estimate(self.c.pit_loss)
                where = f" Se ti fermi ora rientri circa P{est['pos']}." if est else ""
                say(f"Siamo nella finestra pit: box quando vuoi entro il giro {latest}.{where}", "strategia", level=1, urgent=True)
            elif lap == latest and not (self.strategy.plan() or {}).get("compound") and self._once(("chiude", latest)):
                say("Ultimo giro utile per la sosta. Box, box!", "strategia", level=1, urgent=True)

        # usura gomme: stima dei giri rimasti prima del 70%
        wear = self._wear()
        if wear is not None:
            self.wear_hist.append((lap, wear))
            self.wear_hist = self.wear_hist[-4:]
            if len(self.wear_hist) >= 3:
                (l0, w0), (l1, w1) = self.wear_hist[0], self.wear_hist[-1]
                rate = (w1 - w0) / max(1, l1 - l0)
                if rate > 0.3 and wear < 70:
                    left = (70 - wear) / rate
                    if left <= 3 and (remaining is None or left < remaining) and lap - self.wear_warned_lap >= 2:
                        self.wear_warned_lap = lap
                        say(f"Gomme al limite tra circa {max(1, round(left))} giri. Prepariamo la sosta.",
                            "strategia", level=1, urgent=True)

        self._stint_lap(me, lap)
        self._undercut(me, remaining)
        self.strategy.on_lap(me, remaining)

    # ------------------------------------------------------ traffico (prove e qualifica)
    def _traffic(self, me):
        """Giro di lancio con qualcuno vicino davanti, o auto lenta davanti durante il giro veloce.

        Stato del pilota dal gioco: 1 giro veloce, 2 giro di rientro, 3 giro di uscita, 4 in pista.
        """
        length = self.c.session.get("track_length") or 0
        v = (self.c.telemetry.get("speed") or 0) / 3.6
        if not length or v < 15 or me["pit_status"] != 0:
            return
        my_d = me["lap_distance"] % length
        status = me.get("driver_status")
        out_lap_end = status == 3 and my_d > length - 900
        if not (out_lap_end or status == 1):
            return
        best = None
        for c in self.cars:
            if c["idx"] == me["idx"] or c["pit_status"] != 0 or c.get("result_status") not in (2, None):
                continue
            ahead = (c["lap_distance"] % length - my_d) % length
            if ahead <= 5:
                continue
            secs = ahead / v
            if best is None or secs < best[0]:
                best = (secs, c)
        if not best:
            return
        secs, car = best
        if out_lap_end and secs < 2.5:
            key = ("lancio", me["lap_num"])
            if key not in self.traffic_said:
                self.traffic_said.add(key)
                self.c.say(f"Traffico: {self.name(car['idx'])} è a {num(round(secs, 1))} secondi davanti. "
                           "Lascia spazio prima di lanciare il giro.", "avviso", level=1, urgent=True)
        elif status == 1 and secs < 3.0 and car.get("driver_status") in (2, 3):
            key = ("lenta", me["lap_num"], car["idx"])
            if key not in self.traffic_said:
                self.traffic_said.add(key)
                self.c.say(f"Auto lenta davanti tra {num(round(secs))} secondi: {self.name(car['idx'])}, "
                           f"{'nel giro di rientro' if car.get('driver_status') == 2 else 'nel giro di uscita'}.",
                           "avviso", level=1, urgent=True)

    # -------------------------------------------------------- undercut / overcut
    def _stint_lap(self, me, lap):
        """Tempi dei giri puliti dello stint: servono a stimare quanto calano le gomme."""
        h = self.c.history[-1] if self.c.history else None
        if self.pit_exit_pending or me["pit_status"] != 0:
            self.stint = []
            return
        if h and h["valid"] and lap > 2 and not self.c.session.get("safety_car"):
            self.stint.append((lap - 1, h["ms"] / 1000))
            self.stint = self.stint[-8:]

    def degradation(self):
        """Secondi persi per giro dalle gomme (pendenza dei tempi dello stint), oppure None."""
        if len(self.stint) < 3:
            return None
        xs, ys = [p[0] for p in self.stint], [p[1] for p in self.stint]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        den = sum((x - mx) ** 2 for x in xs)
        if not den:
            return None
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        return max(0.0, min(0.4, slope))

    def undercut_gain(self):
        """Quanto guadagneresti fermandoti prima di chi hai davanti: gomme nuove meno il riscaldamento."""
        deg, age = self.degradation(), self.c.status.get("tyre_age") or 0
        if deg is None or age < 6:
            return None
        return deg * age * 1.5 - 0.8

    def _undercut(self, me, remaining):
        gain = self.undercut_gain()
        if gain is None or gain < 0.5 or (remaining is not None and remaining < 5):
            return
        wear = self._wear() or 0
        window = self._in_window(me) or wear >= 45
        if not window:
            return
        ahead, behind = self.rel(-1), self.rel(1)
        if (ahead and ahead["pit_status"] == 0 and ahead["pit_stops"] == me["pit_stops"]
                and ("ahead", ahead["idx"], me["pit_stops"]) not in self.undercut_said):
            gap = self.gap_between(ahead, me)
            if 0 < gap < gain:
                self.undercut_said.add(("ahead", ahead["idx"], me["pit_stops"]))
                self.c.say(f"Undercut possibile {prep('su', self.name(ahead['idx']))}: è a {num(gap)} e con le gomme nuove "
                           f"guadagni circa {num(round(gain, 1))}. Se ti fermi ora e lui resta fuori, gli esci davanti.",
                           "strategia", level=1, urgent=True)
        if (behind and behind["pit_status"] == 0 and behind["pit_stops"] == me["pit_stops"]
                and ("behind", behind["idx"], me["pit_stops"]) not in self.undercut_said):
            gap = self.gap_between(me, behind)
            if 0 < gap < gain:
                self.undercut_said.add(("behind", behind["idx"], me["pit_stops"]))
                self.c.say(f"Attento all'undercut {prep('di', self.name(behind['idx']))}: è a {num(gap)}. "
                           "Se si ferma prima di te può passarti, al suo pit ti fermi anche tu per coprirlo.",
                           "strategia", level=1, urgent=True)

    # ------------------------------------------------- stima rientro dal pit
    def rejoin_estimate(self, pit_loss=22.0):
        """Dove rientri se ti fermi adesso. Usa la stima del gioco se c'è, altrimenti i distacchi."""
        me = self.me()
        if not me or not self.is_race:
            return None
        my_t = me["delta_leader_ms"] / 1000 if me["position"] > 1 else 0.0
        target = my_t + pit_loss
        others = [c for c in self.cars if c["idx"] != me["idx"] and c["result_status"] in (2, 3)]
        def t_of(c):
            return 0.0 if c["position"] == 1 else c["delta_leader_ms"] / 1000
        ahead = [c for c in others if t_of(c) <= target]
        behind = [c for c in others if t_of(c) > target]
        out = {"pos": len(ahead) + 1, "loss": pit_loss, "source": "distacchi"}
        game = self.c.session.get("pit_rejoin", 0)
        if game:
            out["pos"], out["source"] = game, "gioco"
        if ahead:
            a = max(ahead, key=t_of)
            out["ahead"] = {"name": self.name(a["idx"]), "gap": round(target - t_of(a), 1)}
        if behind:
            b = min(behind, key=t_of)
            out["behind"] = {"name": self.name(b["idx"]), "gap": round(t_of(b) - target, 1)}
        return out

    # ---------------------------------------------------- per dashboard e AI
    def snapshot(self):
        me = self.me()
        s = self.c.session
        out = {"is_race": self.is_race, "session_name": s.get("session_name"),
               "weather": s.get("weather"), "track_temp": s.get("track_temp"),
               "air_temp": s.get("air_temp"), "safety_car": s.get("safety_car", 0),
               "pit_ideal": s.get("pit_ideal"), "pit_latest": s.get("pit_latest"),
               "total_laps": s.get("total_laps")}
        nxt = next((f for f in s.get("forecast", []) if f["min"] > 0 and (f["weather"] >= 3 or f["rain"] >= 50)), None)
        out["rain_next"] = nxt
        if me:
            out["position"] = me["position"]
            out["pit_stops"] = me["pit_stops"]
            out["cars"] = len(self.cars)
            for key, off in (("ahead", -1), ("behind", 1)):
                car = self.rel(off)
                if car:
                    gap = self.gap_between(car, me) if off < 0 else self.gap_between(me, car)
                    t = self.tyres.get(car["idx"], {})
                    out[key] = {"name": self.name(car["idx"], voice=False), "gap": round(gap, 1),
                                "compound": t.get("compound"), "tyre_age": t.get("tyre_age"),
                                "pit": car["pit_status"] > 0}
        return out

    def context(self):
        snap = self.snapshot()
        snap["storico_distacchi"] = {
            g: {k: {"pilota": self.name(v[0]), "distacco_s": round(v[1], 2)} for k, v in h.items()}
            for g, h in list(self.gap_hist.items())[-6:]}
        snap["usura_per_giro"] = self.wear_hist
        snap["rientro_se_ti_fermi_ora"] = self.rejoin_estimate(self.c.pit_loss)
        return snap
