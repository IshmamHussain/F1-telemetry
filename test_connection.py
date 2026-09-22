import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

# 1. Your Master Credentials
url = "http://127.0.0.1:8181"
token = "apiv3_f1_master_password_123"
org = "admin"  # Default org for v3 local
bucket = "f1_telemetry"

# 2. Connect to the Engine
print("Connecting to InfluxDB...")
client = influxdb_client.InfluxDBClient(url=url, token=token, org=org)
write_api = client.write_api(write_options=SYNCHRONOUS)

# 3. Create a test data point (Simulating a car going 320 KPH)
print("Sending test F1 data...")
point = (
    influxdb_client.Point("car_status")
    .tag("driver", "You")
    .field("speed_kph", 320)
    .field("throttle", 100)
)

# 4. Write it to the database
write_api.write(bucket=bucket, org=org, record=point)
print("Success! Check Grafana.")