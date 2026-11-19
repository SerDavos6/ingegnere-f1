"""Ingegnere di gara: distacchi, avversari, box, safety car, meteo, strategia gomme.

Funziona anche online. Per i giocatori umani l'ingegnere usa il nome che vedi nella
classifica a sinistra (l'ID online), ripulito per la voce. Se il giocatore non ha
attivato "Mostra nomi online", il gioco non manda il nome e l'ingegnere dice
"il numero 16 della Ferrari"; se manca anche quello, "la P4".
"""
import re

from distacchi import GapTimer
from parlato import fmt_lap_voice, num, plural, tenths
from amico_live import FriendWatch
from squadra import TeamPlay
from decisioni import Decisions
from segui import Follow
from tattica import Tactics
from qualifica import QualiBoard
from strategia import Strategy

# nomi generici che il gioco manda al posto dell'ID online nascosto (varie lingue)
GENERIC = re.compile(r"^(player|giocatore|joueur|spieler|jugador|jogador|gracz|igrok|игрок)\s*\d*$", re.I)

# squadre: solo quelle con lo stesso id e lo stesso nome nelle stagioni 2025 e 2026
TEAMS = {0: "Mercedes", 1: "Ferrari", 2: "Red Bull", 3: "Williams", 4: "Aston Martin",
         5: "Alpine", 6: "Racing Bulls", 7: "Haas", 8: "McLaren", 9: "Sauber",
         # auto 2026 (2026 Season Pack), visti in una gara vera
         220: "Mercedes", 221: "Ferrari", 222: "Red Bull", 223: "Williams", 224: "Aston Martin",
         225: "Alpine", 226: "Racing Bulls", 227: "Haas", 228: "McLaren", 229: "Audi", 230: "Cadillac"}


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

