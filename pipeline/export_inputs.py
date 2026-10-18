"""Server side: export what the isochrone needs, as parquet in /tmp/iso/.

Usage: export_inputs.py [FROM_H TO_H]  (default 4.5 11; full day: 3 28)

  connections.parquet  every timetabled hop (stop -> next stop) of trips that
                       run on DAY, Frankfurt metro box, 04:30-11:00
  stops.parquet        stops of that timetable with stop_key and coordinates
  (delays: export_delays.py, one hour per query)
"""
import sys
sys.path.insert(0, "/home/transit/frankfurt-transit-delays")
from config import connect_db

DAY = "20261020"                       # Tuesday, first school day after the Hessian break
FROM_H, TO_H = (float(sys.argv[1]), float(sys.argv[2])) if len(sys.argv) > 2 else (4.5, 11)
SUFFIX = "" if len(sys.argv) <= 2 else "_full"
OUT = "/tmp/iso"
con = connect_db(read_only=True)
con.execute("SET memory_limit='1GB'")
con.execute("SET threads=2")
ver = con.execute("SELECT ref_version FROM ref_versions ORDER BY loaded_at_utc DESC LIMIT 1").fetchone()[0]
print("version", ver)
con.execute(f"""
    CREATE TEMP TABLE active AS
    WITH cal AS (
        SELECT feed, service_id FROM ffm_calendar
        WHERE ref_version = '{ver}' AND tuesday = 1 AND start_date <= '{DAY}' AND end_date >= '{DAY}'
        UNION
        SELECT feed, service_id FROM ffm_calendar_dates
        WHERE ref_version = '{ver}' AND date = '{DAY}' AND exception_type = 1
    )
    SELECT c.* FROM cal c
    WHERE NOT EXISTS (SELECT 1 FROM ffm_calendar_dates x WHERE x.ref_version = '{ver}' AND x.feed = c.feed
                      AND x.service_id = c.service_id AND x.date = '{DAY}' AND x.exception_type = 2)
""")
sec = lambda col: (f"(CAST(split_part({col},':',1) AS INT)*3600 + CAST(split_part({col},':',2) AS INT)*60"
                   f" + CAST(split_part({col},':',3) AS INT))")
con.execute(f"""
    CREATE TEMP TABLE st AS
    SELECT t.feed, t.trip_id, t.route_short_name, t.mode, st.stop_id, st.stop_sequence,
           {sec('coalesce(st.arrival_time, st.departure_time)')} AS arr_s,
           {sec('coalesce(st.departure_time, st.arrival_time)')} AS dep_s
    FROM ffm_trips t
    JOIN active a ON a.feed = t.feed AND a.service_id = t.service_id
    JOIN ffm_stop_times st ON st.ref_version = t.ref_version AND st.feed = t.feed AND st.trip_id = t.trip_id
    JOIN ffm_stops s ON s.ref_version = st.ref_version AND s.feed = st.feed AND s.stop_id = st.stop_id
    WHERE t.ref_version = '{ver}' AND s.city = 'Frankfurt'
""")
con.execute(f"""
    COPY (
      SELECT * FROM (
        SELECT feed || ':' || trip_id AS trip, route_short_name AS line, mode,
               feed || ':' || stop_id AS dep_stop, dep_s,
               lead(feed || ':' || stop_id) OVER w AS arr_stop, lead(arr_s) OVER w AS arr_s
        FROM st WINDOW w AS (PARTITION BY feed, trip_id ORDER BY stop_sequence))
      WHERE arr_stop IS NOT NULL AND dep_s >= {FROM_H}*3600 AND arr_s <= {TO_H}*3600
    ) TO '{OUT}/connections{SUFFIX}.parquet' (FORMAT parquet)""")
con.execute(f"""
    COPY (SELECT DISTINCT s.feed || ':' || s.stop_id AS stop, s.stop_key, s.stop_name, s.stop_lat, s.stop_lon
          FROM ffm_stops s WHERE s.ref_version = '{ver}' AND s.city = 'Frankfurt')
    TO '{OUT}/stops.parquet' (FORMAT parquet)""")
for f in [f"connections{SUFFIX}", "stops"]:
    print(f, con.execute(f"SELECT count(*) FROM '{OUT}/{f}.parquet'").fetchone()[0])
