#!/usr/bin/env python3
# toadb main.py — ADB time + timezone sync with boot startup window + periodic refresh
# - On boot: scan every DISCOVERY_INTERVAL (default 5s) for up to STARTUP_WINDOW (default 900s).
#   If no device authorizes, exit quietly until next boot.
# - If a sync succeeds during the window, stay running and resync every REFRESH_INTERVAL (default 600s).
# - Sets BOTH system time and system timezone from the phone.
# - CLI: `toadb`, `toadb resync`, `toadb list`, `toadb device N`, `toadb reset`, `toadb oneshot`
# - Extras: LOG_FILE env for file logging, graceful SIGTERM/SIGINT, adb existence check, device model in logs.

import os, sys, time, json, shutil, platform, subprocess, signal
from typing import List, Tuple, Optional

# ---------- simple logger ----------
_LOG_FH = None
def _open_log():
    global _LOG_FH
    fp = os.environ.get("LOG_FILE", "").strip()
    if fp:
        try:
            _LOG_FH = open(fp, "a", encoding="utf-8")
        except Exception:
            _LOG_FH = None

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if _LOG_FH:
        try:
            _LOG_FH.write(line + "\n")
            _LOG_FH.flush()
        except Exception:
            pass

def close_log():
    global _LOG_FH
    try:
        if _LOG_FH:
            _LOG_FH.close()
    finally:
        _LOG_FH = None

# ---------- utils ----------
def run(cmd: List[str], check=False):
    return subprocess.run(cmd, check=check, capture_output=True, text=True)

def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def os_is_windows() -> bool:
    return platform.system().lower().startswith("win")

def is_root_linux() -> bool:
    if os_is_windows(): return False
    try: return os.geteuid() == 0
    except AttributeError: return False

def config_path() -> str:
    if os_is_windows():
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        d = os.path.join(base, "PhoneTimeSync")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "config.json")
    # Linux
    if is_root_linux():
        d = "/etc/toadb"
    else:
        d = os.path.expanduser("~/.config/toadb")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "config.json")

def load_cfg() -> dict:
    p = config_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cfg(cfg: dict):
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

def reset_cfg():
    try: os.remove(config_path())
    except FileNotFoundError: pass

# ---------- adb ----------
def parse_adb_devices(text: str) -> List[Tuple[str, str]]:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("List of devices"): continue
        parts = s.split()
        if len(parts) >= 2:
            out.append((parts[0], parts[1]))
    return out

def adb_devices() -> List[Tuple[str, str]]:
    try:
        r = run(["adb", "devices"])
        return parse_adb_devices(r.stdout)
    except FileNotFoundError:
        log("adb not found. Install platform-tools and ensure adb is on PATH.")
        sys.exit(127)

def first_online_device(devs: List[Tuple[str, str]]) -> Optional[str]:
    for serial, state in devs:
        if state == "device":
            return serial
    return None

def pick_serial(preferred: Optional[str]) -> Optional[str]:
    if preferred: return preferred
    cfg = load_cfg()
    sel = cfg.get("selected_serial")
    devs = adb_devices()
    serials = [s for s, _ in devs]
    if sel and sel in serials:
        return sel
    s = first_online_device(devs)
    if s: return s
    return serials[0] if serials else None

def wait_for_authorized(serial: str):
    run(["adb", "start-server"])
    subprocess.run(["adb", "-s", serial, "wait-for-device"], capture_output=True, text=True)
    while True:
        state = dict(adb_devices()).get(serial, "")
        if state == "device":
            t = subprocess.run(["adb", "-s", serial, "shell", "echo", "ok"], capture_output=True, text=True)
            if t.returncode == 0 and "ok" in (t.stdout or ""):
                log("ADB device authorized.")
                return
        time.sleep(0.5)

