"""Data for the "from any point" reach map (explore/), computed in the browser.

The browser runs the forward search itself, so this only packs the inputs:

  meta.json   grid, stop coordinates, population per cell, average delay
              buffer per mode / hour / level
  c/HH.json   the weekday timetable in hourly chunks by departure: stop ids,
              times in 10 s units, trip id and the delay buffer of each leg
              at its arrival stop for the four reliability levels

Typed arrays travel as base64 inside JSON so GitHub Pages gzips them.
Reliability buffers follow build.py: delay quantile at the arrival stop /
line / hour (weekdays, measured from operators' live data) plus cancel rate
x headway, falling back to the line and then the mode when a stop has fewer
than MIN_N observations.
"""
import base64
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

from build import BOX, DLAT, DLON, MIN_N

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "full"
OUT = HERE / "explore"
Q = ["q50", "q80", "q90", "q95"]
LEVELS = ["Timetable", "1 in 2 days", "4 in 5 days", "9 in 10 days", "19 in 20 days"]


def b64(a):
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def leg_buffers(c, stops, dl):
    """Delay buffer (s) per connection and quantile, at its arrival stop / line / hour."""
    dl = dl.copy()
    dl[Q] = dl[Q].clip(lower=0, upper=1800)
    w = dl.assign(**{q: dl[q] * dl.n for q in Q + ["cancel_rate"]})
    line = w.groupby(["line", "mode", "hour"])[Q + ["cancel_rate", "n"]].sum()
    line[Q + ["cancel_rate"]] = line[Q + ["cancel_rate"]].div(line["n"], axis=0)
    mode = dl.groupby(["mode", "hour"])[Q + ["cancel_rate"]].median()
    allh = dl.groupby("mode")[Q + ["cancel_rate"]].median()           # night hours with no data

    c = c.merge(stops[["stop", "stop_key"]], left_on="arr_stop", right_on="stop", how="left")
    c["hour"] = (c.arr_s // 3600) % 24
    own = dl[dl.n >= MIN_N].set_index(["stop_key", "line", "hour"])[Q + ["cancel_rate"]]
    a = c.join(own, on=["stop_key", "line", "hour"])
    b = c.join(line[Q + ["cancel_rate"]], on=["line", "mode", "hour"])
    m = c.join(mode, on=["mode", "hour"])
    z = c.join(allh, on="mode")
    for col in Q + ["cancel_rate"]:
        a[col] = a[col].fillna(b[col]).fillna(m[col]).fillna(z[col]).fillna(0)
    hw = c.groupby(["line", "arr_stop", "hour"]).trip.transform("count")
    wait = a["cancel_rate"] * (3600 / hw).clip(upper=1800)
    return c, {q: (a[q] + wait).clip(upper=2540).to_numpy() for q in Q}


def main():
    con = pd.read_parquet(SRC / "connections_full.parquet")
    stops = pd.read_parquet(HERE / "data" / "stops.parquet").drop_duplicates("stop")
    dl = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(str(SRC / "delays_*.parquet")))])
    pop = pd.read_parquet(HERE / "data" / "population.parquet")

    stops = stops[stops.stop.isin(set(con.dep_stop) | set(con.arr_stop))].reset_index(drop=True)
    sid = {s: i for i, s in enumerate(stops.stop)}
    con, buf = leg_buffers(con, stops, dl)
    con["u"] = con.dep_stop.map(sid); con["v"] = con.arr_stop.map(sid)
    con["t"] = pd.factorize(con.trip)[0]
    assert len(stops) < 65536 and con.t.max() < 65536
    order = np.argsort(con.dep_s.to_numpy(), kind="stable")
    con = con.iloc[order].reset_index(drop=True)
    buf = {q: v[order] for q, v in buf.items()}

    (OUT / "c").mkdir(parents=True, exist_ok=True)
    hours = con.dep_s // 3600
    for h in sorted(hours.unique()):
        m = (hours == h).to_numpy()
        part = con[m]
        chunk = {
            "u": b64(part.u.to_numpy(np.uint16)), "v": b64(part.v.to_numpy(np.uint16)),
            "dep": b64(np.round(part.dep_s.to_numpy() / 10).astype(np.uint16)),
            "arr": b64(np.round(part.arr_s.to_numpy() / 10).astype(np.uint16)),
            "trip": b64(part.t.to_numpy(np.uint16)),
            **{q: b64(np.round(buf[q][m] / 10).astype(np.uint8)) for q in Q},
        }
        json.dump(chunk, open(OUT / "c" / f"{h:02d}.json", "w"), separators=(",", ":"))

    # population on the same grid as the office map
    rows = int(np.ceil((BOX[2] - BOX[0]) / DLAT)); cols = int(np.ceil((BOX[3] - BOX[1]) / DLON))
    pr = ((pop.lat - BOX[0]) / DLAT).astype(int); pc = ((pop.lon - BOX[1]) / DLON).astype(int)
    ok = (pr >= 0) & (pr < rows) & (pc >= 0) & (pc < cols)
    cell_pop = np.bincount((pr * cols + pc)[ok], weights=pop["pop"][ok], minlength=rows * cols)

    # average buffer per mode, hour and level (what the panel shows)
    con["h"] = (con.arr_s // 3600) % 24
    mode_delay = {}
    for lvl, q in zip(LEVELS[1:], Q):
        t = pd.DataFrame({"mode": con["mode"], "h": con.h, "d": buf[q] / 60}).groupby(["h", "mode"]).d.mean().round(1)
        mode_delay[lvl] = {int(h): t.loc[h].to_dict() for h in t.index.get_level_values(0).unique()}

    meta = {
        "box": BOX, "dlat": DLAT, "dlon": DLON, "rows": rows, "cols": cols,
        "day": "Tuesday 20 Oct 2026", "levels": LEVELS, "quantiles": Q,
        "hours": [int(h) for h in sorted(hours.unique())],
        "stop_lat": b64(stops.stop_lat.to_numpy(np.float32)), "stop_lon": b64(stops.stop_lon.to_numpy(np.float32)),
        "pop": b64(np.clip(np.round(cell_pop), 0, 65535).astype(np.uint16)),
        "mode_delay": mode_delay,
    }
    json.dump(meta, open(OUT / "meta.json", "w"), separators=(",", ":"))
    sizes = sorted((f.stat().st_size for f in (OUT / "c").glob("*.json")), reverse=True)
    print(f"{len(stops)} stops, {con.t.max() + 1} trips, {len(con)} connections; "
          f"meta {(OUT / 'meta.json').stat().st_size / 1e6:.1f} MB, biggest hour {sizes[0] / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
