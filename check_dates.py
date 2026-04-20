import pandas as pd
import data_ingestion

engine = data_ingestion.get_engine()
query = "SELECT timestamp AT TIME ZONE 'Asia/Kolkata' as ts, temperature FROM aqi_readings WHERE temperature IS NOT NULL AND timestamp >= '2026-04-17 00:00:00' ORDER BY timestamp ASC LIMIT 10"
df = pd.read_sql(query, engine)
print(df)
