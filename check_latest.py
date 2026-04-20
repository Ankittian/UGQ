import pandas as pd
import data_ingestion

engine = data_ingestion.get_engine()
print('--- LATEST AQI READINGS ---')
df_aqi = pd.read_sql("SELECT timestamp AT TIME ZONE 'Asia/Kolkata' as ts, temperature, humidity, co2, pm2_5, final_aqi, window_status FROM aqi_readings ORDER BY timestamp DESC LIMIT 3", engine)
print(df_aqi.transpose())

print('\n--- LATEST ENERGY SENSOR READINGS ---')
df_en = pd.read_sql("SELECT timestamp AT TIME ZONE 'Asia/Kolkata' as ts, energy_kwh, voltage, current FROM sensor_readings ORDER BY timestamp DESC LIMIT 3", engine)
print(df_en.transpose())
