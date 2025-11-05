#!/usr/bin/env bash
set -euo pipefail

# toadb.sh — Linux installer with 30s-after-boot start via systemd timer

RAW_BASE="https://raw.githubusercontent.com/parsij/toadb/main"

BASE_DIR="/usr/local/share/adb_time"
PY="$BASE_DIR/main.py"
UNIT="/etc/systemd/system/adb_time.service"
TIMER="/etc/systemd/system/adb_time.timer"
DEFAULTS="/etc/default/adb_time"
CLI="/usr/local/bin/toadb"

need(){ command -v "$1" >/dev/null 2>&1; }

echo "[*] need root..."
if [[ ${EUID:-0} -ne 0 ]]; then
  echo "[*] re-running with sudo..."
  exec sudo -E bash "$0" "$@"
fi

echo "[*] install adb if needed..."
if ! need adb; then
  if need apt; then apt update && apt install -y android-tools-adb adb || apt install -y adb || true
  elif need dnf; then dnf install -y android-tools || true
  elif need pacman; then pacman -Sy --noconfirm android-tools || true
  else echo "[!] couldn't auto-install adb. install platform-tools manually."; fi
fi

echo "[*] reset program dir at $BASE_DIR ..."
rm -rf "$BASE_DIR"
install -d -m 0755 "$BASE_DIR"

echo "[*] fetch main.py ..."
if [[ -f ./main.py ]]; then
  cp ./main.py "$PY"
else
  if need curl; then curl -fsSL "$RAW_BASE/main.py" -o "$PY"
  elif need wget; then wget -qO "$PY" "$RAW_BASE/main.py"
  else echo "[!] need curl or wget, or place main.py next to this script"; exit 1; fi
fi
chmod +x "$PY"

echo "[*] install CLI shim at $CLI ..."
cat > "$CLI" <<'SH'
#!/usr/bin/env bash
exec /usr/bin/env python3 /usr/local/share/adb_time/main.py "$@"
SH
chmod +x "$CLI"

echo "[*] write defaults at $DEFAULTS ..."
cat > "$DEFAULTS" <<'CONF'
# Auto connect to TCP device at boot (optional), e.g.:
# ADB_CONNECT=192.168.49.1:9800
ADB_CONNECT=

# Probe cadence before first success (seconds)
DISCOVERY_INTERVAL=5

# Give up quietly after this long if no device (seconds)
STARTUP_WINDOW=900

# After first success, refresh every N seconds
REFRESH_INTERVAL=600

# Ignore tiny drift under N seconds
DRIFT_THRESHOLD=1

# Optional proxy env if you ever add HTTP calls:
# HTTP_PROXY=http://proxy:3128
# HTTPS_PROXY=http://proxy:3128
# NO_PROXY=localhost,127.0.0.1,::1,192.168.0.0/16
CONF

echo "[*] write systemd service at $UNIT ..."
cat > "$UNIT" <<'UNIT'
[Unit]
Description=toadb: sync system time from Android via ADB
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/etc/default/adb_time
WorkingDirectory=/usr/local/share/adb_time
ExecStartPre=/usr/bin/adb start-server
ExecStart=/usr/bin/env python3 /usr/local/share/adb_time/main.py
# Let it exit quietly after the startup window if no device; do not auto-restart.
Restart=no
StandardOutput=journal
StandardError=journal
UNIT

echo "[*] write systemd timer (30s after boot) at $TIMER ..."
cat > "$TIMER" <<'TIMER'
[Unit]
Description=Delay start of toadb by 30s after boot

[Timer]
OnBootSec=30s
Unit=adb_time.service
Persistent=true

[Install]
WantedBy=timers.target
TIMER

echo "[*] enable timer (and disable direct service start) ..."
systemctl daemon-reload
systemctl disable --now "adb_time.service" 2>/dev/null || true
systemctl enable --now "adb_time.timer"

echo "[✓] Installed."
echo "   • Starts 30s after boot via: systemctl list-timers | grep adb_time"
echo "   • CLI: toadb | toadb resync | toadb list | toadb device N | toadb reset"
echo "   • Configure: /etc/default/adb_time  (ADB_CONNECT, intervals)"
echo "   • Logs:      journalctl -fu adb_time.service"
