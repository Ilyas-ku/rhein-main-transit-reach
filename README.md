# Rhein-Main transit reach — reliability-adjusted isochrones from any point

Interactive map: **https://ilyas-ku.github.io/rhein-main-transit-reach/**

Pick any point in Rhein-Main and a departure time between 05:00 and 23:00: the
page shows where public transport takes you within 20–60 minutes, door to door,
and how far that reach shrinks once real delays and cancellations are counted.

- **Timetable:** GTFS for Rhein-Main (DELFI via gtfs.de), weekday (Tuesday 20 Oct 2026),
  split into hourly chunks that the browser loads on demand.
- **Delays:** my own collector of operators' GTFS-Realtime feed, weekday observations
  since 24 Sep 2026, per stop, line and hour (median, 80th, 90th, 95th percentile,
  cancellation rate).
- **Routing:** a forward Connection Scan runs in the browser for every change;
  walking 4.8 km/h on a 1.3× detour, ≥2 min per transfer.
- **Reliability levels:** every leg's arrival is pushed back by the delay quantile for
  that stop/line/hour plus the expected wait from cancellations; the next vehicle is
  assumed on time, so missed connections are part of the result.
- **Population:** Zensus 2022, 100 m grid.

`pipeline/`: `export_inputs.py 3 28` and `export_delays.py 0 24` run against the
collector's DuckDB, `build_explore.py` packs `meta.json` and `c/HH.json`.

Data: DELFI / gtfs.de (CC BY-SA 4.0), Zensus 2022 (dl-de/by-2-0),
© OpenStreetMap contributors, geocoding Nominatim, basemap © Esri.
