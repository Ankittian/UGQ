import pandas as pd
import sqlalchemy
engine = sqlalchemy.create_engine('postgresql://neondb_owner:npg_JBplV4btz2Fa@ep-summer-tree-a1qoan7c-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require')
df = pd.read_sql("SELECT timestamp, timestamp AT TIME ZONE 'Asia/Kolkata' AS ist_correct FROM aqi_readings ORDER BY timestamp DESC LIMIT 5", engine)
print("dtypes:")
print(df.dtypes)
print("\ndf:")
print(df.head())
