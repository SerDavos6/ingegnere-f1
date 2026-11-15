"""Simulatore di pacchetti EA F1 (formato 2025/2026) per provare tutto senza PS5.

Genera una gara su una pista finta con avversari, un pit stop di chi ti precede,
una safety car, pioggia in arrivo e piccoli errori di guida casuali.

Uso:  python3 simulator.py --laps 6 --speed 4      (speed = quante volte più veloce del reale)
      python3 simulator.py --practice               (prova libera, senza avversari)
"""
import argparse
import math
import random
import socket
import struct
import time

import f1_packets as P

LENGTH = 4200.0
# (distanza apice m, velocità minima km/h, angolo di curva in gradi)
CORNERS = [(450, 95, 80), (1150, 180, 30), (1600, 70, 110), (2350, 140, 50),
           (2900, 230, 15), (3500, 110, 45), (3950, 160, 30)]
VMAX = 315 / 3.6
STEP = 1.0
FORMAT = 2025
NAMES = {0: "Pilota", 1: "Max VERSTAPPEN", 2: "Lando NORRIS", 3: "Charles LECLERC", 4: "Lewis HAMILTON"}


def track_shape():
    """Coordinate x,z per ogni metro: la pista è chiusa e le curve sono dove sono gli apici."""
    n = int(LENGTH / STEP) + 1
    turn = [0.0] * n
    for apex, _, ang in CORNERS:
        for i in range(int(apex - 40), int(apex + 40)):
            turn[i] += math.radians(ang) / 80
    xs, zs, h, x, z = [], [], 0.0, 0.0, 0.0
    for i in range(n):
        xs.append(x); zs.append(z)
        h += turn[i]
        x += math.cos(h) * STEP
        z += math.sin(h) * STEP
    ex, ez = x, z
    return [xs[i] - ex * i / n for i in range(n)], [zs[i] - ez * i / n for i in range(n)]


def speed_profile(brake_factor, vmin_off):
    n = int(LENGTH / STEP) + 1
    lim = [VMAX] * n
    owner = [None] * n
    for k, (apex, vmin, _) in enumerate(CORNERS):
        v = (vmin + vmin_off[k]) / 3.6
        for i in range(int((apex - 15) / STEP), int((apex + 15) / STEP)):
            lim[i] = min(lim[i], v)
    nxt = None
    for i in range(n - 1, -1, -1):
        for k, (apex, _, _) in enumerate(CORNERS):
            if abs(i * STEP - apex) < 1:
                nxt = k
        owner[i] = nxt
    v = lim[:]
    braking = [False] * n
    for i in range(n - 2, -1, -1):
        dec = 42.0 * (brake_factor[owner[i]] if owner[i] is not None else 1)
        vb = math.sqrt(v[i + 1] ** 2 + 2 * dec * STEP)
        if vb < v[i]:
            v[i] = vb
            braking[i] = True
    accel = [False] * n
    for i in range(1, n):
        a = max(2.0, 13.0 * (1 - v[i - 1] / 95))
        va = math.sqrt(v[i - 1] ** 2 + 2 * a * STEP)
        if va < v[i]:
            v[i] = va
            accel[i] = True
            braking[i] = False
    return v, braking, accel


def header(pid, uid, st, frame):
    return struct.pack(P.HEADER_FMT, FORMAT, 25, 1, 0, 1, pid, uid, st, frame, frame, 0, 255)


def cars_block(fmt, rows, trailing=b""):
    """rows: dict indice -> valori. Le auto mancanti sono tutte a zero."""
    size = struct.calcsize(fmt)
    out = b""
    for i in range(P.num_cars(FORMAT)):
        out += struct.pack(fmt, *rows[i]) if i in rows else b"\x00" * size
    return out + trailing


def lap_row(last_ms, cur_ms, d, lap, pos, front_ms, pit=0, stops=0, invalid=0, sector=0):
    return [last_ms, cur_ms, 30000, 0, 30000, 0, front_ms % 60000, front_ms // 60000, 0, 0,
            d, (lap - 1) * LENGTH + d, 0.0, pos, lap, pit, stops, sector, invalid, 0, 0, 0, 0, 0,
            pos, 4, 2, 0, 0, 0, 0, 300.0, 1]


def session_packet(uid, st, frame, a, weather, sc, forecast, race):
    b = bytearray(700)
    struct.pack_into(P.SESSION_FMT, b, 0, weather, 32, 24, a.laps, int(LENGTH), 15 if race else 1, 11)
    b[124] = sc
    b[126] = len(forecast)
    for k, (mins, w, rain) in enumerate(forecast):
        struct.pack_into("<BBBbbbbB", b, 127 + k * 8, 15 if race else 1, mins, w, 30, 0, 22, 0, rain)
    b[653], b[654] = (3, 5) if race else (0, 0)
    return header(P.PID_SESSION, uid, st, frame) + bytes(b)


def participants_packet(uid, st, frame):
    entries = b""
    for i in range(P.num_cars(FORMAT)):
        name = NAMES.get(i, "").encode().ljust(32, b"\x00")
        entries += bytes([0, i, 0, 0, 0, i, 0]) + name + b"\x00" * 18
    return header(P.PID_PARTICIPANTS, uid, st, frame) + bytes([len(NAMES)]) + entries


