#!/usr/bin/env bash
set -euo pipefail

RAW_BASE="https://raw.githubusercontent.com/parsij/toadb/main"

NAME="adb_time"
BASE_DIR="/usr/local/share/$NAME"
PY="$BASE_DIR/main.py"
UNIT="/etc/systemd/system/$NAME.service"
TIMER="/etc/systemd/system/$NAME.timer"
DEFAULTS="/etc/default/$NAME"
CLI="/usr/local/bin/toadb"

need(){ command -v "$1" >/dev/null 2>&1; }

echo "[*] need root..."
if [[ ${EUID:-0} -ne 0 ]]; then
  echo "[*] re-running with sudo..."
  # If we have a readable on-disk script, exec that. If we were piped, refetch under sudo.
  if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" && -r "${BASH_SOURCE[0]}" ]]; then
    exec sudo -E /usr/bin/env bash "${BASH_SOURCE[0]}" "$@"
  else
    if need curl; then
      exec sudo -E /usr/bin/env bash -c "curl -fsSL '$RAW_BASE/toadb.sh' | /usr/bin/env bash -s --"
    elif need wget; then
      exec sudo -E /usr/bin/env bash -c "wget -qO- '$RAW_BASE/toadb.sh' | /usr/bin/env bash -s --"
    else
      echo "[!] cannot elevate from a pipe without curl or wget."
      echo "    Try: bash -c 'wget -qO- $RAW_BASE/toadb.sh | sudo -E bash -s --'"
      exit 1
    fi
  fi
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
ADB_CONNECT=

# Probe cadence before first success (seconds)
DISCOVERY_INTERVAL=5

# Give up quietly after this long if no device (seconds)
STARTUP_WINDOW=900

# After first success, refresh every N seconds
REFRESH_INTERVAL=600

# Ignore tiny drift under N seconds
DRIFT_THRESHOLD=1

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
systemctl disable --now "$NAME.service" 2>/dev/null || true
systemctl enable --now "$NAME.timer"

echo "[✓] Installed."
echo "   • Starts 30s after boot via: systemctl list-timers | grep $NAME"
echo "   • CLI: toadb | toadb resync | toadb list | toadb device N | toadb reset"
echo "   • Configure: /etc/default/adb_time  (ADB_CONNECT, intervals)"
echo "   • Logs:      journalctl -fu $NAME.service"
