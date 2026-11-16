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
import os
from pathlib import Path

import numpy as np
import pandas as pd

from build import DLAT, DLON, MIN_N

HERE = Path(__file__).resolve().parent
# one site per city: CITY=München python build_explore.py
CITY = os.environ.get("CITY", "Frankfurt")
SITES = {
    "Frankfurt": dict(box=(49.85, 8.20, 50.35, 9.00), src=HERE / "data" / "full",
                      stops=HERE / "data" / "stops.parquet", pop=HERE / "data" / "population.parquet",
                      out=HERE / "explore", page=dict(
        TITLE="Rhein-Main transit reach", URL="https://ilyas-ku.github.io/rhein-main-transit-reach/",
        KICKER_EN="Rhein-Main", KICKER_DE="Rhein-Main", REGION_EN="Rhein-Main", REGION_DE="Rhein-Main",
        IN_REGION_DE="im Rhein-Main-Gebiet", START=(50.1141, 8.6788), START_NAME="Frankfurt Hauptwache",
        EXAMPLE="Bockenheimer Warte", DAY_EN="Tuesday 20 Oct 2026", DAY_DE="Dienstag, 20.10.2026",
        AREA_EN="Frankfurt, Offenbach, Hanau, Darmstadt, Mainz, Wiesbaden and Friedberg",
        AREA_DE="Frankfurt, Offenbach, Hanau, Darmstadt, Mainz, Wiesbaden und Friedberg")),
    "München": dict(box=(47.95, 11.20, 48.40, 11.95), src=HERE / "data" / "muc",
                    stops=HERE / "data" / "muc" / "stops.parquet", pop=HERE / "data" / "muc" / "population.parquet",
                    out=HERE / "explore_muc", page=dict(
        TITLE="Munich transit reach", URL="https://ilyas-ku.github.io/munich-transit-reach/",
        KICKER_EN="Munich", KICKER_DE="München", REGION_EN="the Munich region", REGION_DE="Großraum München",
        IN_REGION_DE="im Großraum München", START=(48.1372, 11.5755), START_NAME="München Marienplatz",
        EXAMPLE="Sendlinger Tor", DAY_EN="Tuesday 10 Nov 2026", DAY_DE="Dienstag, 10.11.2026",
        AREA_EN="Munich and its surroundings, from Freising and Erding to Fürstenfeldbruck, Starnberg and Ebersberg",
        AREA_DE="München und Umland, von Freising und Erding bis Fürstenfeldbruck, Starnberg und Ebersberg")),
    "Stuttgart": dict(box=(48.60, 8.90, 48.95, 9.45), src=HERE / "data" / "str",
                      stops=HERE / "data" / "str" / "stops.parquet", pop=HERE / "data" / "str" / "population.parquet",
                      out=HERE / "explore_str", page=dict(
        TITLE="Stuttgart transit reach", URL="https://ilyas-ku.github.io/stuttgart-transit-reach/",
        KICKER_EN="Stuttgart", KICKER_DE="Stuttgart", REGION_EN="the Stuttgart region", REGION_DE="Region Stuttgart",
        IN_REGION_DE="in der Region Stuttgart", START=(48.7840, 9.1817), START_NAME="Stuttgart Hauptbahnhof",
        EXAMPLE="Charlottenplatz", DAY_EN="Tuesday 20 Oct 2026", DAY_DE="Dienstag, 20.10.2026",
        AREA_EN="Stuttgart, Esslingen, Ludwigsburg, Böblingen, Sindelfingen and Waiblingen",
        AREA_DE="Stuttgart, Esslingen, Ludwigsburg, Böblingen, Sindelfingen und Waiblingen")),
    # Düsseldorf and Köln as one region: the two collector boxes touch, and the
    # Düsseldorf box alone cuts every isochrone at the Köln border
    "Düsseldorf": dict(box=(50.80, 6.50, 51.42, 7.20), src=HERE / "data" / "dus",
                       stops=HERE / "data" / "dus" / "stops.parquet", pop=HERE / "data" / "dus" / "population.parquet",
                       out=HERE / "explore_dus", page=dict(
        TITLE="Düsseldorf & Köln transit reach", URL="https://ilyas-ku.github.io/duesseldorf-koeln-transit-reach/",
        KICKER_EN="Düsseldorf · Köln", KICKER_DE="Düsseldorf · Köln", REGION_EN="the Düsseldorf–Köln region",
        REGION_DE="Raum Düsseldorf–Köln", IN_REGION_DE="im Raum Düsseldorf–Köln", START=(51.2200, 6.7942),
        START_NAME="Düsseldorf Hauptbahnhof", EXAMPLE="Heinrich-Heine-Allee",
        DAY_EN="Tuesday 13 Oct 2026", DAY_DE="Dienstag, 13.10.2026",
        AREA_EN="Düsseldorf and Köln with their surroundings, from Krefeld and Ratingen to Leverkusen, Bergisch Gladbach and Brühl",
        AREA_DE="Düsseldorf und Köln mit Umland, von Krefeld und Ratingen bis Leverkusen, Bergisch Gladbach und Brühl")),
}
SITE = SITES[CITY]
BOX, SRC, OUT = SITE["box"], SITE["src"], SITE["out"]
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
    # keyed by mode too: a tram and its replacement bus can share line number and stop
    # (München line 25, 2026-11). Without it the join duplicates rows and every
    # buffer after the first duplicate lands on the wrong leg.
    own = dl[dl.n >= MIN_N].set_index(["stop_key", "line", "mode", "hour"])[Q + ["cancel_rate"]]
    assert own.index.is_unique, "delay table has duplicate stop/line/mode/hour keys"
    a = c.join(own, on=["stop_key", "line", "mode", "hour"])
    assert len(a) == len(c), "buffer join changed the number of legs"
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
    stops = pd.read_parquet(SITE["stops"]).drop_duplicates("stop")
    dl = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(str(SRC / "delays_*.parquet")))])
    pop = pd.read_parquet(SITE["pop"])

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
        "day": SITE["page"]["DAY_EN"], "city": CITY, "levels": LEVELS, "quantiles": Q,
        "hours": [int(h) for h in sorted(hours.unique())],
        "stop_lat": b64(stops.stop_lat.to_numpy(np.float32)), "stop_lon": b64(stops.stop_lon.to_numpy(np.float32)),
        "stop_name": stops.stop_name.fillna("").tolist(),
        "pop": b64(np.clip(np.round(cell_pop), 0, 65535).astype(np.uint16)),
        "mode_delay": mode_delay,
    }
    json.dump(meta, open(OUT / "meta.json", "w", encoding="utf-8"), separators=(",", ":"), ensure_ascii=False)
    # the page: one template for every city, filled in here
    page = (HERE / "site" / "template.html").read_text(encoding="utf-8")
    cfg = dict(SITE["page"], START_LAT=str(SITE["page"]["START"][0]), START_LON=str(SITE["page"]["START"][1]))
    for k, v in cfg.items():
        if k != "START":
            page = page.replace("{{" + k + "}}", v)
    assert "{{" not in page, "unfilled placeholder in template"
    (OUT / "index.html").write_text(page, encoding="utf-8")
    sizes = sorted((f.stat().st_size for f in (OUT / "c").glob("*.json")), reverse=True)
    print(f"{len(stops)} stops, {con.t.max() + 1} trips, {len(con)} connections; "
          f"meta {(OUT / 'meta.json').stat().st_size / 1e6:.1f} MB, biggest hour {sizes[0] / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