def main():
    global FORMAT
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=20777)
    ap.add_argument("--laps", type=int, default=6)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--format", type=int, default=2025, help="2025 o 2026")
    ap.add_argument("--practice", action="store_true", help="prova libera invece della gara")
    a = ap.parse_args()
    FORMAT = a.format
    race = not a.practice
    random.seed(a.seed)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = (a.host, a.port)
    send = lambda pkt: sock.sendto(pkt, dest)
    uid = random.SystemRandom().getrandbits(63)
    xs, zs = track_shape()
    dt = 1 / 60
    st, frame = 0.0, 0
    last_ms, fuel = 0, 1.2
    wear = [3.0, 3.0, 2.5, 2.5]
    gap_ahead, gap_behind = 2.0, 1.6

    for lap in range(1, a.laps + 1):
        nc = len(CORNERS)
        if lap == 1:
            bf, vo = [1.0] * nc, [0] * nc
        else:
            bf = [random.choice([1.0, 1.0, 0.8, 0.7, 1.05]) for _ in range(nc)]
            vo = [random.choice([0, 0, -6, -10, 3]) for _ in range(nc)]
        v, braking, accel = speed_profile(bf, vo)
        d, t_lap, invalid = 0.0, 0.0, 0
        while d < LENGTH:
            i = min(int(d / STEP), len(v) - 1)
            spd = v[i]
            brk = 1.0 if braking[i] else 0.0
            thr = 1.0 if (accel[i] or (not braking[i] and spd >= VMAX - 0.5)) else (0.0 if braking[i] else 0.35)
            gear = max(1, min(8, int(spd * 3.6 / 40) + 1))
            if lap == 3 and 2000 < d < 2010 and not race:
                invalid = 1
            temps = [95, 95, 92, 92]
            if lap == 4 and 3000 < d < 3400:
                temps[0] = 113
            st += dt; frame += 1; t_lap += dt
            sector = 0 if d < 1400 else (1 if d < 2800 else 2)

            send(header(P.PID_MOTION, uid, st, frame) + cars_block(
                "<6f6h6f", {0: [xs[i], 0.0, zs[i], 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.5, -1.0, 1.0, 0, 0, 0]}))
            send(header(P.PID_TELEMETRY, uid, st, frame) + cars_block(
                P.TELEMETRY_FMT,
                {0: [int(spd * 3.6), thr, 0.0, brk, 0, gear, 6000 + gear * 900, 0, 50, 0,
                     500, 500, 600, 600, *temps, 98, 98, 96, 96, 105, 23.0, 23.0, 22.5, 22.5, 0, 0, 0, 0]},
                b"\x00\x00\x00"))

            cur = int(t_lap * 1000)
            rows = {0: lap_row(last_ms, cur, d, lap, 3, int(gap_ahead * 1000), invalid=invalid, sector=sector)}
            if race:
                pit2 = 1 if (lap == 3 and 300 < d < 800) else 0
                after = lap > 3 or (lap == 3 and d >= 800)
                rows[1] = lap_row(last_ms, cur, d, lap, 1, 0)
                rows[2] = lap_row(last_ms, cur, d, lap, 5 if after else 2, 3000, pit=pit2, stops=1 if after else 0)
                rows[3] = lap_row(last_ms, cur, d, lap, 3 if after else 4, int(gap_behind * 1000))
                rows[4] = lap_row(last_ms, cur, d, lap, 4 if after else 5, 2500)
                if after:
                    rows[0] = lap_row(last_ms, cur, d, lap, 2, 4500, sector=sector)
            send(header(P.PID_LAP, uid, st, frame) + cars_block(P.LAP_FMT, rows, b"\x00\x00"))

            if frame % 10 == 0:
                srows = {0: [0, 0, 1, 56, 0, 40.0, 110.0, fuel, 13000, 4000, 8, 0, 0, 16, 16, lap - 1, 0,
                             0.0, 0.0, 3e6, 1, 0.0, 0.0, 0.0, 0]}
                if race:
                    srows[2] = [0] * 13 + [17, 17, lap, 0] + [0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0]
                send(header(P.PID_STATUS, uid, st, frame) + cars_block(P.STATUS_FMT, srows))
            if frame % 30 == 0:
                send(header(P.PID_DAMAGE, uid, st, frame) + cars_block("<4f4B4B4B18B", {0: [*wear, *([0] * 30)]}))
            if frame % 30 == 1:
                sc = 1 if (race and lap == 4 and 1000 < d < 3000) else 0
                weather = 3 if (race and lap >= 5) else 1
                forecast = [(0, weather, 10), (15, 3, 60), (30, 3, 70)] if lap < 5 else [(0, 3, 80)]
                send(session_packet(uid, st, frame, a, weather, sc, forecast, race))
            if frame % 300 == 2:
                send(participants_packet(uid, st, frame))
            d += spd * dt
            time.sleep(dt / a.speed)
        last_ms = int(t_lap * 1000)
        fuel -= 0.45
        wear = [w + 12 for w in wear]
        gap_ahead = max(0.3, gap_ahead - 0.3)
        gap_behind = max(0.4, gap_behind - 0.35)
        print(f"Giro {lap}: {last_ms / 1000:.3f}s {'(non valido)' if invalid else ''}", flush=True)
    send(header(P.PID_LAP, uid, st, frame + 1) + cars_block(
        P.LAP_FMT, {0: lap_row(last_ms, 0, 0.0, a.laps + 1, 2, 4500)}, b"\x00\x00"))


if __name__ == "__main__":
    main()
