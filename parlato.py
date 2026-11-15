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
    return num(sec) + " secondi"


def plural(n, one, many):
    return f"{n} {one if n == 1 else many}"