def phone_epoch(serial: str) -> Optional[int]:
    cmds = [
        ["adb", "-s", serial, "shell", "date", "+%s"],
        ["adb", "-s", serial, "shell", "toybox", "date", "+%s"],
        ["adb", "-s", serial, "shell", "busybox", "date", "+%s"],
        ["adb", "-s", serial, "shell", "sh", "-c", "date +%s"],
    ]
    for c in cmds:
        p = subprocess.run(c, capture_output=True, text=True)
        s = (p.stdout or "").strip()
        if p.returncode == 0 and s.isdigit():
            return int(s)
    return None

def phone_offset_hhmm(serial: str) -> Optional[str]:
    p = subprocess.run(["adb", "-s", serial, "shell", "date", "+%z"], capture_output=True, text=True)
    s = (p.stdout or "").strip().replace("\r", "")
    if p.returncode == 0 and len(s) >= 5 and s[0] in "+-":
        return s[:5]
    return None

def phone_tz_id(serial: str) -> Optional[str]:
    # Prefer IANA zone ID
    for cmd in (
        ["adb", "-s", serial, "shell", "getprop", "persist.sys.timezone"],
        ["adb", "-s", serial, "shell", "settings", "get", "global", "time_zone"],
    ):
        p = run(cmd)
        s = (p.stdout or "").strip().replace("\r", "")
        if p.returncode == 0 and s and s.lower() != "null":
            return s
    return None

def device_model(serial: str) -> str:
    try:
        r = run(["adb", "-s", serial, "shell", "getprop", "ro.product.model"])
        m = (r.stdout or "").strip()
        return m if m else "unknown-model"
    except Exception:
        return "unknown-model"

# ---------- elevation ----------
def elevate_linux():
    if is_root_linux(): return
    exe = sys.executable
    args = [exe] + sys.argv
    if have("pkexec"):
        os.execvp("pkexec", ["pkexec"] + args)
    elif have("sudo"):
        os.execvp("sudo", ["sudo"] + args)
    else:
        log("Need root privileges but neither pkexec nor sudo is available.")
        sys.exit(1)

def is_admin_windows() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def elevate_windows():
    if is_admin_windows(): return
    import ctypes
    args = " ".join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, None, 1)
    sys.exit(0)

# ---------- timezone helpers ----------
IANA_TO_WINDOWS = {
    # Common mappings
    "UTC": "UTC",
    "Etc/UTC": "UTC",
    "America/Los_Angeles": "Pacific Standard Time",
    "America/Denver": "Mountain Standard Time",
    "America/Chicago": "Central Standard Time",
    "America/New_York": "Eastern Standard Time",
    "America/Phoenix": "US Mountain Standard Time",
    "America/Anchorage": "Alaskan Standard Time",
    "Pacific/Honolulu": "Hawaiian Standard Time",
    "Europe/London": "GMT Standard Time",
    "Europe/Berlin": "W. Europe Standard Time",
    "Europe/Paris": "Romance Standard Time",
    "Europe/Madrid": "Romance Standard Time",
    "Europe/Rome": "W. Europe Standard Time",
    "Europe/Warsaw": "Central European Standard Time",
    "Europe/Moscow": "Russian Standard Time",
    "Asia/Tehran": "Iran Standard Time",
    "Asia/Jerusalem": "Israel Standard Time",
    "Asia/Tokyo": "Tokyo Standard Time",
    "Asia/Seoul": "Korea Standard Time",
    "Asia/Shanghai": "China Standard Time",
    "Asia/Hong_Kong": "China Standard Time",
    "Asia/Kolkata": "India Standard Time",
    "Asia/Kathmandu": "Nepal Standard Time",
    "Australia/Sydney": "AUS Eastern Standard Time",
    "Australia/Perth": "W. Australia Standard Time",
    "America/Sao_Paulo": "E. South America Standard Time",
    "America/Bogota": "SA Pacific Standard Time",
    "Africa/Cairo": "Egypt Standard Time",
    "Africa/Johannesburg": "South Africa Standard Time",
}

