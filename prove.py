"""Prove libere con un programma, come un weekend vero.

1. qualche giro veloce: il coach si fa il riferimento delle staccate
2. una prova lunga (6-8 giri a ritmo gara, stesse gomme, niente box): misuro passo, quanto scendono
   i tempi e soprattutto quanto si consumano le gomme a ogni giro
3. i dati restano salvati per pista e categoria (cartella prove/): il briefing della gara li usa per il
   piano gomme, e la strategia li usa nei primi giri, prima di avere dati della gara.
"""
import json
import os
import time

from parlato import fmt_lap_voice, num, tenths

LONG_MIN = 5          # giri puliti di fila per una prova lunga
LONG_GOAL = 8
FAST_LAPS = 3         # giri veloci prima di proporre la prova lunga


def _median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


def _slope(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    d = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / d if d else 0.0


class Practice:
    def __init__(self, coach):
        self.c = coach
        self.dir = None
        self.reset()

    def reset(self):
        self.stint = []           # giri della prova in corso: {"lap", "ms", "wear"}
        self.stint_comp = None
        self.said = set()
        self.fast = 0             # giri veloci validi fatti
        self.long_done = False

    # ------------------------------------------------------------------ dati salvati
    def _path(self, track=None, cat=None):
        c = self.c
        track = c.track_id if track is None else track
        cat = (cat or c.category or "f1_2025")
        if self.dir is None or track is None:
            return None
        return os.path.join(self.dir, f"{track}_{cat}.json")

    def load(self, track=None, cat=None):
        """{mescola: {"wear": % al giro, "ms": passo, "deg": ms in più a giro, "laps", "date"}}"""
        p = self._path(track, cat)
        try:
            with open(p) as f:
                return json.load(f)
        except (OSError, TypeError, ValueError):
            return {}

    def _save(self, comp, res):
        p = self._path()
        if not p:
            return
        data = self.load()
        old = data.get(comp)
        # tengo la prova più lunga (più affidabile); a parità, la più recente
        if old and old.get("laps", 0) > res["laps"] and time.time() - old.get("date", 0) < 30 * 86400:
            return
        data[comp] = res
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, p)

    def rate_for(self, comp):
        """Usura al giro stimata dalle prove per una mescola (anche da un'altra mescola provata)."""
        from strategia import LIFE
        data = self.load()
        if comp in data and data[comp].get("wear"):
            return data[comp]["wear"]
        for k, v in data.items():
            if k in LIFE and comp in LIFE and v.get("wear"):
                return v["wear"] * LIFE[k] / LIFE[comp]
        return None

    # ------------------------------------------------------------------ in pista
    def active(self):
        c = self.c
        st = c.session.get("session_type") or 0
        return bool(c.session) and not c.in_race and not c.is_quali and 1 <= st <= 4

    def on_session(self):
        """Prima uscita in prova: il programma."""
        if self.active() and "programma" not in self.said:
            self.said.add("programma")
            have = self.load()
            txt = ("Programma delle prove: tre o quattro giri veloci, poi una prova lunga di 8 giri a ritmo gara "
                   "con le gomme che pensi di usare in gara. Così preparo la strategia.")
            if have:
                txt += " Ho già dati sulle " + ", ".join(sorted(have)) + ": puoi provare un'altra mescola."
            self.c.say(txt, "prove", level=2)

    def on_lap(self, lap_ms, valid, complete, pit_lap):
        if not self.active():
            return
        c = self.c
        comp = c.status.get("compound")
        wear = c.damage.get("tyre_wear")
        wear = max(wear) if wear else None
        if pit_lap or not complete or comp != self.stint_comp:
            self._close()
            self.stint, self.stint_comp = [], comp
            if pit_lap or not complete:
                return
        if not valid:
            # un giro non valido (limiti) non rompe la prova lunga, ma non conta per il passo
            return
        self.fast += 1
        self.stint.append({"lap": c.lap_num, "ms": lap_ms, "wear": wear})
        n = len(self.stint)
        if not self.long_done and self.fast == FAST_LAPS and "proponi" not in self.said:
            self.said.add("proponi")
            c.say("Bene, il riferimento c'è. Quando vuoi, prova lunga: 8 giri a ritmo gara, senza forzare "
                  "le gomme. Ti dico io quando basta.", "prove", level=2)
        if n >= LONG_MIN:
            res = self.result()
            if res and n == LONG_MIN and ("lunga", comp) not in self.said:
                self.said.add(("lunga", comp))
                c.say(f"Prova lunga: {self.describe(comp, res)} Continua fino a 8 giri se puoi.", "prove", level=2)
            if res and n == LONG_GOAL and ("fatta", comp) not in self.said:
                self.said.add(("fatta", comp))
                self.long_done = True
                self._close()
                c.say(f"Prova lunga completata con le {comp}: {self.describe(comp, res)} "
                      "Ho salvato tutto per la gara.", "prove", level=1, urgent=True)

    def result(self):
        """Passo e usura della prova in corso (scarto i giri lenti: traffico o errori)."""
        s = self.stint
        if len(s) < LONG_MIN:
            return None
        best = min(x["ms"] for x in s)
        clean = [x for x in s if x["ms"] <= best * 1.02]
        if len(clean) < LONG_MIN - 1:
            return None
        ws = [x for x in s if x["wear"] is not None]
        wear = (ws[-1]["wear"] - ws[0]["wear"]) / max(1, ws[-1]["lap"] - ws[0]["lap"]) if len(ws) >= 2 else None
        return {"ms": _median([x["ms"] for x in clean]), "best": best,
                "deg": round(_slope([x["lap"] for x in clean], [x["ms"] for x in clean])),
                "wear": round(wear, 2) if wear and wear > 0.1 else None,
                "laps": len(s), "date": time.time()}

    def _close(self):
        if self.stint_comp and len(self.stint) >= LONG_MIN:
            res = self.result()
            if res:
                self._save(self.stint_comp, res)

    def describe(self, comp, r):
        txt = f"passo {fmt_lap_voice(r['ms'])}"
        if r.get("deg", 0) >= 40:
            txt += f", perdi circa {tenths(r['deg'] / 1000)} a giro"
        if r.get("wear"):
            life = 70.0 / r["wear"]
            txt += f", usura {num(r['wear'])} per cento a giro: le {comp} durano circa {int(life)} giri."
        else:
            txt += "."
        return txt

    def briefing_text(self):
        """Per il briefing della gara: cosa abbiamo imparato nelle prove."""
        data = self.load()
        if not data:
            return None
        bits = []
        for comp, r in sorted(data.items(), key=lambda kv: kv[1].get("ms") or 1e9):
            b = f"con le {comp} passo {fmt_lap_voice(r['ms'])}"
            if r.get("wear"):
                b += f", durano circa {int(70 / r['wear'])} giri"
            bits.append(b)
        return "Dalle prove: " + "; ".join(bits[:3])

    def snapshot(self):
        return {"active": self.active(), "stint": len(self.stint), "goal": LONG_GOAL, "compound": self.stint_comp,
                "saved": self.load() if self.active() else None}
