"""Server side: weekday delay quantiles per stop_key / line / hour, one hour at a time.

All hours in one GROUP BY took the box to OOM on 2026-10-17 (quantile states
cannot spill), so each morning hour is its own small query and file.
"""
import sys
sys.path.insert(0, "/home/transit/frankfurt-transit-delays")
from config import connect_db

OUT = "/tmp/iso"
con = connect_db(read_only=True)
con.execute("SET memory_limit='1GB'")
con.execute("SET threads=2")
HOURS = range(int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else range(5, 11)
for h in HOURS:
    con.execute(f"""
        COPY (
          SELECT stop_key, route_short_name AS line, mode, hour_local AS hour, count(*) AS n,
                 avg(CASE WHEN is_cancelled THEN 1.0 ELSE 0.0 END) AS cancel_rate,
                 quantile_cont(CASE WHEN NOT is_cancelled THEN delay_s END, 0.5) AS q50,
                 quantile_cont(CASE WHEN NOT is_cancelled THEN delay_s END, 0.8) AS q80,
                 quantile_cont(CASE WHEN NOT is_cancelled THEN delay_s END, 0.9) AS q90,
                 quantile_cont(CASE WHEN NOT is_cancelled THEN delay_s END, 0.95) AS q95
          FROM fact_valid
          WHERE city = 'Frankfurt' AND dow BETWEEN 1 AND 5 AND hour_local = {h}
            AND stop_key IS NOT NULL AND (delay_s IS NULL OR abs(delay_s) <= 7200)
          GROUP BY 1, 2, 3, 4
        ) TO '{OUT}/delays_{h:02d}.parquet' (FORMAT parquet)""")
    print(h, con.execute(f"SELECT count(*), sum(n) FROM '{OUT}/delays_{h:02d}.parquet'").fetchone(), flush=True)