SLICKS = {16, 17, 18, 19, 20, 21, 22}     # F1 e F2
PIT_CONFIRM_S = 3.0                         # secondi di attesa prima di dire "è entrato ai box"
RETIRED_STATUS = {4, 5, 6, 7}               # result status: non finisce, squalificato, non classificato, ritirato
RETIRE_REASONS = {3: " per i danni", 8: " per un guasto"}     # motivo dell'evento ritiro (6 = squalifica)

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
        self.pit_pending = {}         # idx -> (quando è entrato, davanti/dietro): lo dico solo se non si ritira
        self.retired = set()          # auto ritirate già annunciate
        self.prev_pos = {}
        self.retire_t = -1e9          # ultimo ritiro: la posizione guadagnata non è un sorpasso
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
        self.mate_close = {}          # compagno -> giro dell'ultimo avviso "è vicino"
        self.start_grid = None        # posizione di partenza (a fine gara il gioco a volte manda 0)
        self.strategy = Strategy(self)
        self.friend = FriendWatch(self)
        self.team = TeamPlay(self)
        self.dec = Decisions(self)
        self.follow = Follow(self)
        self.tactics = Tactics(self)
        self.quali = QualiBoard(self)

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
        mate = self.mate_name(idx) if voice else None
        if mate:
            return f"{self.mate_word(idx)} {mate}"
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

    def mates(self):
        """Amici del server nella stessa gara: [(nome, indice auto)]."""
        try:
            return [(n, i) for n, i in self.c.teammates() if i in self.by_idx and i != self.c.player_idx]
        except Exception:
            return []

    def mate_name(self, idx):
        return next((n for n, i in self.mates() if i == idx), None)

    def same_team(self, idx):
        """True solo se l'amico guida davvero per la tua stessa squadra nel gioco."""
        mine = (self.names.get(self.c.player_idx) or {}).get("team")
        theirs = (self.names.get(idx) or {}).get("team")
        return mine is not None and mine == theirs and mine != 255

    def mate_word(self, idx):
        """'il tuo compagno' se siete nella stessa squadra, altrimenti 'il tuo amico'."""
        return "il tuo compagno" if self.same_team(idx) else "il tuo amico"

    def _wear(self):
        w = self.c.damage.get("tyre_wear")
        return max(w) if w else None

    # ----------------------------------------------------------- ingressi
    def on_participants(self, names):
        self.names = names

    def corner_here(self):
        """La curva dove sei adesso (per gli avvisi sui limiti della pista)."""
        c = self.c
        d = (c.last_lapdata or {}).get("lap_distance")
        if d is None or not c.ref:
            return None
        for z in c.ref.zones:
            if z["brake"] - 120 <= d <= z["exit"] + 200:
                return c.zone_label(z)
        return None

    def on_event(self, ev):
        say, me_idx = self.c.say, self.c.player_idx
        code = ev["code"]
        if code == "RTMT":
            if ev["car"] != me_idx:
                self._retired(ev["car"], ev.get("reason", 0))
            return
        if code == "LGOT":
            self.dec.on_lights_out()
            return
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
                where = self.corner_here()
                txt = (f"Avvertimento per {what}{' in ' + where if where else ''}. " +
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

    def _retired(self, idx, reason):
        """Un avversario si è ritirato: lo dico una volta sola, con il motivo se il gioco lo manda."""
        if idx in self.retired or not self.is_race:
            return
        self.retired.add(idx)
        self.pit_pending.pop(idx, None)
        me_idx = self.c.player_idx
        car_pos = self.prev_pos.get(idx) or (self.by_idx.get(idx) or {}).get("position")
        me_pos = self.prev_pos.get(me_idx) or (self.me() or {}).get("position")
        self.retire_t = self.c.now
        n = self.name(idx)
        if reason == 6:
            msg = f"{cap(n)} è stato squalificato."
        else:
            msg = self.c.frasi("ritiro", nome=n, Nome=cap(n), why=RETIRE_REASONS.get(reason, ""))
        near = False
        if car_pos and me_pos:
            if car_pos < me_pos:
                msg += " Era davanti a te: guadagni una posizione." if car_pos == me_pos - 1 else " Guadagni una posizione."
                near = car_pos == me_pos - 1
            elif car_pos == me_pos + 1:
                msg += " Era dietro di te."
                near = True
        self.c.say(msg, "gara", level=1 if near else 2, urgent=near)

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
            out["mates"] = [[round(v, 1) for v in self.xz[i]] for _, i in self.mates()
                            if i in self.xz and self.by_idx.get(i) not in (self.rel(-1), self.rel(1))]
        return out

    def on_status_all(self, tyres):
        self.tyres = tyres

    def on_laps(self, cars):
        self.timer.update(cars, self.c.now)
        # posizioni del giro di dati precedente: quando uno si ritira la classifica cambia subito
        self.prev_pos = {c["idx"]: c["position"] for c in self.cars} or self.prev_pos
        self.cars = cars
        self.by_idx = {c["idx"]: c for c in cars}
        if self.c.is_quali:
            self.quali.on_laps(cars)
        me = self.me()
        if not me or not self.is_race:
            self.prev_pit = {c["idx"]: c["pit_status"] for c in cars}
            if me and self.c.session.get("session_type") != 18:
                self._traffic(me)
            return
        say = self.c.say

        # ritiri visti dallo stato di gara (se l'evento del gioco non è arrivato)
        for c in cars:
            if c["idx"] != me["idx"] and c.get("result_status") in RETIRED_STATUS:
                self._retired(c["idx"], 6 if c["result_status"] == 5 else 0)

        # avversari vicini che rientrano ai box: aspetto PIT_CONFIRM_S secondi prima di dirlo,
        # perché chi si ritira spesso passa prima dalla pit lane o dal garage
        for off in (-1, 1):
            car = self.rel(off)
            if not car or car["idx"] in self.retired:
                continue
            if (car["pit_status"] == 1 and self.prev_pit.get(car["idx"], 1) == 0 and me["pit_status"] == 0
                    and car.get("result_status") in (2, None)):
                self.pit_pending[car["idx"]] = (self.c.now, "davanti" if off < 0 else "dietro")
        for idx, (since, where) in list(self.pit_pending.items()):
            car = self.by_idx.get(idx)
            if (car is None or idx in self.retired or car.get("result_status") not in (2, None)
                    or car.get("driver_status") == 0):
                self.pit_pending.pop(idx)          # si è ritirato o è tornato in garage: niente "box"
                continue
            if self.c.now - since < PIT_CONFIRM_S:
                continue
            self.pit_pending.pop(idx)
            n = self.name(idx)
            msg = self.c.frasi("box_rivale", nome=n, Nome=cap(n), dove=where)
            deg, age = self.degradation(), self.c.status.get("tyre_age") or 0
            if where == "davanti" and deg is not None and deg * age < 0.8 and me["pit_stops"] == car["pit_stops"]:
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
                    n = self.name(behind["idx"])
                    say(self.c.frasi("dentro_secondo", nome=n, Nome=cap(n), gap=self.c.gap_text(gap)), "gara", level=1, urgent=True)
            else:
                self.close_since = None
        else:
            self.close_since = None

        if self.start_grid is None and me.get("grid_position"):
            self.start_grid = me["grid_position"]
        self.dec.on_lapdata(me)                  # com'è andata la partenza, dopo la prima curva
        self.follow.on_laps(me)                  # il pilota che mi hai chiesto di seguire
        self.tactics.on_live(me)                 # chi ti segue in scia prima del rettilineo
        # radio di squadra: il compagno (un amico del server) è subito davanti o dietro
        if stable:
            self.friend.on_live(me)
            self.strategy.on_live(me)
        if me["lap_num"] > 1 and stable and me["pit_status"] == 0:
            for off, where in ((-1, "davanti"), (1, "dietro")):
                car = self.rel(off)
                mate = self.mate_name(car["idx"]) if car else None
                if not mate or car["pit_status"]:
                    continue
                gap = self.gap_between(car, me) if off < 0 else self.gap_between(me, car)
                if 0 < gap < 1.0 and me["lap_num"] - self.mate_close.get(mate, -10) >= 3:
                    self.mate_close[mate] = me["lap_num"]
                    team = self.same_team(car["idx"])
                    say(f"{where.capitalize()} c'è {self.mate_word(car['idx'])} {mate}, a {self.c.gap_text(gap)}. " +
                        ("Se lo attacchi fallo pulito, niente contatti." if off < 0 else
                         "Lascia spazio in staccata, non buttiamo via punti per la squadra." if team else
                         "Difenditi pulito, niente contatti tra amici."),
                        "squadra", level=2, urgent=True)

        # il tuo pit stop
        if self.my_stops is None:
            self.my_stops = me["pit_stops"]
        if me["pit_stops"] > self.my_stops:
            self.my_stops = me["pit_stops"]
            self.pit_exit_pending = True
        if self.pit_exit_pending and me["pit_status"] == 0:
            self.pit_exit_pending = False
            self.wear_hist = []
            say(self.c.frasi("pit_fatto", p=me["position"]), "gara", level=1, urgent=True)

        # cambi di posizione (stabili da 3 secondi, per ignorare i sorpassi a metà)
        pos = me["position"]
        cand, since = self.pos_candidate
        if pos != cand:
            self.pos_candidate = (pos, self.c.now)
        elif self.c.now - since >= 3 and pos != self.pos_announced:
            if self.c.now - self.retire_t < 15 and pos < (self.pos_announced or 99):
                pass                                   # posizione guadagnata per un ritiro: già detto
            elif self.pos_announced is not None and me["pit_status"] == 0:
                # al primo giro le posizioni cambiano di continuo: le scrivo ma non le dico,
                # la posizione la dico a fine giro (come in una gara vera)
                lvl = 3 if me["lap_num"] <= 1 else 2
                if pos < self.pos_announced:
                    say(self.c.with_name(self.c.frasi("sorpasso", p=pos)), "gara", level=lvl)
                else:
                    say(self.c.frasi("perso", p=pos), "gara", level=lvl)
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
            say(self.c.frasi("bandiera_scacchi", p=me["position"]), "gara", level=1, urgent=True)
            text = self.debrief(me)
            if text:
                say(text, "debrief", level=2, urgent=True)
            try:
                self.c.on_race_end(me, text)
            except Exception as e:
                print("Riepilogo su Discord non inviato:", e, flush=True)
            return
        if remaining is not None and remaining <= 0:
            return                      # gara finita
        if remaining == 1:
            say(self.c.frasi("ultimo_giro", p=me["position"]), "gara", level=1, urgent=True)

        # distacchi e tendenza
        hist = {}
        fr = self.c.frasi
        first = (fr("pos_mancano", p=me["position"], n=remaining) if remaining and remaining > 1
                 else fr("pos", p=me["position"]))
        parts = [first]
        prev = self.gap_hist.get(lap - 1, {})
        ahead, behind = self.rel(-1), self.rel(1)
        if ahead and me["pit_status"] == 0:
            gap = self.gap_between(ahead, me)
            hist["ahead"] = (ahead["idx"], gap)
            txt = fr("davanti", nome=self.name(ahead["idx"]), gap=self.c.gap_text(gap))
            p = prev.get("ahead")
            if p and p[0] == ahead["idx"]:
                diff = p[1] - gap
                if diff >= 0.05:
                    txt += ", " + fr("recuperi", d=tenths(diff))
                elif diff <= -0.05:
                    txt += ", " + fr("perdi_giro", d=tenths(diff))
            t = self.tyres.get(ahead["idx"])
            mine = self.c.status.get("compound")
            if t and t.get("compound") and mine and t["compound"] != mine:
                txt += f", lui ha le {t['compound']} di {plural(t['tyre_age'], 'giro', 'giri')}"
            parts.append(txt)
        if behind and behind["pit_status"] == 0:
            gap = self.gap_between(me, behind)
            hist["behind"] = (behind["idx"], gap)
            txt = fr("dietro", nome=self.name(behind["idx"]), gap=self.c.gap_text(gap))
            p = prev.get("behind")
            if p and p[0] == behind["idx"]:
                diff = p[1] - gap
                if diff >= 0.05:
                    txt += fr("si_avvicina", d=tenths(diff))
            parts.append(txt)
        self.gap_hist[lap] = hist
        # dove sono i compagni di squadra (amici del server nella stessa gara)
        for mname, midx in self.mates():
            car = self.by_idx.get(midx)
            if not car or car in (ahead, behind) or car.get("result_status") not in (2, 3, None):
                continue
            front, back = (car, me) if car["position"] < me["position"] else (me, car)
            g = self.gap_between(front, back)
            side = "davanti" if car["position"] < me["position"] else "dietro"
            txt = f"{self.mate_word(midx)} {mname} è P{car['position']}"
            if g and g < 60:
                txt += f", a {self.c.gap_text(g)} {side}"
            parts.append(txt)
        # le cose che mi chiedi spesso te le dico da solo a fine giro
        parts += self.c.habit_parts()
        if remaining != 1:
            say(". ".join(p[0].upper() + p[1:] for p in parts) + ".", "gara", level=2)

        # finestra pit
        ideal, latest = s.get("pit_ideal", 0), s.get("pit_latest", 0)
        if ideal and me["pit_stops"] == 0:
            if lap == ideal and self._once(("apre", ideal)):
                est = self.rejoin_estimate(self.c.pit_loss)
                where = f" Se ti fermi ora rientri circa P{est['pos']}" if est else ""
                if est and est.get("to_stop") and est["pos"] > me["position"] + 1:
                    n = est["to_stop"]
                    where += f", ma {n} di quelli davanti {'deve' if n == 1 else 'devono'} ancora fermarsi"
                where += "." if est else ""
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
        self.friend.on_lap(me, remaining)
        self.team.on_lap(me)
        self.dec.on_lap(me)
        self.follow.on_lap(me, remaining)
        self.tactics.on_lap(me, remaining)
        self.strategy.on_lap(me, remaining)

    # ------------------------------------------------------ briefing prima del via
    def briefing(self):
        """Il riepilogo prima della partenza: griglia, vicini, gomme, piano, meteo, finestra box."""
        c, s = self.c, self.c.session
        parts = [f"Gara di {s['total_laps']} giri a {c.track_name}"]
        me = self.me()
        if me:
            grid = me.get("grid_position") or me["position"]
            self.start_grid = self.start_grid or grid      # online il gioco a volte non manda la griglia
            txt = "Parti dalla pole" if grid == 1 else f"Parti P{grid}"
            front = next((x for x in self.cars if x["position"] == grid - 1), None)
            back = next((x for x in self.cars if x["position"] == grid + 1), None)
            if front:
                txt += f", davanti hai {self.name(front['idx'])}"
            if back:
                txt += (" e " if front else ", ") + f"dietro {self.name(back['idx'])}"
            parts.append(txt)
        comp = c.status.get("compound")
        if comp:
            parts.append(f"Partiamo con le {comp}")
        try:
            p = self.strategy.plan()
        except Exception:
            p = None
        prv = c.practice.briefing_text() if not c.practice.active() else None
        if prv:
            parts.append(prv)
        if p:
            parts.append(self.strategy.describe(p).rstrip("."))
            self.strategy.last_plan = (p["stop"], p.get("lap"), p.get("compound"), p.get("two_stops"))
            self.strategy.last_plan_lap = 1
        ideal, latest = s.get("pit_ideal", 0), s.get("pit_latest", 0)
        if ideal and latest:
            parts.append(f"Finestra box dal giro {ideal} al {latest}")
        rain = next((f for f in s.get("forecast", []) if f["min"] > 0 and (f["weather"] >= 3 or f["rain"] >= 50)), None)
        if s.get("weather", 0) >= 3:
            parts.append(f"Pista bagnata, {s['track_temp']} gradi")
        elif rain:
            parts.append(f"Pista a {s['track_temp']} gradi, ma pioggia prevista tra {rain['min']} minuti")
        else:
            parts.append(f"Pista asciutta a {s['track_temp']} gradi, niente pioggia prevista")
        return ". ".join(x[0].upper() + x[1:] for x in parts) + ". Buona gara!"

    # ------------------------------------------------------ debrief dopo la bandiera a scacchi
    def debrief(self, me):
        """Il riassunto della gara alla radio, come dopo il traguardo vero: posizioni, giro migliore,
        costanza, soste, penalità e il punto dove hai perso di più."""
        c, parts = self.c, []
        pos, grid = me["position"], me.get("grid_position") or self.start_grid or 0
        if grid:
            d = grid - pos
            how = (f"{d} posizioni guadagnate" if d > 1 else "una posizione guadagnata" if d == 1 else
                   f"{-d} posizioni perse" if d < -1 else "una posizione persa" if d == -1 else "stessa posizione del via")
            parts.append("Dalla pole alla vittoria, partenza e arrivo in testa" if grid == 1 and pos == 1
                         else f"Partito P{grid}, arrivato P{pos}: {how}")
        laps = [h for h in c.history if h.get("valid") and h.get("ms")]
        if laps:
            best = min(laps, key=lambda h: h["ms"])
            parts.append(f"Il tuo giro più veloce è {fmt_lap_voice(best['ms'])}, al giro {best['lap']}")
            clean = [h["ms"] / 1000 for h in laps if h["lap"] > 1 and h["ms"] <= best["ms"] * 1.03]
            if len(clean) >= 4:
                mean = sum(clean) / len(clean)
                sd = (sum((x - mean) ** 2 for x in clean) / len(clean)) ** 0.5
                parts.append(f"Costanza ottima, i giri puliti variano di {tenths(sd)}" if sd < 0.25 else
                             f"Costanza buona, i giri puliti variano di {tenths(sd)}" if sd < 0.45 else
                             f"Costanza da migliorare: i giri puliti variano di {tenths(sd)}")
        stops = me.get("pit_stops") or 0
        parts.append("Nessuna sosta" if not stops else "Una sosta" if stops == 1 else f"{stops} soste")
        pen = me.get("penalties_s") or 0
        if pen or self.warnings >= 2:
            bits = []
            if pen:
                bits.append(f"{pen} secondi di penalità")
            if self.warnings >= 2:
                bits.append(f"{self.warnings} avvertimenti per i limiti della pista")
            parts.append("Attenzione: " + " e ".join(bits))
        # la staccata dove hai perso di più, in media, nei giri di gara
        loss = {}
        for h in c.history:
            for z in h.get("zones") or []:
                loss.setdefault(z["n"], []).append((z["loss"], z.get("at")))
        worst = None
        for n, vals in loss.items():
            if len(vals) >= 3:
                avg = sum(v for v, _ in vals) / len(vals)
                if avg > 0.05 and (worst is None or avg > worst[1]):
                    worst = (n, avg, vals[0][1])
        if worst:
            parts.append(f"Da migliorare: {c.zone_label({'n': worst[0], 'at': worst[2]})}, "
                         f"ci perdi in media {tenths(worst[1])} a giro")
        if pos == 1:
            parts.append("Vittoria, grande lavoro!")
        elif pos <= 3:
            parts.append("Podio, ottimo lavoro!")
        if len(parts) <= 1:
            return None
        return "Il riassunto della gara. " + ". ".join(p[0].upper() + p[1:] for p in parts).rstrip("!") + (
            "!" if parts[-1].endswith("!") else ".")

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
                self.c.say(f"Undercut possibile {prep('su', self.name(ahead['idx']))}: è a {self.c.gap_text(gap)} e con le gomme nuove "
                           f"guadagni circa {num(round(gain, 1))}. Se ti fermi ora e lui resta fuori, gli esci davanti.",
                           "strategia", level=1, urgent=True)
        if (behind and behind["pit_status"] == 0 and behind["pit_stops"] == me["pit_stops"]
                and ("behind", behind["idx"], me["pit_stops"]) not in self.undercut_said):
            gap = self.gap_between(me, behind)
            if 0 < gap < gain:
                self.undercut_said.add(("behind", behind["idx"], me["pit_stops"]))
                self.c.say(f"Attento all'undercut {prep('di', self.name(behind['idx']))}: è a {self.c.gap_text(gap)}. "
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
        # la stima del gioco a volte è senza senso (in una gara vera diceva P21 da primo): la uso
        # solo se è vicina al calcolo sui distacchi
        if game and abs(game - out["pos"]) <= 3:
            out["pos"], out["source"] = game, "gioco"
        # quelli davanti che non si sono ancora fermati: dopo la loro sosta ti ripassano davanti
        out["to_stop"] = sum(1 for c in ahead if c["pit_stops"] <= me["pit_stops"])
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
            prev = self.gap_hist.get(me["lap_num"] - 1, {})
            try:
                friends = {f["idx"]: f for f in (self.c.friend_info() or [])}
            except Exception:
                friends = {}
            for key, off in (("ahead", -1), ("behind", 1)):
                car = self.rel(off)
                if car:
                    gap = self.gap_between(car, me) if off < 0 else self.gap_between(me, car)
                    t = self.tyres.get(car["idx"], {})
                    p = prev.get(key)
                    f = friends.get(car["idx"])
                    out[key] = {"name": self.name(car["idx"], voice=False), "gap": round(gap, 1),
                                "mate": self.mate_name(car["idx"]), "pos": car["position"],
                                "mate_word": "compagno" if self.same_team(car["idx"]) else "amico",
                                "compound": t.get("compound"), "tyre_age": t.get("tyre_age"),
                                "pit": car["pit_status"] > 0,
                                # quanto si accorcia il distacco a giro (positivo = vi avvicinate)
                                "trend": round(p[1] - gap, 2) if p and p[0] == car["idx"] else None,
                                "wear": round(f["wear"]) if f and f.get("wear") is not None else None}
            lp = self.strategy.last_plan
            if lp:
                plan = {"stop": bool(lp[0]), "lap": lp[1], "compound": lp[2], "two_stops": bool(lp[3]),
                        "now": self.c.status.get("compound"), "lap_now": me["lap_num"]}
                try:
                    est = self.rejoin_estimate(self.c.pit_loss) if lp[0] else None
                    plan["rejoin"] = est.get("pos") if est else None
                except Exception:
                    plan["rejoin"] = None
                out["plan"] = plan
            out["mates"] = [{"name": n, "position": self.by_idx[i]["position"]} for n, i in self.mates()]
            # mini classifica: due auto davanti e due dietro (più il compagno, se è lontano)
            tower, mates = [], dict((i, n) for n, i in self.mates())
            near = [c for c in self.cars if abs(c["position"] - me["position"]) <= 2]
            near += [self.by_idx[i] for i in mates if self.by_idx[i] not in near]
            for car in sorted(near, key=lambda c: c["position"]):
                mine = car["idx"] == me["idx"]
                if mine:
                    gap = None
                elif car["position"] < me["position"]:
                    gap = self.gap_between(car, me)
                else:
                    gap = -self.gap_between(me, car)
                t = self.tyres.get(car["idx"], {})
                tower.append({"pos": car["position"], "name": "Tu" if mine else self.name(car["idx"], voice=False),
                              "me": mine, "mate": mates.get(car["idx"]), "gap": round(gap, 1) if gap is not None else None,
                              "team": (self.names.get(car["idx"]) or {}).get("team"), "compound": t.get("compound"),
                              "pit": car["pit_status"] > 0})
            out["tower"] = tower
            out["follow"] = self.follow.snapshot()
        return out

    def drivers_for_ai(self):
        """Chi c'è in gara, per capire «quello della Ferrari», «chi è terzo»…"""
        out = []
        for car in sorted(self.cars, key=lambda c: c["position"] or 99):
            if car["idx"] == self.c.player_idx:
                continue
            p = self.names.get(car["idx"]) or {}
            t = self.tyres.get(car["idx"]) or {}
            out.append({"nome": self.name(car["idx"], voice=False), "squadra": TEAMS.get(p.get("team")),
                        "pos": car["position"], "gomme": t.get("compound"), "eta_gomme": t.get("tyre_age")})
        return out[:22]

    def context(self):
        snap = self.snapshot()
        snap["storico_distacchi"] = {
            g: {k: {"pilota": self.name(v[0]), "distacco_s": round(v[1], 2)} for k, v in h.items()}
            for g, h in list(self.gap_hist.items())[-6:]}
        snap["usura_per_giro"] = self.wear_hist
        snap["rientro_se_ti_fermi_ora"] = self.rejoin_estimate(self.c.pit_loss)
        return snap
