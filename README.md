# JMA-WX-Bridge

Local proxy server acting as a drop-in replacement for the Open-Meteo `/v1/forecast` API, backed by Japan Meteorological Agency (JMA) XML feeds for locations in Japan and transparent passthrough (with stale-if-error caching) to Open-Meteo for the rest of the world.

## Requirements

- **OS:** Linux or macOS (works on x86_64 and ARM64, e.g., Raspberry Pi / Apple Silicon)
- **Python:** `>= 3.12` (managed automatically if you use `uv`)
- **Package Manager:** [`uv`](https://docs.astral.sh/uv/) (recommended), or any PEP 621-compatible tool (`pip install -e .`)
- **Database:** SQLite 3 (included in Python's standard library; no external DB server needed)
- **Network:** Outbound HTTPS access to:
  - `www.jma.go.jp` (one-time setup to fetch JMA Class10s boundary GeoJSON)
  - `www.data.jma.go.jp` (periodic `regular.xml` & `VPFD51` XML polling)
  - `api.open-meteo.com` (optional, only for non-Japan passthrough requests)

## Setup

1. **Install `uv`** (if not already installed):

   ```bash
   # macOS (Homebrew)
   brew install uv

   # Linux / macOS (official installer)
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Clone and install dependencies** (creates `.venv/` and installs Python + packages):

   ```bash
   git clone <your-repo-url> JMA-WX-Bridge
   cd JMA-WX-Bridge
   uv sync
   ```

3. **Fetch JMA boundary data** (downloads JMA's official `area.json` and `geojson/class10s.json` into `src/jma_wx_bridge/geo/data/jma_areas.geojson`, which is gitignored so JMA data is not redistributed in the repository):

   ```bash
   uv run python scripts/fetch_jma_geojson.py
   ```

4. **Verify installation** (optional):

   ```bash
   uv run pytest -v
   ```

## Run

```bash
uv run uvicorn jma_wx_bridge.main:app --host 0.0.0.0 --port 8000
```

Sanity check:

```bash
curl -s http://127.0.0.1:8000/healthz
# {"status":"ok","areas_loaded":153,"cached_forecasts":...}
```

Then point your client (e.g., `meshing-around`) to `http://127.0.0.1:8000/v1/forecast`.

### Running as a Background Service

#### Option 1: `systemd` User Service (Linux)

Create `~/.config/systemd/user/jma-wx-bridge.service`:

```ini
[Unit]
Description=JMA-WX-Bridge (JMA-to-OpenMeteo Proxy)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/JMA-WX-Bridge
ExecStart=/path/to/JMA-WX-Bridge/.venv/bin/uvicorn jma_wx_bridge.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Enable and start it (plus `enable-linger` so it stays running after you log out):

```bash
systemctl --user daemon-reload
systemctl --user enable --now jma-wx-bridge
sudo loginctl enable-linger "$USER"
journalctl --user -u jma-wx-bridge -f
```

#### Option 2: `launchd` User Agent (macOS)

Create `~/Library/LaunchAgents/local.jma-wx-bridge.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.jma-wx-bridge</string>
  <key>WorkingDirectory</key>
  <string>/path/to/JMA-WX-Bridge</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/JMA-WX-Bridge/.venv/bin/uvicorn</string>
    <string>jma_wx_bridge.main:app</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>8000</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/jma-wx-bridge.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/jma-wx-bridge.err.log</string>
</dict>
</plist>
```

Load it:

```bash
launchctl load -w ~/Library/LaunchAgents/local.jma-wx-bridge.plist
```

#### Option 3: Quick Portable Background (`tmux` or `nohup`)

```bash
# Using tmux
tmux new-session -d -s jma-wx-bridge 'uv run uvicorn jma_wx_bridge.main:app --host 127.0.0.1 --port 8000'

# Or using nohup
nohup uv run uvicorn jma_wx_bridge.main:app --host 127.0.0.1 --port 8000 > jma-wx-bridge.log 2>&1 &
```

### Environment Variables (`JMA_WX_*`)

| Variable | Default | Description |
| --- | --- | --- |
| `JMA_WX_DB_PATH` | `jma_wx_bridge.db` | SQLite WAL cache file path |
| `JMA_WX_GEOJSON_PATH` | *(bundled)* | Optional custom GeoJSON override |
| `JMA_WX_POLL_INTERVAL_MINUTES` | `20` | JMA `regular.xml` poll interval |
| `JMA_WX_CACHE_RETENTION_DAYS` | `3` | Days before cached rows are pruned |
| `JMA_WX_ENABLE_BACKGROUND_POLL` | `true` | Toggle background JMA poller |
| `JMA_WX_OPEN_METEO_BASE_URL` | `https://api.open-meteo.com/v1/forecast` | Upstream Open-Meteo endpoint |

## Test & Lint

```bash
uv run pytest -v
uv run ruff check .
```

## Disclaimer

### Data Source's Terms and Conditions

This script uses [Public XML Feed from JMA](https://xml.kishou.go.jp/xmlpull.html).

Terms and Conditions for all informations are applied correspondingly to theirs.

### Always refer reliable, primary source for critical applications/situations

Although this script uses data from JMA(Japan Meteorological Agency)'s Public service,
no certainty nor reliability is guaranteed by any means.

Use reliable source directly for any critical applications and/or situations:
  - [JMA Official Website](https://www.jma.go.jp/bosai/#lang=en)
  - [NHK World Japan: National Weather Forecast](https://www3.nhk.or.jp/nhkworld/news/weather/)
  - [NHK AM/FM Broadcast Frequency Llist](https://www.nhk.or.jp/radio/info/frequency.html)


### License

This script is licensed under GNU GENERAL PUBLIC LICENSE Version 3(GPLv3).
Refer LICENSE for full text.

### General Terms

Nobody is responsible for any bricked devices, dead SD cards, thermonuclear wars, or for you getting fired because the weather forecast provided by this service turned out to be wrong and you forgot to bring an umbrella on a rainy day. Nor are we responsible if you end up drenched in sweat during dangerously hot summer weather. (Seriously, be careful out there!)
