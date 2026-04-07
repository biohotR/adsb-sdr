# ADS-B Flight Tracker Setup Guide

## Current Mode: Simulation
The app is running with fake aircraft data for UI testing.

### Quick Start (No Hardware)

Run the app with built-in simulated traffic:

```bash
export SIMULATION_MODE="true"
export SIMULATED_AIRCRAFT_COUNT="12"
python app.py
```

Open http://localhost:5001 and click aircraft on the map to see advanced details,
history trail, and hobbyist links.

Note: by default, if no `aircraft.json` live feed is found, the app automatically
falls back to simulation mode (`AUTO_SIMULATION_WHEN_NO_FEED=true`).

## Switching to Live Mode

### Step 1: Hardware Setup
1. Connect RTL-SDR dongle to USB
2. Connect 1090 MHz antenna to the dongle

Disable simulation mode before running live feed:

```bash
export SIMULATION_MODE="false"
```

### Step 2: Install dump1090

**macOS:**
```bash
brew install dump1090-mutability
```

**Raspberry Pi / Debian / Ubuntu:**
```bash
sudo apt-get update
sudo apt-get install dump1090-fa
```

### Step 3: Run dump1090
```bash
# macOS (dump1090-mutability)
dump1090 --interactive --net --write-json /tmp/dump1090

# Linux (dump1090-fa)
dump1090-fa --interactive --net --write-json /tmp/dump1090
```

You should see aircraft appearing in the terminal if your antenna is working.

### Step 4: Configure Flask App

Use environment variables instead of editing source code:

```bash
export DUMP1090_JSON_PATH="/tmp/dump1090/aircraft.json"
export FLASK_HOST="0.0.0.0"
export FLASK_PORT="5001"
export FLASK_DEBUG="false"
export LOG_LEVEL="INFO"
```

Optional metadata API configuration:

```bash
export METADATA_API_URL="https://api.airplanes.live/v2/hex/{hex_code}"
export REQUEST_TIMEOUT_SECONDS="3"
```

Persistence (SQLite) configuration:

```bash
export DB_PATH="data/signal_tracker.db"
export HISTORY_RETENTION_HOURS="24"
```

Quick persistence demo:
1. Run in simulation for 1-2 minutes.
2. Stop and start the app again.
3. Click an aircraft and verify history/tracked stats are still present.

### Step 5: Run the Flask App
```bash
python app.py
```

Open http://localhost:5001

## Troubleshooting

### Command not found?
```bash
# Check what's installed
brew list dump1090-mutability
ls /opt/homebrew/bin/dump1090*

# The command is usually just "dump1090" on macOS
dump1090 --help
```

### No aircraft showing?
1. Check dump1090 is running and showing aircraft in terminal
2. Verify the JSON path matches where dump1090 writes
3. Check antenna connection
4. Open `/status` in the browser and confirm `running: true`

### RTL-SDR not detected?
```bash
# Install RTL-SDR drivers
brew install librtlsdr  # macOS
sudo apt-get install rtl-sdr  # Linux

# Test the dongle
rtl_test
```

### Permission issues on Linux?
```bash
sudo usermod -a -G plugdev $USER
# Then logout and login again
```

## File Locations

| OS | Command | Default aircraft.json path |
|----|---------|---------------------------|
| macOS | `dump1090` | `/tmp/dump1090/aircraft.json` |
| Linux | `dump1090-fa` | `/run/dump1090-fa/aircraft.json` |
