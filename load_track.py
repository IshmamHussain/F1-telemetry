"""
F1 Track Layout Loader
======================
Fetches official track coordinates from FastF1 and loads them into InfluxDB.
The track outline is stored as a `track_layout` measurement that Grafana's
Plotly panel can render as a persistent reference line underneath live car data.

Usage:
    python load_track.py                     # Interactive circuit picker
    python load_track.py --circuit monza     # Load a specific circuit
    python load_track.py --list              # Show all available circuits
    python load_track.py --year 2025 --gp 5  # Load by GP round number
"""

import argparse
import sys
import time

import fastf1
import numpy as np
# pyrefly: ignore [missing-import]
import influxdb_client
# pyrefly: ignore [missing-import]
from influxdb_client.client.write_api import WriteOptions

# --- DB CONFIG (same as live_telemetry.py) ---
DB_URL = "http://127.0.0.1:8181"
DB_TOKEN = "apiv3_f1_master_password_123"
DB_ORG = "admin"
DB_BUCKET = "f1_telemetry"

# --- CIRCUIT LOOKUP TABLE ---
# Maps friendly names to (year, round_number) for FastF1
CIRCUITS = {
    "bahrain":       (2025, 1),
    "saudi":         (2025, 2),
    "australia":     (2025, 3),
    "japan":         (2025, 4),
    "china":         (2025, 5),
    "miami":         (2025, 6),
    "imola":         (2025, 7),
    "monaco":        (2025, 8),
    "spain":         (2025, 9),
    "canada":        (2025, 10),
    "austria":       (2025, 11),
    "silverstone":   (2025, 12),
    "belgium":       (2025, 13),
    "hungary":       (2025, 14),
    "netherlands":   (2025, 15),
    "monza":         (2025, 16),
    "azerbaijan":    (2025, 17),
    "singapore":     (2025, 18),
    "usa":           (2025, 19),
    "mexico":        (2025, 20),
    "brazil":        (2025, 21),
    "vegas":         (2025, 22),
    "qatar":         (2025, 23),
    "abudhabi":      (2025, 24),
}


def fetch_track_coordinates(year, round_num):
    """
    Fetch track X/Y coordinates from FastF1 using the fastest lap's telemetry.
    Returns a numpy array of shape (N, 2) with columns [x, y].
    """
    print(f"  Fetching session data for {year} Round {round_num}...")

    # Enable caching so subsequent loads are instant
    fastf1.Cache.enable_cache("fastf1_cache")

    # Try Race first, fall back to Qualifying, then FP1
    for session_type in ["R", "Q", "FP1"]:
        try:
            session = fastf1.get_session(year, round_num, session_type)
            session.load(telemetry=True, weather=False, messages=False)

            # Get the fastest lap for clean track coordinates
            fastest = session.laps.pick_fastest()
            if fastest is None or fastest.empty if hasattr(fastest, 'empty') else fastest is None:
                continue

            tel = fastest.get_telemetry()
            if tel is None or tel.empty:
                continue

            x = tel["X"].values
            y = tel["Y"].values

            # Close the loop (connect last point to first)
            x = np.append(x, x[0])
            y = np.append(y, y[0])

            print(f"  ✓ Got {len(x)} track points from {session_type} session")
            return np.column_stack([x, y]), session.event["EventName"]

        except Exception as e:
            print(f"  ⚠ {session_type} failed: {e}")
            continue

    return None, None


def load_into_influxdb(coords, circuit_name):
    """
    Write track coordinates into InfluxDB as the `track_layout` measurement.
    Each point gets a sequence index so the line renders in order.
    """
    client = influxdb_client.InfluxDBClient(url=DB_URL, token=DB_TOKEN, org=DB_ORG)
    write_api = client.write_api(write_options=WriteOptions(batch_size=500, flush_interval=1000))

    # First, delete any existing track_layout data for this circuit
    delete_api = client.delete_api()
    try:
        delete_api.delete(
            start="1970-01-01T00:00:00Z",
            stop="2100-01-01T00:00:00Z",
            predicate=f'_measurement="track_layout" AND circuit="{circuit_name}"',
            bucket=DB_BUCKET,
            org=DB_ORG,
        )
        print(f"  Cleared old layout data for {circuit_name}")
    except Exception:
        pass  # InfluxDB 3 may not support delete — that's OK, we'll overwrite

    # Write each coordinate point with a unique timestamp
    # Using fake timestamps spaced 1ms apart to maintain ordering
    base_time = 946684800_000_000_000  # 2000-01-01 in nanoseconds
    points = []

    for i, (x, y) in enumerate(coords):
        p = (
            influxdb_client.Point("track_layout")
            .tag("circuit", circuit_name)
            .field("x", float(x))
            .field("y", float(y))
            .field("seq", i)
            .time(base_time + (i * 1_000_000))  # 1ms apart
        )
        points.append(p)

    write_api.write(bucket=DB_BUCKET, org=DB_ORG, record=points)
    write_api.flush()
    write_api.close()
    client.close()

    print(f"  ✓ Wrote {len(points)} track layout points to InfluxDB")