def etc_gmt_from_offset(hhmm: str) -> Optional[str]:
    # Map full-hour offsets to Etc/GMT zones. Note: signs are inverted in Etc/GMT names.
    # +0800 (UTC+8) -> Etc/GMT-8, -0300 (UTC-3) -> Etc/GMT+3
    try:
        sign = 1 if hhmm[0] == "+" else -1
        h = int(hhmm[1:3])
        m = int(hhmm[3:5])
        if m != 0:
            return None
        name = f"Etc/GMT{(-sign*h):+d}".replace("+", "+").replace("+-", "-")
        return name
    except Exception:
        return None

def set_timezone_linux(tz_id: Optional[str], off: Optional[str]) -> bool:
    # Try IANA ID first
    if tz_id:
        if have("timedatectl"):
            p = subprocess.run(["timedatectl", "set-timezone", tz_id], capture_output=True, text=True)
            if p.returncode == 0:
                log(f"Linux timezone set to {tz_id}")
                return True
        # fallback to direct link if timedatectl missing
        zonefile = f"/usr/share/zoneinfo/{tz_id}"
        if os.path.exists(zonefile):
            try:
                subprocess.run(["ln", "-sf", zonefile, "/etc/localtime"], check=False)
                with open("/etc/timezone", "w", encoding="utf-8") as f:
                    f.write(tz_id + "\n")
                log(f"Linux timezone set (symlink) to {tz_id}")
                return True
            except Exception as e:
                log(f"Failed to write timezone link: {e}")

    # Fallback: attempt Etc/GMT from offset if on full hour
    if off:
        etc = etc_gmt_from_offset(off)
        if etc and have("timedatectl"):
            p = subprocess.run(["timedatectl", "set-timezone", etc], capture_output=True, text=True)
            if p.returncode == 0:
                log(f"Linux timezone set to {etc} (from offset {off})")
                return True
    log("Linux timezone unchanged (no valid tz id or offset mapping).")
    return False

def set_timezone_windows(tz_id: Optional[str], off: Optional[str]) -> bool:
    target = None
    if tz_id and tz_id in IANA_TO_WINDOWS:
        target = IANA_TO_WINDOWS[tz_id]
    # Minimal offset fallbacks if no mapping
    if not target and off:
        off_map = {
            "-0800": "Pacific Standard Time",
            "-0700": "Mountain Standard Time",
            "-0600": "Central Standard Time",
            "-0500": "Eastern Standard Time",
            "+0000": "UTC",
            "+0100": "W. Europe Standard Time",
            "+0200": "South Africa Standard Time",
            "+0300": "Russian Standard Time",
            "+0330": "Iran Standard Time",
            "+0530": "India Standard Time",
            "+0900": "Tokyo Standard Time",
        }
        target = off_map.get(off)
    if not target:
        log("Windows timezone unchanged (no mapping for phone tz/offset).")
        return False

    ps = [
        "powershell", "-NoProfile", "-Command",
        f"try {{ Set-TimeZone -Id '{target}' -ErrorAction Stop; 'OK' }} catch {{ 'ERR:'+$_ }}"
    ]
    p = subprocess.run(ps, capture_output=True, text=True)
    if p.returncode == 0 and "OK" in (p.stdout or ""):
        log(f"Windows timezone set to {target}")
        return True
    log(f"Failed to set Windows timezone to {target}: {(p.stdout or p.stderr).strip()}")
    return False

# ---------- set time ----------
def set_time_linux_epoch(epoch: int) -> bool:
    if have("timedatectl"):
        subprocess.run(["timedatectl", "set-ntp", "false"], capture_output=True)
    p = subprocess.run(["date", "-u", "-s", f"@{epoch}"], capture_output=True, text=True)
    ok = (p.returncode == 0)
    if not ok:
        log("Failed to set Linux time: " + (p.stderr or "").strip())
    if have("timedatectl"):
        subprocess.run(["timedatectl", "set-ntp", "true"], capture_output=True)
    return ok

