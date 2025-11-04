#!/usr/bin/env bash
set -euo pipefail

# Use the RAW GitHub URL for real content, not the pretty HTML page.
RAW_BASE="https://raw.githubusercontent.com/parsij/toadb/main"

NAME="adb_time"
BASE_DIR="/usr/local/share/$NAME"
PY="$BASE_DIR/main.py"
UNIT="/etc/systemd/system/$NAME.service"
DEFAULTS="/etc/default/$NAME"
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

echo "[*] install program..."
install -d -m 0755 "$BASE_DIR"
if [[ -f ./main.py ]]; then
  cp ./main.py "$PY"
else
  if need curl; then curl -fsSL "$RAW_BASE/main.py" -o "$PY"
  elif need wget; then wget -qO "$PY" "$RAW_BASE/main.py"
  else echo "[!] need curl or wget, or place main.py next to toadb.sh"; exit 1; fi
fi
chmod +x "$PY"

echo "[*] install CLI shim at $CLI ..."
cat > "$CLI" <<'SH'
#!/usr/bin/env bash
exec /usr/bin/env python3 /usr/local/share/adb_time/main.py "$@"
SH
chmod +x "$CLI"

echo "[*] defaults file at $DEFAULTS ..."
cat > "$DEFAULTS" <<'CONF'
# Optional: auto connect to TCP device at boot, e.g.:
# ADB_CONNECT=192.168.49.1:9800
ADB_CONNECT=

# How often to probe before the first successful sync (seconds)
DISCOVERY_INTERVAL=5

# How long after boot to keep trying before exiting if no device ever authorizes (seconds)
STARTUP_WINDOW=900

# After a successful sync, how often to refresh (seconds)
REFRESH_INTERVAL=600

# Ignore tiny drift under N seconds
DRIFT_THRESHOLD=1
CONF

echo "[*] systemd unit..."
# Important: no Restart=always. If it exits after the startup window, it stays down until next boot.
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
ExecStartPre=/bin/sleep 5
ExecStartPre=/usr/bin/adb start-server
ExecStart=/usr/bin/env python3 /usr/local/share/adb_time/main.py
# Let it exit quietly if no device after the window; don't auto-restart.
Restart=no
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "$NAME.service"
systemctl restart "$NAME.service" || true

echo "[✓] Installed."
echo "   • CLI: toadb | toadb resync | toadb list | toadb device N | toadb reset"
echo "   • Set ADB_CONNECT in /etc/default/adb_time if using Wi-Fi ADB."
echo "   • Logs: journalctl -fu $NAME.service"
