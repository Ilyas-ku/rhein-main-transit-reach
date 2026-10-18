"""Reliability-adjusted public transport isochrones TO one office.

For every arrival deadline (07:00-10:00) and reliability level, a reverse
Connection Scan over the weekday timetable finds, for each stop, the latest
departure that still reaches the office in time. A 200 m grid of home
locations then walks to the best stop. Output: web/data.json for index.html.

Reliability: each leg's arrival is pushed back by the delay that is not
exceeded on X% of weekday arrivals at that stop, on that line, in that hour
(measured 24 Sep - 16 Oct 2026), plus the expected wait caused by
cancellations (cancel rate x headway). The next vehicle is assumed to leave
on time, so a late arrival can cost the connection - that is the point.
This is conservative for multi-leg trips (per-leg quantiles add up).

Inputs in data/: connections, stops (export_inputs.py), delays_HH
(export_delays.py), population (Zensus 2022 100 m grid).
"""
import base64
import glob
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
DATA, WEB = HERE / "data", HERE / "web"

OFFICE = (50.0985379, 8.6814607)                  # Hedderichstraße 108-110, Nominatim
BOX = (49.85, 8.20, 50.35, 9.00)                  # Frankfurt metro box of the collector
DLAT, DLON = 0.0018, 0.0028                       # ~200 m cells
WALK_M_PER_S = 80 / 60 / 1.3                      # 4.8 km/h along a 1.3x detour of the crow line
MAX_ACCESS_M = 1000                               # home -> stop, ~16 min
MAX_EGRESS_M = 1200                               # stop -> office
MAX_TRANSFER_M = 400
TRANSFER_BUFFER_S = 120
MIN_N = 20                                        # below this, a stop's delays fall back to its line
ARRIVALS = [7 * 3600 + 15 * 60 * i for i in range(13)]          # 07:00 .. 10:00
LEVELS = [("Timetable", None), ("1 in 2 days", "q50"), ("4 in 5 days", "q80"),
          ("9 in 10 days", "q90"), ("19 in 20 days", "q95")]
BANDS = [10, 20, 30, 40, 50, 60]

to_m = Transformer.from_crs(4326, 25832, always_xy=True)


def load():
    con = pd.read_parquet(DATA / "connections.parquet")
    stops = pd.read_parquet(DATA / "stops.parquet").drop_duplicates("stop")
    dl = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(str(DATA / "delays_*.parquet")))])
    pop = pd.read_parquet(DATA / "population.parquet")
    return con, stops, dl, pop