def set_time_windows_epoch(epoch: int) -> bool:
    stop = ["powershell", "-NoProfile", "-Command",
            "Stop-Service w32time -ErrorAction SilentlyContinue"]
    start = ["powershell", "-NoProfile", "-Command",
             "Start-Service w32time -ErrorAction SilentlyContinue"]
    setd = ["powershell", "-NoProfile", "-Command",
            f"$u={epoch}; $t=[DateTimeOffset]::FromUnixTimeSeconds($u).LocalDateTime; Set-Date -Date $t"]
    subprocess.run(stop, capture_output=True, text=True)
    p = subprocess.run(setd, capture_output=True, text=True)
    ok = (p.returncode == 0)
    if not ok:
        log("Failed to set Windows time: " + (p.stderr or "").strip())
    subprocess.run(start, capture_output=True, text=True)
    return ok

# ---------- actions ----------
def sync_once(serial: str, drift_threshold: int = 1) -> bool:
    # Read phone time + tz info
    pe = phone_epoch(serial)
    if pe is None:
        log("Failed to read epoch from phone.")
        return False
    off = phone_offset_hhmm(serial)
    tz = phone_tz_id(serial)

    host = int(time.time())
    drift = pe - host
    log(f"Phone epoch: {pe} | Host epoch: {host} | Drift: {drift}s")
    if tz:
        log(f"Phone timezone: {tz}")
    if off:
        log(f"Phone offset: {off}")

    # Elevate, set timezone first (always), then set time if needed
    ok_tz = True
    ok_time = True
    if os_is_windows():
        elevate_windows()
        ok_tz = set_timezone_windows(tz, off)
        if abs(drift) >= drift_threshold:
            ok_time = set_time_windows_epoch(pe)
        else:
            log("Drift below threshold; skipped time set.")
    else:
        elevate_linux()
        ok_tz = set_timezone_linux(tz, off)
        if abs(drift) >= drift_threshold:
            ok_time = set_time_linux_epoch(pe)
        else:
            log("Drift below threshold; skipped time set.")

    if ok_tz and ok_time:
        log("✅ System time/timezone updated.")
    elif ok_tz and not ok_time:
        log("⚠️ Timezone updated, time unchanged due to error or threshold.")
    elif not ok_tz and ok_time:
        log("⚠️ Time updated, timezone unchanged (no mapping or failure).")
    else:
        log("❌ Failed to update time and timezone.")
    return ok_tz and ok_time

def cmd_list() -> int:
    devs = adb_devices()
    if not devs:
        log("No ADB devices found.")
        return 1
    log("Detected devices:")
    for i, (s, st) in enumerate(devs, 1):
        log(f"  {i}: {s} [{st}]")
    return 0

def cmd_device(args: List[str]) -> int:
    devs = adb_devices()
    if not devs:
        log("No ADB devices found. Connect or `adb connect host:port`.")
        return 1
    if not args:
        return cmd_list()
    try:
        idx = int(args[0])
    except ValueError:
        log("Usage: toadb device N   (use 'toadb list' first)")
        return 1
    if idx < 1 or idx > len(devs):
        log("Invalid device number.")
        return 1
    serial = devs[idx - 1][0]
    cfg = load_cfg()
    cfg["selected_serial"] = serial
    save_cfg(cfg)
    log(f"Selected device: {serial}")
    return 0

def cmd_reset() -> int:
    reset_cfg()
    log("toadb config reset.")
    return 0

def cmd_resync() -> int:
    cfg = load_cfg()
    s = pick_serial(cfg.get("selected_serial"))
    if not s:
        log("No devices detected. Waiting for one...")
        subprocess.run(["adb", "wait-for-device"], capture_output=True, text=True)
        s = pick_serial(None)
        if not s:
            log("Still no device.")
            return 1
    log(f"Using device: {s} ({device_model(s)})")
    wait_for_authorized(s)
    return 0 if sync_once(s) else 1

# ---------- graceful exit ----------
def _handle_signal(sig, frame):
    log(f"Received signal {sig}; exiting.")
    close_log()
    sys.exit(0)

