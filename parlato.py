"""Formattazione di tempi e numeri, per lo schermo e per la voce."""


def fmt_lap(ms):
    if not ms:
        return "--:--.---"
    m, rest = divmod(int(ms), 60000)
    return f"{m}:{rest / 1000:06.3f}"


def num(x, dec=1):
    """3.14 -> '3 virgola 1' (si legge bene ad alta voce)."""
    s = f"{abs(x):.{dec}f}"
    if s.endswith(".0"):
        s = s[:-2]
    return s.replace(".", " virgola ")


def fmt_lap_voice(ms):
    m, rest = divmod(int(ms), 60000)
    s = num(rest / 1000)
    return f"{m} e {s}" if m else s


def tenths(sec):
    """0.23 -> '2 decimi', 1.4 -> '1 virgola 4 secondi'."""
    sec = abs(sec)
    if sec < 1:
        d = max(1, round(sec * 10))
        return "1 decimo" if d == 1 else f"{d} decimi"
    t = num(sec)
    return t + (" secondo" if t == "1" else " secondi")


def gap_voice(g):
    """Un distacco detto come lo direbbe un ingegnere: "8 decimi", "un secondo e mezzo", "circa 4 secondi"."""
    g = abs(g)
    if g < 0.25:
        return "un soffio"
    if g < 0.95:
        d = max(2, round(g * 10))
        return f"{d} decimi"
    if g < 10:
        h = round(g * 2) / 2                      # al mezzo secondo
        whole, half = int(h), h - int(h) >= 0.5
        base = "un secondo" if whole == 1 else f"{whole} secondi"
        txt = base + (" e mezzo" if half else "")
        return ("circa " + txt) if g >= 3 else txt
    return f"circa {round(g)} secondi"


def plural(n, one, many):
    return f"{n} {one if n == 1 else many}"
