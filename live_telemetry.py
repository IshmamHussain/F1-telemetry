import socket
import struct
# pyrefly: ignore [missing-import]
import influxdb_client
# pyrefly: ignore [missing-import]
from influxdb_client.client.write_api import WriteOptions

# --- DB CONFIG ---
url = "http://127.0.0.1:8181"
token = "apiv3_f1_master_password_123"
org = "admin"
bucket = "f1_telemetry"

client = influxdb_client.InfluxDBClient(url=url, token=token, org=org)
write_api = client.write_api(write_options=WriteOptions(batch_size=200, flush_interval=500))

# --- UDP CONFIG ---
UDP_IP = "127.0.0.1" 
UDP_PORT = 20777
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

# Data Sync Variables
found_rival_id = -1
p_throttle = 0.0
p_brake = 0.0
r_throttle = 0.0
r_brake = 0.0
p_tyre_wear = [0.0, 0.0, 0.0, 0.0]  # FL, FR, RL, RR

print("--- F1 25 MASTER ENGINE: TRACK MAP MODE ---")

while True:
    try:
        data, addr = sock.recvfrom(2048)
        packet_id = data[6]
        player_idx = data[27]
        points = []

        # PACKET 6: TELEMETRY (Capture Braking)
        if packet_id == 6:
            p_off = 29 + (player_idx * 60)
            p_speed = struct.unpack_from('<H', data, p_off)[0]
            p_throttle = struct.unpack_from('<f', data, p_off + 2)[0]
            p_brake = struct.unpack_from('<f', data, p_off + 10)[0]
            
            points.append(influxdb_client.Point("car_telemetry").tag("driver", "Player")
                .field("speed_kph", p_speed).field("throttle", p_throttle).field("brake", p_brake))

            # Auto-Detect Rival
            for i in range(22):
                if i == player_idx: continue
                r_off = 29 + (i * 60)
                r_speed = struct.unpack_from('<H', data, r_off)[0]
                
                if r_speed > 10 and r_speed != 115:
                    found_rival_id = i
                    r_throttle = struct.unpack_from('<f', data, r_off + 2)[0]
                    r_brake = struct.unpack_from('<f', data, r_off + 10)[0]
                    points.append(influxdb_client.Point("car_telemetry").tag("driver", "Rival")
                        .field("speed_kph", r_speed).field("throttle", r_throttle).field("brake", r_brake))
                    break 

            print(f"LIVE | Player: {p_speed}km/h | Rival: {r_speed if found_rival_id != -1 else 0}km/h | Tyres: FL={p_tyre_wear[0]:.1f}% FR={p_tyre_wear[1]:.1f}% RL={p_tyre_wear[2]:.1f}% RR={p_tyre_wear[3]:.1f}%    ", end='\r')

        # PACKET 10: CAR DAMAGE (Capture Tyre Wear)
        elif packet_id == 10:
            # CarDamageData: tyresWear (4 floats) then tyresDamage (4 uint8s)
            p_dmg_off = 29 + (player_idx * 46)
            p_tyre_wear = list(struct.unpack_from('<4f', data, p_dmg_off))
            p_tyre_dmg = list(struct.unpack_from('<4B', data, p_dmg_off + 16))

            points.append(influxdb_client.Point("tyre_wear").tag("driver", "Player")
                .field("fl", p_tyre_wear[0])
                .field("fr", p_tyre_wear[1])
                .field("rl", p_tyre_wear[2])
                .field("rr", p_tyre_wear[3]))
                
            points.append(influxdb_client.Point("tyre_damage").tag("driver", "Player")
                .field("fl", float(p_tyre_dmg[0]))
                .field("fr", float(p_tyre_dmg[1]))
                .field("rl", float(p_tyre_dmg[2]))
                .field("rr", float(p_tyre_dmg[3])))

        # PACKET 0: MOTION (Capture GPS + Sync Brake) [ENABLED]
        elif packet_id == 0:
            # Player Map Data
            p_m_off = 29 + (player_idx * 60)
            points.append(influxdb_client.Point("track_map").tag("driver", "Player")
                .field("world_x", struct.unpack_from('<f', data, p_m_off)[0])
                .field("world_z", struct.unpack_from('<f', data, p_m_off + 8)[0])
                .field("throttle", p_throttle)
                .field("brake", p_brake))
        
            # Rival Map Data
            if found_rival_id != -1:
                r_m_off = 29 + (found_rival_id * 60)
                points.append(influxdb_client.Point("track_map").tag("driver", "Rival")
                    .field("world_x", struct.unpack_from('<f', data, r_m_off)[0])
                    .field("world_z", struct.unpack_from('<f', data, r_m_off + 8)[0])
                    .field("throttle", r_throttle)
                    .field("brake", r_brake))

        if points:
            write_api.write(bucket=bucket, org=org, record=points)
            
    except Exception:
        pass