# ---------- daemon with startup window ----------
def run_boot_cycle(oneshot=False):
    connect_target = os.environ.get("ADB_CONNECT", "").strip()
    discovery_interval = int(os.environ.get("DISCOVERY_INTERVAL", "5"))
    startup_window = int(os.environ.get("STARTUP_WINDOW", "900"))
    refresh_interval = int(os.environ.get("REFRESH_INTERVAL", "600"))
    drift_threshold = int(os.environ.get("DRIFT_THRESHOLD", "1"))

    log(f"toadb daemon: discovery every {discovery_interval}s for {startup_window}s, "
        f"then refresh every {refresh_interval}s on success.")

    if not have("adb"):
        log("adb not in PATH. Exiting.")
        sys.exit(127)

    run(["adb", "start-server"])
    start = time.monotonic()
    had_success = False
    last_choice = None

    while True:
        try:
            if connect_target:
                subprocess.run(["adb", "connect", connect_target], capture_output=True, text=True)

            devs = adb_devices()
            if not devs:
                if oneshot:
                    log("No devices; oneshot mode exiting.")
                    return
                if not had_success and (time.monotonic() - start >= startup_window):
                    log("No device authorized within startup window; exiting until next boot.")
                    sys.exit(0)
                time.sleep(discovery_interval)
                continue

            cfg = load_cfg()
            prefer = cfg.get("selected_serial")
            serial = pick_serial(prefer)
            online_count = sum(1 for _, st in devs if st == "device")
            if online_count > 1 and not prefer:
                log("2+ online devices detected: using the first. Set one with: toadb list | toadb device N")
            if serial != last_choice:
                log(f"Watching device: {serial} ({device_model(serial)})")
                last_choice = serial

            wait_for_authorized(serial)

            if sync_once(serial, drift_threshold=drift_threshold):
                had_success = True

            if oneshot:
                return

            if had_success:
                time.sleep(refresh_interval)
            else:
                if time.monotonic() - start >= startup_window:
                    log("Startup window expired without a successful sync; exiting until next boot.")
                    sys.exit(0)
                time.sleep(discovery_interval)

        except Exception as e:
            log(f"[warn] loop error: {e}")
            if oneshot:
                return
            if had_success:
                time.sleep(refresh_interval)
            else:
                if time.monotonic() - start >= startup_window:
                    log("Startup window expired after errors; exiting until next boot.")
                    sys.exit(0)
                time.sleep(discovery_interval)

# ---------- CLI ----------
def main():
    _open_log()
    for s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(s, _handle_signal)
        except Exception:
            pass

    args = sys.argv[1:]
    if not args:
        run_boot_cycle()
        close_log()
        return

    if args[0] in ("--oneshot", "oneshot"):
        run_boot_cycle(oneshot=True)
        close_log()
        return

    cmd = args[0].lower()
    rest = args[1:]

    if cmd in ("help", "-h", "--help"):
        print("""toadb commands:
  toadb                Run daemon (boot behavior: 15 min discovery window, then 10 min refresh after success)
  toadb oneshot        One-time sync then exit (waits for authorization if needed)
  toadb resync         One-shot sync now (waits for authorization)
  toadb list           List connected devices
  toadb device [N]     Select device by number
  toadb reset          Clear saved selection/config

Env vars:
  LOG_FILE=/var/log/toadb.log
  ADB_CONNECT=host:port
  DISCOVERY_INTERVAL=5
  STARTUP_WINDOW=900
  REFRESH_INTERVAL=600
  DRIFT_THRESHOLD=1
""")
        close_log()
        return

    if cmd == "resync":
        code = cmd_resync()
        close_log()
        sys.exit(code)
    if cmd == "list":
        code = cmd_list()
        close_log()
        sys.exit(code)
    if cmd == "device":
        code = cmd_device(rest)
        close_log()
        sys.exit(code)
    if cmd == "reset":
        code = cmd_reset()
        close_log()
        sys.exit(code)

    print("Unknown command. Try 'toadb help'.")
    close_log()
    sys.exit(1)

if __name__ == "__main__":
    main()
