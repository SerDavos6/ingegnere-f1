"""L'amico in gara: cosa sa l'ingegnere della sua macchina.

Sul server su internet anche la PS5 del tuo amico manda i dati, quindi l'ingegnere conosce cose che
il gioco online non dice: usura vera delle gomme, batteria, quando il suo ingegnere pensa di fermarsi.
Solo per gli amici che condividono i giri (Impostazioni → Condividi i giri), come per tutto il resto.

coach.friend_info() → [{"name", "idx", "wear", "compound", "tyre_age", "ers", "stops", "plan_lap"}]
"""
from parlato import num


def _n(s):
    return " ".join(str(s or "").lower().split())


class FriendWatch:
    WEAR_SOON = 55          # usura oltre la quale "si ferma presto"
    ERS_DIFF = 25           # punti di batteria di differenza per dire "attaccalo" / "difenditi"

    def __init__(self, race):
        self.r = race
        self.reset()

    def reset(self):
        self.soon_said = set()      # (nome, soste) dell'avviso "si ferma presto"
        self.stops_seen = {}        # nome -> soste viste
        self.ers_lap = {}           # nome -> giro dell'ultimo avviso batteria

    @property
    def c(self):
        return self.r.c

    def friends(self):
        try:
            return [f for f in (self.c.friend_info() or []) if f.get("idx") in self.r.by_idx]
        except Exception:
            return []

    def _gap(self, f, me):
        """Distacco con l'amico: positivo se è davanti."""
        car = self.r.by_idx.get(f["idx"])
        if not car or not me:
            return None, car
        if car["position"] < me["position"]:
            return self.r.gap_between(car, me), car
        return -self.r.gap_between(me, car), car

    # ------------------------------------------------------------ a ogni giro
    def on_lap(self, me, remaining):
        if not me or me["lap_num"] <= 2 or me["pit_status"] or (remaining is not None and remaining < 3):
            return
        for f in self.friends():
            gap, car = self._gap(f, me)
            if car is None or car.get("result_status") not in (2, None):
                continue
            who = f"{self.r.mate_word(f['idx']).capitalize()} {f['name']}"
            # si è appena fermato: che gomma ha messo
            stops = f.get("stops") or 0
            prev = self.stops_seen.get(f["name"])
            self.stops_seen[f["name"]] = stops
            if prev is not None and stops > prev and f.get("compound"):
                self.c.say(f"{who} si è fermato, ha montato le {f['compound']}.", "squadra", level=2)
                continue
            wear = f.get("wear")
            key = (f["name"], stops)
            if wear is None or wear < self.WEAR_SOON or key in self.soon_said or car["pit_status"]:
                continue
            self.soon_said.add(key)
            txt = f"{who} ha le gomme al {wear:.0f} per cento, si fermerà presto"
            if f.get("plan_lap") and f["plan_lap"] >= me["lap_num"]:
                txt += f", il suo piano dice giro {f['plan_lap']}"
            same = stops == me["pit_stops"]
            if gap is not None and same and 0 < -gap < 4:
                txt += ". È dietro: se entra lui, entra anche tu il giro dopo per coprire l'undercut."
            elif gap is not None and same and 0 < gap < 4:
                txt += ". È davanti: quando entra spingi forte, puoi passarlo con l'overcut."
            else:
                txt += "."
            self.c.say(txt, "squadra", level=2, urgent=gap is not None and abs(gap) < 4)

    # ------------------------------------------------------------ in diretta: batteria
    def on_live(self, me):
        if not me or me["lap_num"] <= 1 or me["pit_status"]:
            return
        mine = self.c.energy.now_pct
        if mine is None:
            return
        for f in self.friends():
            his = f.get("ers")
            gap, car = self._gap(f, me)
            if his is None or gap is None or car["pit_status"] or not 0 < abs(gap) < 1.0:
                continue
            if me["lap_num"] - self.ers_lap.get(f["name"], -10) < 3:
                continue
            who = f"{self.r.mate_word(f['idx'])} {f['name']}"
            if gap > 0 and mine - his >= self.ERS_DIFF:
                self.ers_lap[f["name"]] = me["lap_num"]
                self.c.say(f"Hai più batteria {who.replace('il tuo', 'del tuo')}: {mine:.0f} contro {his:.0f}. "
                           "Attaccalo sul prossimo rettilineo.", "squadra", level=1, urgent=True)
            elif gap < 0 and his - mine >= self.ERS_DIFF:
                self.ers_lap[f["name"]] = me["lap_num"]
                self.c.say(f"Occhio, {who} ha più batteria di te: {his:.0f} contro {mine:.0f}. "
                           "Difendi l'interno in staccata.", "squadra", level=1, urgent=True)

    # ------------------------------------------------------------ domande
    def asked(self, q):
        """La domanda parla di un amico? (per nome, "amico" o "compagno")."""
        fs = self.friends()
        if not fs:
            return None
        named = [f for f in fs if _n(f["name"]) and _n(f["name"]) in q]
        if named:
            return named
        if "amic" in q or "compagn" in q:
            return fs
        return None

    def answer(self, fs):
        me = self.r.me()
        out = []
        for f in fs[:2]:
            car = self.r.by_idx.get(f["idx"])
            if not car:
                continue
            bits = [f"{f['name']} è P{car['position']}"]
            gap, _ = self._gap(f, me)
            if gap is not None and abs(gap) < 60:
                bits[0] += f", a {self.c.gap_text(gap)} {'davanti' if gap > 0 else 'dietro'}"
            if f.get("compound"):
                g = f"gomme {f['compound']}"
                if f.get("tyre_age") is not None:
                    g += f" di {f['tyre_age']} giri"
                if f.get("wear") is not None:
                    g += f", usura {f['wear']:.0f} per cento"
                bits.append(g)
            if f.get("ers") is not None:
                bits.append(f"batteria {f['ers']:.0f} per cento")
            stops = f.get("stops") or 0
            bits.append("non si è ancora fermato" if stops == 0 else f"{stops} {'sosta' if stops == 1 else 'soste'}")
            if f.get("plan_lap") and me and f["plan_lap"] >= me["lap_num"]:
                bits.append(f"il suo piano è fermarsi verso il giro {f['plan_lap']}")
            out.append(", ".join(bits) + ".")
        return " ".join(out) if out else None
