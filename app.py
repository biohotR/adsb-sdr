from flask import Flask, jsonify, render_template
import json
import logging
import math
import os
import random
import sqlite3
import threading
import time
from collections import deque

import requests

DEFAULT_DUMP1090_JSON_PATH = "/tmp/dump1090/aircraft.json"
DEFAULT_METADATA_API_URL = "https://api.airplanes.live/v2/hex/{hex_code}"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 3
DEFAULT_MAX_HISTORY_POINTS = 120
DEFAULT_SIMULATED_AIRCRAFT_COUNT = 10
DEFAULT_AUTO_SIMULATION_WHEN_NO_FEED = True
DEFAULT_DB_PATH = "data/signal_tracker.db"
DEFAULT_HISTORY_RETENTION_HOURS = 24

DUMP1090_JSON_PATH = os.getenv("DUMP1090_JSON_PATH", DEFAULT_DUMP1090_JSON_PATH)
METADATA_API_URL = os.getenv("METADATA_API_URL", DEFAULT_METADATA_API_URL)
REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("REQUEST_TIMEOUT_SECONDS", DEFAULT_REQUEST_TIMEOUT_SECONDS)
)
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5001"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"
MAX_HISTORY_POINTS = int(os.getenv("MAX_HISTORY_POINTS", DEFAULT_MAX_HISTORY_POINTS))
SIMULATION_MODE = os.getenv("SIMULATION_MODE", "false").lower() == "true"
SIMULATED_AIRCRAFT_COUNT = int(
    os.getenv("SIMULATED_AIRCRAFT_COUNT", DEFAULT_SIMULATED_AIRCRAFT_COUNT)
)
SIMULATION_CENTER_LAT = float(os.getenv("SIMULATION_CENTER_LAT", "44.4268"))
SIMULATION_CENTER_LON = float(os.getenv("SIMULATION_CENTER_LON", "26.1025"))
AUTO_SIMULATION_WHEN_NO_FEED = (
    os.getenv("AUTO_SIMULATION_WHEN_NO_FEED", str(DEFAULT_AUTO_SIMULATION_WHEN_NO_FEED))
    .lower()
    == "true"
)
DB_PATH = os.getenv("DB_PATH", DEFAULT_DB_PATH)
HISTORY_RETENTION_HOURS = int(
    os.getenv("HISTORY_RETENTION_HOURS", DEFAULT_HISTORY_RETENTION_HOURS)
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("signal_tracker")

AIRCRAFT_DB = {}
AIRCRAFT_DB_LOCK = threading.Lock()
LAST_METADATA_ERROR = None
AIRCRAFT_HISTORY = {}
AIRCRAFT_STATS = {}
SIMULATED_AIRCRAFT = []
SIM_RANDOM = random.Random(1090)
SIM_LAST_UPDATE = None
DB_LOCK = threading.Lock()
PERSIST_WRITE_COUNT = 0


def set_last_metadata_error(message):
    global LAST_METADATA_ERROR
    LAST_METADATA_ERROR = message


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with get_db_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS aircraft_metadata (
                hex TEXT PRIMARY KEY,
                airline TEXT,
                type TEXT,
                registration TEXT,
                category TEXT,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS aircraft_stats (
                hex TEXT PRIMARY KEY,
                first_seen REAL,
                last_seen REAL,
                max_altitude REAL,
                max_speed REAL,
                total_distance_km REAL,
                samples INTEGER,
                last_lat REAL,
                last_lon REAL,
                last_position_ts REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS aircraft_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hex TEXT NOT NULL,
                ts REAL NOT NULL,
                lat REAL,
                lon REAL,
                altitude REAL,
                speed REAL,
                track REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_aircraft_history_hex_ts ON aircraft_history(hex, ts)"
        )


def prune_old_history(conn):
    cutoff = time.time() - (HISTORY_RETENTION_HOURS * 3600)
    conn.execute("DELETE FROM aircraft_history WHERE ts < ?", (cutoff,))


def persist_metadata(hex_code, metadata):
    with DB_LOCK:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO aircraft_metadata(hex, airline, type, registration, category, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(hex) DO UPDATE SET
                    airline=excluded.airline,
                    type=excluded.type,
                    registration=excluded.registration,
                    category=excluded.category,
                    updated_at=excluded.updated_at
                """,
                (
                    hex_code,
                    metadata.get("airline"),
                    metadata.get("type"),
                    metadata.get("registration"),
                    metadata.get("category"),
                    time.time(),
                ),
            )


def persist_history_and_stats(hex_code, point, stats):
    global PERSIST_WRITE_COUNT

    with DB_LOCK:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO aircraft_history(hex, ts, lat, lon, altitude, speed, track)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hex_code,
                    point.get("timestamp"),
                    point.get("lat"),
                    point.get("lon"),
                    point.get("altitude"),
                    point.get("speed"),
                    point.get("track"),
                ),
            )
            last_position = stats.get("last_position") or {}
            conn.execute(
                """
                INSERT INTO aircraft_stats(
                    hex, first_seen, last_seen, max_altitude, max_speed,
                    total_distance_km, samples, last_lat, last_lon, last_position_ts
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(hex) DO UPDATE SET
                    first_seen=excluded.first_seen,
                    last_seen=excluded.last_seen,
                    max_altitude=excluded.max_altitude,
                    max_speed=excluded.max_speed,
                    total_distance_km=excluded.total_distance_km,
                    samples=excluded.samples,
                    last_lat=excluded.last_lat,
                    last_lon=excluded.last_lon,
                    last_position_ts=excluded.last_position_ts
                """,
                (
                    hex_code,
                    stats.get("first_seen"),
                    stats.get("last_seen"),
                    stats.get("max_altitude"),
                    stats.get("max_speed"),
                    stats.get("total_distance_km", 0.0),
                    stats.get("samples", 0),
                    last_position.get("lat"),
                    last_position.get("lon"),
                    last_position.get("timestamp"),
                ),
            )
            PERSIST_WRITE_COUNT += 1
            if PERSIST_WRITE_COUNT % 100 == 0:
                prune_old_history(conn)


def load_persisted_state():
    with DB_LOCK:
        with get_db_connection() as conn:
            metadata_rows = conn.execute(
                "SELECT hex, airline, type, registration, category FROM aircraft_metadata"
            ).fetchall()
            for row in metadata_rows:
                AIRCRAFT_DB[row["hex"]] = {
                    "airline": row["airline"] or "N/A",
                    "type": row["type"] or "N/A",
                    "registration": row["registration"] or "N/A",
                    "category": row["category"] or "N/A",
                }

            stats_rows = conn.execute(
                """
                SELECT
                    hex, first_seen, last_seen, max_altitude, max_speed,
                    total_distance_km, samples, last_lat, last_lon, last_position_ts
                FROM aircraft_stats
                """
            ).fetchall()
            for row in stats_rows:
                AIRCRAFT_STATS[row["hex"]] = {
                    "first_seen": row["first_seen"],
                    "last_seen": row["last_seen"],
                    "max_altitude": row["max_altitude"],
                    "max_speed": row["max_speed"],
                    "total_distance_km": row["total_distance_km"] or 0.0,
                    "samples": row["samples"] or 0,
                    "last_position": {
                        "lat": row["last_lat"],
                        "lon": row["last_lon"],
                        "timestamp": row["last_position_ts"],
                    }
                    if row["last_lat"] is not None and row["last_lon"] is not None
                    else None,
                }

            history_rows = conn.execute(
                """
                SELECT hex, ts, lat, lon, altitude, speed, track
                FROM aircraft_history
                ORDER BY ts DESC
                """
            ).fetchall()
            grouped = {}
            for row in history_rows:
                bucket = grouped.setdefault(row["hex"], [])
                if len(bucket) < MAX_HISTORY_POINTS:
                    bucket.append(
                        {
                            "timestamp": row["ts"],
                            "lat": row["lat"],
                            "lon": row["lon"],
                            "altitude": row["altitude"],
                            "speed": row["speed"],
                            "track": row["track"],
                        }
                    )

            for hex_code, points in grouped.items():
                AIRCRAFT_HISTORY[hex_code] = deque(reversed(points), maxlen=MAX_HISTORY_POINTS)


def initialize_simulated_aircraft():
    global SIMULATED_AIRCRAFT

    templates = [
        ("RYR", "B738", "Boeing 737-800", "Ryanair", "LROP", "LCLK"),
        ("WZZ", "A320", "Airbus A320", "Wizz Air", "LROP", "EDDF"),
        ("TAR", "A318", "Airbus A318", "TAROM", "LROP", "LFPG"),
        ("QTR", "B789", "Boeing 787-9", "Qatar Airways", "OTHH", "LROP"),
        ("DLH", "A321", "Airbus A321", "Lufthansa", "EDDM", "LROP"),
        ("BAW", "A319", "Airbus A319", "British Airways", "EGLL", "LROP"),
        ("UAE", "B77W", "Boeing 777-300ER", "Emirates", "OMDB", "LROP"),
        ("KLM", "B739", "Boeing 737-900", "KLM", "EHAM", "LROP"),
        ("EZY", "A320", "Airbus A320", "easyJet", "EGGW", "LROP"),
        ("THY", "B38M", "Boeing 737 MAX 8", "Turkish Airlines", "LTFM", "LROP"),
    ]

    fleet = []
    for i in range(SIMULATED_AIRCRAFT_COUNT):
        tpl = templates[i % len(templates)]
        heading = SIM_RANDOM.uniform(0, 360)
        speed = SIM_RANDOM.uniform(280, 470)
        altitude = SIM_RANDOM.uniform(9000, 39000)
        radius_deg = SIM_RANDOM.uniform(0.05, 0.45)
        angle = SIM_RANDOM.uniform(0, 360)
        hex_code = f"{0x700000 + i:06x}"

        flight_num = 100 + i * 7
        fleet.append(
            {
                "hex": hex_code,
                "flight": f"{tpl[0]}{flight_num}",
                "t": tpl[1],
                "desc": tpl[2],
                "airline_name": tpl[3],
                "origin": tpl[4],
                "destination": tpl[5],
                "r": f"YR-S{i:02d}",
                "category": "A3",
                "messages": SIM_RANDOM.randint(1500, 98000),
                "squawk": str(SIM_RANDOM.randint(1000, 7777)),
                "emergency": "none",
                "radius_deg": radius_deg,
                "angle": angle,
                "track": heading,
                "gs": speed,
                "alt_baro": altitude,
                "baro_rate": SIM_RANDOM.choice([-1400, -800, 0, 500, 1100]),
                "rssi": round(SIM_RANDOM.uniform(-31, -7), 1),
                "seen": round(SIM_RANDOM.uniform(0.1, 2.5), 1),
                "nav_qnh": SIM_RANDOM.choice([1008, 1012, 1015, 1018]),
            }
        )

    SIMULATED_AIRCRAFT = fleet


def generate_simulated_data():
    global SIM_LAST_UPDATE

    if not SIMULATED_AIRCRAFT:
        initialize_simulated_aircraft()

    now_ts = time.time()
    dt = 1.0 if SIM_LAST_UPDATE is None else max(0.2, min(now_ts - SIM_LAST_UPDATE, 3.0))
    SIM_LAST_UPDATE = now_ts

    aircraft = []
    for plane in SIMULATED_AIRCRAFT:
        turn = SIM_RANDOM.uniform(-3.5, 3.5)
        plane["track"] = (plane["track"] + turn) % 360
        plane["angle"] = (plane["angle"] + (plane["gs"] / 900) * dt) % 360

        rad = math.radians(plane["angle"])
        lat = SIMULATION_CENTER_LAT + plane["radius_deg"] * math.cos(rad)
        lon = SIMULATION_CENTER_LON + plane["radius_deg"] * math.sin(rad)

        plane["baro_rate"] = SIM_RANDOM.choice([-1300, -700, -300, 0, 250, 650, 1200])
        plane["alt_baro"] = max(2500, min(43000, plane["alt_baro"] + plane["baro_rate"] * dt / 60))
        plane["gs"] = max(180, min(520, plane["gs"] + SIM_RANDOM.uniform(-9, 9)))
        plane["messages"] += SIM_RANDOM.randint(2, 12)
        plane["seen"] = round(SIM_RANDOM.uniform(0.0, 1.4), 1)
        plane["lat"] = round(lat, 5)
        plane["lon"] = round(lon, 5)

        enriched = {
            "hex": plane["hex"],
            "flight": plane["flight"],
            "t": plane["t"],
            "desc": plane["desc"],
            "airline_name": plane["airline_name"],
            "origin": plane["origin"],
            "destination": plane["destination"],
            "r": plane["r"],
            "category": plane["category"],
            "messages": plane["messages"],
            "squawk": plane["squawk"],
            "emergency": plane["emergency"],
            "track": round(plane["track"], 1),
            "gs": round(plane["gs"], 1),
            "alt_baro": round(plane["alt_baro"], 1),
            "baro_rate": plane["baro_rate"],
            "rssi": plane["rssi"],
            "seen": plane["seen"],
            "nav_qnh": plane["nav_qnh"],
            "lat": plane["lat"],
            "lon": plane["lon"],
            "airline": plane["airline_name"],
            "type": plane["desc"],
        }
        update_aircraft_history(enriched)
        aircraft.append(enriched)

    return {"now": int(now_ts), "messages": sum(p["messages"] for p in SIMULATED_AIRCRAFT), "aircraft": aircraft}


def is_simulation_active():
    if SIMULATION_MODE:
        return True
    if AUTO_SIMULATION_WHEN_NO_FEED and not os.path.exists(DUMP1090_JSON_PATH):
        return True
    return False


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def haversine_km(lat1, lon1, lat2, lon2):
    radius_km = 6371.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    d_lat = lat2_rad - lat1_rad
    d_lon = lon2_rad - lon1_rad
    a_val = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(d_lon / 2) ** 2
    )
    c_val = 2 * math.atan2(math.sqrt(a_val), math.sqrt(1 - a_val))
    return radius_km * c_val


def update_aircraft_history(plane):
    hex_code = plane.get("hex", "").lower()
    if not hex_code:
        return

    now_ts = time.time()
    lat = to_float(plane.get("lat"))
    lon = to_float(plane.get("lon"))
    altitude = to_float(plane.get("alt_baro"))
    speed = to_float(plane.get("gs"))
    track = to_float(plane.get("track"))

    persist_point = None
    stats_snapshot = None
    with AIRCRAFT_DB_LOCK:
        stats = AIRCRAFT_STATS.setdefault(
            hex_code,
            {
                "first_seen": now_ts,
                "last_seen": now_ts,
                "max_altitude": None,
                "max_speed": None,
                "total_distance_km": 0.0,
                "samples": 0,
                "last_position": None,
            },
        )

        stats["last_seen"] = now_ts
        stats["samples"] += 1

        if altitude is not None:
            prev_max_alt = stats.get("max_altitude")
            stats["max_altitude"] = (
                altitude if prev_max_alt is None else max(prev_max_alt, altitude)
            )

        if speed is not None:
            prev_max_speed = stats.get("max_speed")
            stats["max_speed"] = speed if prev_max_speed is None else max(prev_max_speed, speed)

        if lat is None or lon is None:
            return

        prev = stats.get("last_position")
        if prev:
            segment_distance = haversine_km(prev["lat"], prev["lon"], lat, lon)
            # Guard against occasional decoding spikes that would skew totals.
            if 0 < segment_distance < 250:
                stats["total_distance_km"] += segment_distance

        stats["last_position"] = {"lat": lat, "lon": lon, "timestamp": now_ts}

        history = AIRCRAFT_HISTORY.setdefault(hex_code, deque(maxlen=MAX_HISTORY_POINTS))
        history.append(
            {
                "timestamp": now_ts,
                "lat": lat,
                "lon": lon,
                "altitude": altitude,
                "speed": speed,
                "track": track,
            }
        )
        persist_point = {
            "timestamp": now_ts,
            "lat": lat,
            "lon": lon,
            "altitude": altitude,
            "speed": speed,
            "track": track,
        }
        stats_snapshot = {
            "first_seen": stats.get("first_seen"),
            "last_seen": stats.get("last_seen"),
            "max_altitude": stats.get("max_altitude"),
            "max_speed": stats.get("max_speed"),
            "total_distance_km": stats.get("total_distance_km", 0.0),
            "samples": stats.get("samples", 0),
            "last_position": stats.get("last_position"),
        }

    try:
        persist_history_and_stats(hex_code, persist_point, stats_snapshot)
    except Exception:
        logger.exception("Failed to persist history for %s", hex_code)

def fetch_aircraft_metadata(hex_code):
    try:
        url = METADATA_API_URL.format(hex_code=hex_code)
        headers = {"User-Agent": "Signal-ADSB-Tracker/1.0"}
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)

        if response.status_code == 200:
            data = response.json()
            if data:
                with AIRCRAFT_DB_LOCK:
                    AIRCRAFT_DB[hex_code] = {
                        "airline": data.get("ownOp", "N/A"),
                        "type": data.get("desc", "N/A"),
                        "registration": data.get("r", "N/A"),
                        "category": data.get("t", "N/A"),
                    }
                persist_metadata(hex_code, AIRCRAFT_DB[hex_code])
                set_last_metadata_error(None)
            else:
                with AIRCRAFT_DB_LOCK:
                    AIRCRAFT_DB[hex_code] = {"airline": "Unknown", "fetched": True}
        else:
            msg = f"Metadata API returned status={response.status_code} for {hex_code}"
            logger.warning(msg)
            set_last_metadata_error(msg)
            with AIRCRAFT_DB_LOCK:
                AIRCRAFT_DB[hex_code] = {"airline": "Unknown", "fetched": True}
    except requests.RequestException as exc:
        msg = f"Metadata request failed for {hex_code}: {exc}"
        logger.warning(msg)
        set_last_metadata_error(msg)
        with AIRCRAFT_DB_LOCK:
            AIRCRAFT_DB[hex_code] = {"airline": "Unknown", "fetched": True}
    except Exception:
        logger.exception("Unexpected metadata fetch failure for %s", hex_code)
        set_last_metadata_error("Unexpected metadata fetch error")
        with AIRCRAFT_DB_LOCK:
            AIRCRAFT_DB[hex_code] = {"airline": "Unknown", "fetched": True}

app = Flask(__name__)

init_db()
load_persisted_state()

def get_live_data():
    try:
        if is_simulation_active():
            return generate_simulated_data()

        if not os.path.exists(DUMP1090_JSON_PATH):
            return {"now": 0, "messages": 0, "aircraft": []}

        with open(DUMP1090_JSON_PATH, 'r') as f:
            data = json.load(f)

            for plane in data.get('aircraft', []):
                hex_code = plane.get('hex', '').lower()

                if not hex_code:
                    continue

                with AIRCRAFT_DB_LOCK:
                    known = hex_code in AIRCRAFT_DB
                    if not known:
                        AIRCRAFT_DB[hex_code] = {"airline": "Loading..."}

                if not known:
                    t = threading.Thread(target=fetch_aircraft_metadata, args=(hex_code,))
                    t.daemon = True
                    t.start()

                with AIRCRAFT_DB_LOCK:
                    cached = AIRCRAFT_DB.get(hex_code, {})

                if cached.get("airline") != "Loading...":
                    plane['airline'] = cached.get("airline", "N/A")
                    plane['type'] = cached.get("type", plane.get('type', 'N/A'))
                    plane['r'] = cached.get("registration", plane.get('r', 'N/A'))

                update_aircraft_history(plane)

            return data
    except Exception:
        logger.exception("Failed to load live data from %s", DUMP1090_JSON_PATH)
        return {"now": 0, "messages": 0, "aircraft": []}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/data')
def data():
    return jsonify(get_live_data())

@app.route('/status')
def status():
    json_exists = os.path.exists(DUMP1090_JSON_PATH)
    with AIRCRAFT_DB_LOCK:
        cached_aircraft = len(AIRCRAFT_DB)
        tracked_aircraft = len(AIRCRAFT_HISTORY)

    simulation_active = is_simulation_active()
    mode = "simulation" if simulation_active else "live"
    simulation_reason = None
    if simulation_active:
        simulation_reason = "forced" if SIMULATION_MODE else "missing_live_feed"

    return jsonify({
        "mode": mode,
        "json_path": DUMP1090_JSON_PATH,
        "db_path": DB_PATH,
        "persistence": "sqlite",
        "running": True if simulation_active else json_exists,
        "cached_aircraft": cached_aircraft,
        "tracked_aircraft": tracked_aircraft,
        "metadata_error": LAST_METADATA_ERROR,
        "simulation_reason": simulation_reason,
    })


@app.route('/aircraft/<hex_code>/details')
def aircraft_details(hex_code):
    norm_hex = hex_code.lower()
    with AIRCRAFT_DB_LOCK:
        metadata = AIRCRAFT_DB.get(norm_hex, {})
        stats = AIRCRAFT_STATS.get(norm_hex)
        history = list(AIRCRAFT_HISTORY.get(norm_hex, []))

    if not stats and not history:
        return jsonify({"error": "Aircraft not found"}), 404

    first_seen = stats.get("first_seen") if stats else None
    last_seen = stats.get("last_seen") if stats else None
    duration_seconds = 0
    if first_seen and last_seen:
        duration_seconds = max(0, int(last_seen - first_seen))

    return jsonify(
        {
            "hex": norm_hex,
            "metadata": {
                "airline": metadata.get("airline", "N/A"),
                "type": metadata.get("type", "N/A"),
                "registration": metadata.get("registration", "N/A"),
                "category": metadata.get("category", "N/A"),
            },
            "stats": {
                "first_seen": first_seen,
                "last_seen": last_seen,
                "tracked_duration_seconds": duration_seconds,
                "max_altitude": stats.get("max_altitude") if stats else None,
                "max_speed": stats.get("max_speed") if stats else None,
                "total_distance_km": stats.get("total_distance_km", 0.0) if stats else 0.0,
                "samples": stats.get("samples", 0) if stats else 0,
            },
            "history": {
                "points": history,
                "count": len(history),
            },
            "hobby_links": {
                "adsbexchange": f"https://globe.adsbexchange.com/?icao={norm_hex}",
                "airframes": f"https://www.airframes.org/icao/{norm_hex}",
            },
        }
    )

if __name__ == '__main__':
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
