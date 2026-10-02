import socket
import struct
import math
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
p_speed = 0
p_throttle = 0.0
p_brake = 0.0
p_tyre_surf_temp = [0, 0, 0, 0]
r_throttle = 0.0
r_brake = 0.0
p_tyre_wear = [0.0, 0.0, 0.0, 0.0]  # FL, FR, RL, RR

class CornerTracker:
    def __init__(self, name, center_x, center_z, radius):
        self.name = name
        self.center_x = center_x
        self.center_z = center_z
        self.radius = radius
        self.in_corner = False
        
        # Metrics
        self.entry_speed = 0
        self.min_speed = 999
        self.exit_speed = 0
        self.max_brake = 0
        self.max_throttle = 0
        self.entry_temp_fl = 0
        self.max_temp_fl = 0
        
    def update(self, x, z, speed, throttle, brake, temp_fl):
        dist = math.hypot(x - self.center_x, z - self.center_z)
        is_inside = dist <= self.radius
        result = None
        
        if is_inside and not self.in_corner:
            # Just entered
            self.in_corner = True
            self.entry_speed = speed
            self.min_speed = speed
            self.max_brake = brake
            self.max_throttle = throttle
            self.entry_temp_fl = temp_fl
            self.max_temp_fl = temp_fl
            
        elif is_inside and self.in_corner:
            # Inside corner
            self.min_speed = min(self.min_speed, speed)
            self.max_brake = max(self.max_brake, brake)
            self.max_throttle = max(self.max_throttle, throttle)
            self.max_temp_fl = max(self.max_temp_fl, temp_fl)
            self.exit_speed = speed
            
        elif not is_inside and self.in_corner:
            # Just exited
            self.in_corner = False
            temp_delta = self.max_temp_fl - self.entry_temp_fl
            
            rating = "Good"
            if temp_delta >= 3:
                rating = "Bad (High sliding/temp spike)"
            elif self.max_brake > 0.8 and self.min_speed < 50:
                rating = "Bad (Locked up/Over-slowed)"
                
            result = {
                "name": self.name,
                "entry_speed": self.entry_speed,
                "min_speed": self.min_speed,
                "exit_speed": self.exit_speed,
                "temp_delta": temp_delta,
                "max_brake": self.max_brake,
                "rating": rating
            }
        return result

# NOTE: You will need to change these X, Z coordinates and radius to match real corners on your track!
# You can find the coordinates by driving to a corner and looking at your track map X/Z values in Grafana.
turn_1 = CornerTracker("Turn 1", center_x=100.0, center_z=-50.0, radius=150.0)

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
            p_tyre_surf_temp = list(struct.unpack_from('<4B', data, p_off + 30))
            
            points.append(influxdb_client.Point("car_telemetry").tag("driver", "Player")
                .field("speed_kph", p_speed).field("throttle", p_throttle).field("brake", p_brake)
                .field("temp_fl", float(p_tyre_surf_temp[0]))
                .field("temp_fr", float(p_tyre_surf_temp[1]))
                .field("temp_rl", float(p_tyre_surf_temp[2]))
                .field("temp_rr", float(p_tyre_surf_temp[3])))

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
            p_world_x = struct.unpack_from('<f', data, p_m_off)[0]
            p_world_z = struct.unpack_from('<f', data, p_m_off + 8)[0]
            
            points.append(influxdb_client.Point("track_map").tag("driver", "Player")
                .field("world_x", p_world_x)
                .field("world_z", p_world_z)
                .field("throttle", p_throttle)
                .field("brake", p_brake))
                
            # Update Corner Tracker
            corner_result = turn_1.update(p_world_x, p_world_z, p_speed, p_throttle, p_brake, p_tyre_surf_temp[0])
            if corner_result:
                print(f"\n>>> CORNER EXIT [{corner_result['name']}] Rating: {corner_result['rating']} | Temp Spike: {corner_result['temp_delta']}C | Apex Speed: {corner_result['min_speed']}km/h")
                points.append(influxdb_client.Point("corner_analysis").tag("corner", corner_result["name"])
                    .tag("driver", "Player")
                    .field("rating", corner_result["rating"])
                    .field("temp_delta", float(corner_result["temp_delta"]))
                    .field("min_speed", float(corner_result["min_speed"]))
                    .field("entry_speed", float(corner_result["entry_speed"]))
                    .field("exit_speed", float(corner_result["exit_speed"]))
                    .field("max_brake", float(corner_result["max_brake"])))
        
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