def save_track_json(coords, circuit_name):
    """
    Also save as a local JSON file for backup / Grafana static data source.
    """
    import json
    output = {
        "circuit": circuit_name,
        "point_count": len(coords),
        "coordinates": [{"x": float(x), "y": float(y), "seq": i} for i, (x, y) in enumerate(coords)],
    }
    filename = f"tracks/{circuit_name.lower().replace(' ', '_')}.json"

    import os
    os.makedirs("tracks", exist_ok=True)

    with open(filename, "w") as f:
        json.dump(output, f, indent=2)

    print(f"  ✓ Saved backup to {filename}")


def interactive_picker():
    """Show a nice menu for picking a circuit."""
    print("\n╔══════════════════════════════════════╗")
    print("║     F1 TRACK LAYOUT LOADER           ║")
    print("╚══════════════════════════════════════╝\n")
    print("Available circuits:\n")

    names = list(CIRCUITS.keys())
    for i, name in enumerate(names, 1):
        year, rnd = CIRCUITS[name]
        print(f"  {i:2d}. {name.capitalize():15s}  (Round {rnd})")

    print(f"\n  {'A':>2s}. Load ALL circuits")
    print()

    choice = input("Pick a circuit (number, name, or 'A' for all): ").strip().lower()

    if choice == "a":
        return list(CIRCUITS.keys())

    # Try as number
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(names):
            return [names[idx]]
    except ValueError:
        pass

    # Try as name
    if choice in CIRCUITS:
        return [choice]

    # Fuzzy match
    matches = [n for n in names if choice in n]
    if matches:
        return matches

    print(f"  ✗ Unknown circuit: {choice}")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Load F1 track layouts into InfluxDB")
    parser.add_argument("--circuit", "-c", help="Circuit name (e.g. 'monza', 'silverstone')")
    parser.add_argument("--year", "-y", type=int, default=2025, help="Season year")
    parser.add_argument("--gp", "-g", type=int, help="GP round number")
    parser.add_argument("--list", "-l", action="store_true", help="List available circuits")
    parser.add_argument("--all", "-a", action="store_true", help="Load all circuits")
    parser.add_argument("--no-db", action="store_true", help="Save JSON only, skip InfluxDB")
    args = parser.parse_args()

    if args.list:
        print("\nAvailable circuits:")
        for name, (year, rnd) in CIRCUITS.items():
            print(f"  {name:15s}  {year} Round {rnd}")
        return

    # Determine which circuits to load
    if args.all:
        circuits_to_load = list(CIRCUITS.keys())
    elif args.circuit:
        key = args.circuit.lower().replace(" ", "")
        if key in CIRCUITS:
            circuits_to_load = [key]
        else:
            matches = [n for n in CIRCUITS if key in n]
            if matches:
                circuits_to_load = matches
            else:
                print(f"Unknown circuit: {args.circuit}")
                print("Use --list to see available circuits")
                sys.exit(1)
    elif args.gp:
        circuits_to_load = [("custom", args.year, args.gp)]
    else:
        circuits_to_load = interactive_picker()

    # Load each circuit
    total = len(circuits_to_load)
    for i, circuit in enumerate(circuits_to_load, 1):
        if isinstance(circuit, tuple):
            _, year, rnd = circuit
            circuit_key = f"round_{rnd}"
        else:
            circuit_key = circuit
            year, rnd = CIRCUITS[circuit]

        print(f"\n[{i}/{total}] Loading {circuit_key.upper()}...")

        coords, event_name = fetch_track_coordinates(year, rnd)
        if coords is None:
            print(f"  ✗ Could not fetch track data for {circuit_key}")
            continue

        circuit_label = event_name or circuit_key.capitalize()

        # Save JSON backup
        save_track_json(coords, circuit_label)

        # Load into InfluxDB
        if not args.no_db:
            try:
                load_into_influxdb(coords, circuit_label)
            except Exception as e:
                print(f"  ✗ InfluxDB write failed: {e}")
                print("  (JSON backup was saved successfully)")

        if i < total:
            time.sleep(1)  # Be nice to the FastF1 API

    print("\n══════════════════════════════════════")
    print("  All done! Track layouts are ready.")
    print("  Open Grafana → Import dashboard JSON")
    print("══════════════════════════════════════\n")


if __name__ == "__main__":
    main()
