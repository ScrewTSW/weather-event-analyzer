# Installation

## Python Dependencies

```bash
python -m ensurepip --upgrade
python -m pip install -r requirements.txt
```

### What each dependency does

| Package | Purpose |
|---------|---------|
| `requests` | HTTP client for all API calls (SLIDER tiles, Open-Meteo, NWS alerts, NOMADS) |
| `Pillow` | Image handling — tile stitching, cropping, labeling, GIF assembly |
| `numpy` | Array operations for atmospheric data and image manipulation |
| `matplotlib` | Atmosphere charts (CAPE/CIN, temperature, wind) and radar PPI plots |
| `scipy` | Bilinear interpolation for regridding atmospheric data onto satellite imagery |
| `gradio` | Web UI framework — the interactive browser interface |
| `arm_pyart` | NEXRAD Level 2 radar data parsing (reflectivity + velocity volume scans) |
| `nexradaws` | AWS S3 interface for querying and downloading NEXRAD radar files |
| `xarray` | N-dimensional labeled arrays — used with cfgrib to read GRIB2 as datasets |
| `cfgrib` | Python wrapper around ecCodes — decodes GRIB2 files (GFS forecasts, MRMS radar) |
| `imageio[ffmpeg]` | MP4 video generation for satellite time-lapses |
| `pytest` | Test runner |

### ecCodes system library

`cfgrib` requires the **ecCodes** C library from ECMWF (European Centre for Medium-Range Weather Forecasts). ecCodes is the standard library for reading GRIB and BUFR files — the binary formats used by every major weather service (NOAA, ECMWF, JMA, DWD).

Without ecCodes, the app still works for satellite imagery and Open-Meteo JSON data, but cannot read gridded GFS or MRMS GRIB2 files (needed for map overlays and radar composites).

```bash
# Fedora / Nobara
sudo dnf install eccodes eccodes-devel

# Ubuntu / Debian
sudo apt install libeccodes-dev

# macOS (Homebrew)
brew install eccodes

# Or via conda (any platform)
conda install -c conda-forge eccodes
```

After installing ecCodes, verify it works:

```bash
python -c "import cfgrib; print('cfgrib OK')"
```

## Quick Start

```bash
# Web UI
python main.py --web

# CLI — satellite imagery
python main.py satellite --location "Harbin, China" --date 2026-05-31 --start 0700 --end 1200

# CLI — atmosphere data
python main.py atmosphere --lat 45.75 --lon 126.65 --date 2026-05-31
```

## Run as systemd service (auto-start at boot)

This makes the web UI available on your local network at `http://<your-ip>:7860`.

**1. Create the service file** from the template — replace the placeholders with your values:

```bash
sed -e "s|USERNAME|$(whoami)|g" \
    -e "s|APPLICATION_DIRECTORY|$(pwd)|g" \
    weather-event-analyzer.service | sudo tee /etc/systemd/system/weather-event-analyzer.service
```

Or edit manually: replace `USERNAME` with your user and `APPLICATION_DIRECTORY` with the full path to this project.

**2. Enable and start the service:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable weather-event-analyzer
sudo systemctl start weather-event-analyzer
```

**3. Open the firewall port (if firewalld is active):**

```bash
sudo firewall-cmd --add-port=7860/tcp --permanent
sudo firewall-cmd --reload
```

**4. Check status:**

```bash
sudo systemctl status weather-event-analyzer
journalctl -u weather-event-analyzer -f   # follow logs
```

**5. Access from other devices on your network:**

Open `http://<server-ip>:7860` in a browser. Find your IP with `ip addr show`.
