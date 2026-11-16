"""Server side: the delay statistics the commute-cost view needs on top of
export_delays.py: mean delay and the 10th / 25th / 75th percentile per
stop_key / line / hour (weekdays), one hour per query.

CUTOFF keeps the sample the same as the site's existing delays_HH.parquet
(service days before the date that file was exported), so the "average day"
sits between the old quantiles. Output: OUT/cost_HH.parquet.

    CITY=Stuttgart CUTOFF=2026-11-16 OUT=/tmp/iso_str python export_cost.py 0 24
"""
import os
import sys
sys.path.insert(0, "/home/transit/frankfurt-transit-delays")
from config import connect_db

CITY = os.environ.get("CITY", "Frankfurt")
CITIES_SQL = ", ".join(f"'{c.strip()}'" for c in CITY.split(","))
CUTOFF = os.environ["CUTOFF"]
OUT = os.environ.get("OUT", "/tmp/iso")
os.makedirs(OUT, exist_ok=True)
con = connect_db(read_only=True)
con.execute("SET memory_limit='1GB'")
con.execute("SET threads=2")
HOURS = range(int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else range(5, 11)
for h in HOURS:
    con.execute(f"""
        COPY (
          SELECT stop_key, route_short_name AS line, mode, hour_local AS hour,
                 avg(CASE WHEN NOT is_cancelled THEN delay_s END) AS mean,
                 quantile_cont(CASE WHEN NOT is_cancelled THEN delay_s END, 0.10) AS q10,
                 quantile_cont(CASE WHEN NOT is_cancelled THEN delay_s END, 0.25) AS q25,
                 quantile_cont(CASE WHEN NOT is_cancelled THEN delay_s END, 0.75) AS q75
          FROM fact_valid
          WHERE city IN ({CITIES_SQL}) AND dow BETWEEN 1 AND 5 AND hour_local = {h}
            AND service_date < DATE '{CUTOFF}'
            AND stop_key IS NOT NULL AND (delay_s IS NULL OR abs(delay_s) <= 7200)
          GROUP BY 1, 2, 3, 4
        ) TO '{OUT}/cost_{h:02d}.parquet' (FORMAT parquet)""")
    print(h, con.execute(f"SELECT count(*) FROM '{OUT}/cost_{h:02d}.parquet'").fetchone()[0], flush=True)