def leg_delays(con, stops, dl):
    """Delay budget (s) per connection and level, at its arrival stop / line / hour."""
    q = ["q50", "q80", "q90", "q95"]
    dl[q] = dl[q].clip(lower=0, upper=1800)
    # line-level fallback: n-weighted mean of the stop quantiles
    w = dl.assign(**{c: dl[c] * dl.n for c in q + ["cancel_rate"]})
    line = w.groupby(["line", "mode", "hour"])[q + ["cancel_rate", "n"]].sum()
    line[q + ["cancel_rate"]] = line[q + ["cancel_rate"]].div(line["n"], axis=0)
    mode = dl.groupby(["mode", "hour"])[q + ["cancel_rate"]].median()

    c = con.merge(stops[["stop", "stop_key"]], left_on="arr_stop", right_on="stop", how="left")
    c["hour"] = (c.arr_s // 3600).clip(5, 10)
    own = dl[dl.n >= MIN_N].set_index(["stop_key", "line", "hour"])[q + ["cancel_rate"]]
    a = c.join(own, on=["stop_key", "line", "hour"])
    b = c.join(line[q + ["cancel_rate"]], on=["line", "mode", "hour"])
    m = c.join(mode, on=["mode", "hour"])
    for col in q + ["cancel_rate"]:
        a[col] = a[col].fillna(b[col]).fillna(m[col]).fillna(0)
    # headway of this line at this stop and hour, for the cancellation wait
    hw = c.groupby(["line", "arr_stop", "hour"]).trip.transform("count")
    headway = (3600 / hw).clip(upper=1800)
    out = {}
    for name, col in LEVELS:
        out[name] = np.zeros(len(c)) if col is None else (a[col] + a["cancel_rate"] * headway).to_numpy()
    return out


def footpaths(xy, radius):
    tree = cKDTree(xy)
    pairs = tree.query_pairs(radius, output_type="ndarray")
    d = np.linalg.norm(xy[pairs[:, 0]] - xy[pairs[:, 1]], axis=1)
    nb = [[(i, 0.0)] for i in range(len(xy))]
    for (i, j), dist in zip(pairs, d):
        t = dist / WALK_M_PER_S
        nb[i].append((j, t)); nb[j].append((i, t))
    return nb


def reverse_csa(T, c, delay, nb, egress, n_stops):
    """Latest scheduled departure per stop that still reaches the office by T.

    Connections are scanned by arrival time, latest first: anything that
    could follow a connection departs after it arrives, so it is already done.
    """
    NEG = -1e18
    A = [NEG] * n_stops                            # latest arrival at a stop that still works
    for s, t in egress:
        A[s] = max(A[s], T - t)
    L = [NEG] * n_stops                            # latest boarding time at a stop
    ok = set()
    m = (c["arr_s"] <= T) & (c["dep_s"] >= T - 2 * 3600)
    idx = np.flatnonzero(m)
    idx = idx[np.argsort(-c["arr_s"][idx], kind="stable")]
    dep, arr, ds, as_, tr = (c[k][idx].tolist() for k in ("dep", "arr", "dep_s", "arr_s", "trip"))
    dly = delay[idx].tolist()
    for u, v, td, ta, t, d in zip(dep, arr, ds, as_, tr, dly):
        if t not in ok:
            if ta + d > A[v]:
                continue
            ok.add(t)
        if td > L[u]:
            L[u] = td
            for w, wt in nb[u]:
                x = td - wt - TRANSFER_BUFFER_S
                if x > A[w]:
                    A[w] = x
    return np.array(L)


def main():
    con, stops, dl, pop = load()
    stops = stops[stops.stop.isin(set(con.dep_stop) | set(con.arr_stop))].reset_index(drop=True)
    sid = {s: i for i, s in enumerate(stops.stop)}
    sx, sy = to_m.transform(stops.stop_lon.to_numpy(), stops.stop_lat.to_numpy())
    sxy = np.column_stack([sx, sy])
    ox, oy = to_m.transform(OFFICE[1], OFFICE[0])

    delays = leg_delays(con, stops, dl)
    # average buffer per mode in the morning peak, shown next to the slider
    peak = (con.arr_s >= 6.5 * 3600) & (con.arr_s <= 9.5 * 3600)
    mode_delay = {name: (pd.Series(delays[name][peak.to_numpy()], index=con["mode"][peak])
                         .groupby(level=0).mean() / 60).round(1).to_dict() for name, _ in LEVELS}
    print(mode_delay)
    c = {"dep": con.dep_stop.map(sid).to_numpy(), "arr": con.arr_stop.map(sid).to_numpy(),
         "dep_s": con.dep_s.to_numpy().astype(float), "arr_s": con.arr_s.to_numpy().astype(float),
         "trip": pd.factorize(con.trip)[0]}
    nb = footpaths(sxy, MAX_TRANSFER_M)
    de = np.hypot(sx - ox, sy - oy)
    egress = [(i, d / WALK_M_PER_S) for i, d in enumerate(de) if d <= MAX_EGRESS_M]
    print(f"{len(con)} connections, {len(stops)} stops, {len(egress)} stops within walking of the office")

    # grid of home locations
    lats = np.arange(BOX[0] + DLAT / 2, BOX[2], DLAT)
    lons = np.arange(BOX[1] + DLON / 2, BOX[3], DLON)
    LON, LAT = np.meshgrid(lons, lats)               # row 0 = south
    gx, gy = to_m.transform(LON.ravel(), LAT.ravel())
    gxy = np.column_stack([gx, gy])
    tree = cKDTree(sxy)
    near = tree.query_ball_point(gxy, MAX_ACCESS_M)
    ci, si = [], []
    for cell, lst in enumerate(near):
        ci.extend([cell] * len(lst)); si.extend(lst)
    ci, si = np.array(ci), np.array(si)
    walk_cs = np.hypot(gxy[ci, 0] - sxy[si, 0], gxy[ci, 1] - sxy[si, 1]) / WALK_M_PER_S
    direct = np.hypot(gx - ox, gy - oy) / WALK_M_PER_S
    active = np.unique(np.concatenate([ci, np.flatnonzero(direct <= 60 * 60)]))
    pos = np.full(len(gx), -1); pos[active] = np.arange(len(active))
    print(f"grid {len(lats)}x{len(lons)}, {len(active)} cells within walking of a stop")

    # population per active cell
    pr = ((pop.lat - BOX[0]) / DLAT).astype(int); pc = ((pop.lon - BOX[1]) / DLON).astype(int)
    inside = (pr >= 0) & (pr < len(lats)) & (pc >= 0) & (pc < len(lons))
    cell_pop = np.bincount((pr * len(lons) + pc)[inside], weights=pop["pop"][inside], minlength=len(gx))

    scen, stats = {}, {}
    for T in ARRIVALS:
        for name, _ in LEVELS:
            L = reverse_csa(T, c, delays[name], nb, egress, len(stops))
            best = np.full(len(gx), -1e18)
            np.maximum.at(best, ci, L[si] - walk_cs)
            best = np.maximum(best, np.where(direct <= 3600, T - direct, -1e18))
            mins = (T - best) / 60
            m = np.where(mins <= 60, np.ceil(mins), 255).astype(np.uint8)[active]
            key = f"{T // 3600:02d}:{T % 3600 // 60:02d}|{name}"
            scen[key] = base64.b64encode(m.tobytes()).decode()
            tt = np.where(mins <= 60, mins, np.inf)
            stats[key] = [int(cell_pop[tt <= b].sum()) for b in BANDS]
        print(T // 3600, T % 3600 // 60, {k.split('|')[1]: v[2] for k, v in stats.items() if k.startswith(f"{T // 3600:02d}:{T % 3600 // 60:02d}")})

    WEB.mkdir(exist_ok=True)
    json.dump({
        "office": OFFICE, "box": BOX, "dlat": DLAT, "dlon": DLON, "rows": len(lats), "cols": len(lons),
        "active": base64.b64encode(active.astype(np.uint32).tobytes()).decode(),
        "popcells": base64.b64encode(np.clip(np.round(cell_pop[active]), 0, 65535).astype(np.uint16).tobytes()).decode(),
        "levels": [n for n, _ in LEVELS], "arrivals": [f"{t // 3600:02d}:{t % 3600 // 60:02d}" for t in ARRIVALS],
        "bands": BANDS, "scenarios": scen, "pop": stats, "mode_delay": mode_delay,
    }, open(WEB / "data.json", "w"), separators=(",", ":"))
    print(f"wrote {WEB / 'data.json'} ({(WEB / 'data.json').stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
