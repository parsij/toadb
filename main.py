#!/usr/bin/env python3

import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time


_LOG_HANDLE = None


def _open_log_file():
    """Open log file if LOG_FILE is set."""
    global _LOG_HANDLE
    log_path = os.environ.get("LOG_FILE", "").strip()
    if not log_path:
        return
    try:
        _LOG_HANDLE = open(log_path, "a", encoding="utf-8")
    except Exception:
        _LOG_HANDLE = None


def log(message):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line, flush=True)
    if _LOG_HANDLE:
        try:
            _LOG_HANDLE.write(line + "\n")
            _LOG_HANDLE.flush()
        except Exception:
            pass


def close_log():
    global _LOG_HANDLE
    try:
        if _LOG_HANDLE:
            _LOG_HANDLE.close()
    finally:
        _LOG_HANDLE = None



def run(cmd, check=False):
    """Wrapper around subprocess.run with captured text output."""
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def have(cmd):
    return shutil.which(cmd) is not None


def os_is_windows():
    return platform.system().lower().startswith("win")


def is_root_linux():
    if os_is_windows():
        return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def config_path():
    if os_is_windows():
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        cfg_dir = os.path.join(base, "PhoneTimeSync")
        os.makedirs(cfg_dir, exist_ok=True)
        return os.path.join(cfg_dir, "config.json")

    if is_root_linux():
        cfg_dir = "/etc/toadb"
    else:
        cfg_dir = os.path.expanduser("~/.config/toadb")
    os.makedirs(cfg_dir, exist_ok=True)
    return os.path.join(cfg_dir, "config.json")


def load_cfg():
    path = config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cfg(cfg):
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def reset_cfg():
    path = config_path()
    try:
        os.remove(path)
    except FileNotFoundError:
        pass



def parse_adb_devices(text):
    devices = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))
    return devices


def adb_devices():
    try:
        result = run(["adb", "devices"])
        return parse_adb_devices(result.stdout)
    except FileNotFoundError:
        log("adb not found. Install platform-tools and put adb on PATH.")
        sys.exit(127)


def first_online_device(devices):
    for serial, state in devices:
        if state == "device":
            return serial
    return None


def pick_serial(preferred):
    if preferred:
        return preferred

    cfg = load_cfg()
    saved = cfg.get("selected_serial")

    devices = adb_devices()
    serials = [s for s, _ in devices]

    if saved and saved in serials:
        return saved

    online = first_online_device(devices)
    if online:
        return online

    if serials:
        return serials[0]
    return None


def wait_for_authorized(serial):
    run(["adb", "start-server"])
    subprocess.run(["adb", "-s", serial, "wait-for-device"], capture_output=True, text=True)

    while True:
        devices = dict(adb_devices())
        state = devices.get(serial, "")
        if state == "device":
            probe = subprocess.run(
                ["adb", "-s", serial, "shell", "echo", "ok"],
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0 and "ok" in (probe.stdout or ""):
                log("ADB device authorized.")
                return
        time.sleep(0.5)


def phone_epoch(serial):
    cmds = [
        ["adb", "-s", serial, "shell", "date", "+%s"],
        ["adb", "-s", serial, "shell", "toybox", "date", "+%s"],
        ["adb", "-s", serial, "shell", "busybox", "date", "+%s"],
        ["adb", "-s", serial, "shell", "sh", "-c", "date +%s"],
    ]
    for cmd in cmds:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        raw = (proc.stdout or "").strip()
        if proc.returncode == 0 and raw.isdigit():
            return int(raw)
    return None


def phone_offset_hhmm(serial):
    proc = subprocess.run(
        ["adb", "-s", serial, "shell", "date", "+%z"],
        capture_output=True,
        text=True,
    )
    s = (proc.stdout or "").strip().replace("\r", "")
    if proc.returncode == 0 and len(s) >= 5 and s[0] in "+-":
        return s[:5]
    return None


def phone_tz_id(serial):
    """Try to get IANA timezone id from the phone."""
    candidates = (
        ["adb", "-s", serial, "shell", "getprop", "persist.sys.timezone"],
        ["adb", "-s", serial, "shell", "settings", "get", "global", "time_zone"],
    )
    for cmd in candidates:
        proc = run(cmd)
        val = (proc.stdout or "").strip().replace("\r", "")
        if proc.returncode == 0 and val and val.lower() != "null":
            return val
    return None


def device_model(serial):
    try:
        res = run(["adb", "-s", serial, "shell", "getprop", "ro.product.model"])
        model = (res.stdout or "").strip()
        if model:
            return model
    except Exception:
        pass
    return "unknown-model"



def elevate_linux():
    if is_root_linux():
        return

    exe = sys.executable
    args = [exe] + sys.argv

    if have("pkexec"):
        os.execvp("pkexec", ["pkexec"] + args)
    if have("sudo"):
        os.execvp("sudo", ["sudo"] + args)

    log("Need root privileges but neither pkexec nor sudo is available.")
    sys.exit(1)


def is_admin_windows():
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate_windows():
    if is_admin_windows():
        return

    import ctypes

    cmdline = " ".join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, cmdline, None, 1
    )
    sys.exit(0)



IANA_TO_WINDOWS = {
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


def etc_gmt_from_offset(hhmm):
    """
    Map full-hour offsets to Etc/GMT zones.
    Example: +0800 -> Etc/GMT-8, -0300 -> Etc/GMT+3
    """
    try:
        sign = 1 if hhmm[0] == "+" else -1
        hours = int(hhmm[1:3])
        minutes = int(hhmm[3:5])
        if minutes != 0:
            return None
        offset = -sign * hours
        return f"Etc/GMT{offset:+d}".replace("+-", "-")
    except Exception:
        return None


def set_timezone_linux(tz_id, off):
    # First try the phone's IANA timezone id
    if tz_id:
        if have("timedatectl"):
            proc = subprocess.run(
                ["timedatectl", "set-timezone", tz_id],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                log(f"Linux timezone set to {tz_id}")
                return True

        zonefile = os.path.join("/usr/share/zoneinfo", tz_id)
        if os.path.exists(zonefile):
            try:
                subprocess.run(["ln", "-sf", zonefile, "/etc/localtime"], check=False)
                with open("/etc/timezone", "w", encoding="utf-8") as f:
                    f.write(tz_id + "\n")
                log(f"Linux timezone set (symlink) to {tz_id}")
                return True
            except Exception as exc:
                log(f"Failed to write timezone link: {exc}")

    # Fallback: try to infer Etc/GMT from numeric offset (full hour only)
    if off:
        etc_name = etc_gmt_from_offset(off)
        if etc_name and have("timedatectl"):
            proc = subprocess.run(
                ["timedatectl", "set-timezone", etc_name],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                log(f"Linux timezone set to {etc_name} (from offset {off})")
                return True

    log("Linux timezone unchanged (no valid tz id or offset mapping).")
    return False


def set_timezone_windows(tz_id, off):
    target = None

    if tz_id and tz_id in IANA_TO_WINDOWS:
        target = IANA_TO_WINDOWS[tz_id]

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

    ps_cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        f"try {{ Set-TimeZone -Id '{target}' -ErrorAction Stop; 'OK' }} "
        "catch {{ 'ERR:'+$_ }}",
    ]
    proc = subprocess.run(ps_cmd, capture_output=True, text=True)
    if proc.returncode == 0 and "OK" in (proc.stdout or ""):
        log(f"Windows timezone set to {target}")
        return True

    msg = (proc.stdout or proc.stderr or "").strip()
    log(f"Failed to set Windows timezone to {target}: {msg}")
    return False


def set_time_linux_epoch(epoch):
    if have("timedatectl"):
        subprocess.run(["timedatectl", "set-ntp", "false"], capture_output=True)

    proc = subprocess.run(
        ["date", "-u", "-s", f"@{epoch}"],
        capture_output=True,
        text=True,
    )
    ok = proc.returncode == 0
    if not ok:
        log("Failed to set Linux time: " + (proc.stderr or "").strip())

    if have("timedatectl"):
        subprocess.run(["timedatectl", "set-ntp", "true"], capture_output=True)

    return ok


def set_time_windows_epoch(epoch):
    stop_cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Stop-Service w32time -ErrorAction SilentlyContinue",
    ]
    start_cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Start-Service w32time -ErrorAction SilentlyContinue",
    ]
    set_cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            f"$u={epoch}; "
            "$t=[DateTimeOffset]::FromUnixTimeSeconds($u).LocalDateTime; "
            "Set-Date -Date $t"
        ),
    ]

    subprocess.run(stop_cmd, capture_output=True, text=True)
    proc = subprocess.run(set_cmd, capture_output=True, text=True)
    ok = proc.returncode == 0
    if not ok:
        log("Failed to set Windows time: " + (proc.stderr or "").strip())
    subprocess.run(start_cmd, capture_output=True, text=True)
    return ok



def sync_once(serial, drift_threshold=1):
    # grab time and timezone information from the phone
    phone_ts = phone_epoch(serial)
    if phone_ts is None:
        log("Failed to read epoch from phone.")
        return False

    phone_offset = phone_offset_hhmm(serial)
    phone_tz = phone_tz_id(serial)

    host_ts = int(time.time())
    drift = phone_ts - host_ts

    log(f"Phone epoch: {phone_ts} | Host epoch: {host_ts} | Drift: {drift}s")
    if phone_tz:
        log(f"Phone timezone: {phone_tz}")
    if phone_offset:
        log(f"Phone offset: {phone_offset}")

    ok_tz = True
    ok_time = True

    if os_is_windows():
        elevate_windows()
        ok_tz = set_timezone_windows(phone_tz, phone_offset)
        if abs(drift) >= drift_threshold:
            ok_time = set_time_windows_epoch(phone_ts)
        else:
            log("Drift below threshold; skipping time change.")
    else:
        elevate_linux()
        ok_tz = set_timezone_linux(phone_tz, phone_offset)
        if abs(drift) >= drift_threshold:
            ok_time = set_time_linux_epoch(phone_ts)
        else:
            log("Drift below threshold; skipping time change.")

    if ok_tz and ok_time:
        log("System time and timezone updated.")
    elif ok_tz and not ok_time:
        log("Timezone updated; time unchanged due to error or threshold.")
    elif not ok_tz and ok_time:
        log("Time updated; timezone unchanged (no mapping or failure).")
    else:
        log("Failed to update time and timezone.")

    return ok_tz and ok_time


def cmd_list():
    devices = adb_devices()
    if not devices:
        log("No ADB devices found.")
        return 1

    log("Detected devices:")
    for idx, (serial, state) in enumerate(devices, 1):
        log(f"  {idx}: {serial} [{state}]")
    return 0


def cmd_device(args):
    devices = adb_devices()
    if not devices:
        log("No ADB devices found. Connect or use `adb connect host:port`.")
        return 1

    if not args:
        return cmd_list()

    try:
        idx = int(args[0])
    except ValueError:
        log("Usage: toadb device N   (use 'toadb list' first)")
        return 1

    if idx < 1 or idx > len(devices):
        log("Invalid device number.")
        return 1

    serial = devices[idx - 1][0]
    cfg = load_cfg()
    cfg["selected_serial"] = serial
    save_cfg(cfg)
    log(f"Selected device: {serial}")
    return 0


def cmd_reset():
    reset_cfg()
    log("toadb config reset.")
    return 0


def cmd_resync():
    cfg = load_cfg()
    serial = pick_serial(cfg.get("selected_serial"))
    if not serial:
        log("No devices detected. Waiting for one...")
        subprocess.run(["adb", "wait-for-device"], capture_output=True, text=True)
        serial = pick_serial(None)
        if not serial:
            log("Still no device.")
            return 1

    log(f"Using device: {serial} ({device_model(serial)})")
    wait_for_authorized(serial)
    return 0 if sync_once(serial) else 1


def _handle_signal(sig, frame):
    log(f"Received signal {sig}; exiting.")
    close_log()
    sys.exit(0)


def run_boot_cycle(oneshot=False):
    connect_target = os.environ.get("ADB_CONNECT", "").strip()
    discovery_interval = int(os.environ.get("DISCOVERY_INTERVAL", "5"))
    startup_window = int(os.environ.get("STARTUP_WINDOW", "900"))
    refresh_interval = int(os.environ.get("REFRESH_INTERVAL", "600"))
    drift_threshold = int(os.environ.get("DRIFT_THRESHOLD", "1"))

    log(
        f"toadb daemon: discovery every {discovery_interval}s for {startup_window}s, "
        f"then refresh every {refresh_interval}s on success."
    )

    if not have("adb"):
        log("adb not in PATH. Exiting.")
        sys.exit(127)

    run(["adb", "start-server"])
    start_time = time.monotonic()
    had_success = False
    last_serial = None

    while True:
        try:
            if connect_target:
                subprocess.run(
                    ["adb", "connect", connect_target],
                    capture_output=True,
                    text=True,
                )

            devices = adb_devices()
            if not devices:
                if oneshot:
                    log("No devices; oneshot mode exiting.")
                    return
                if not had_success and (time.monotonic() - start_time >= startup_window):
                    log(
                        "No device authorized within startup window; exiting until next boot."
                    )
                    sys.exit(0)
                time.sleep(discovery_interval)
                continue

            cfg = load_cfg()
            prefer = cfg.get("selected_serial")
            serial = pick_serial(prefer)

            online_count = sum(1 for _, st in devices if st == "device")
            if online_count > 1 and not prefer:
                log(
                    "2+ online devices detected: using the first. "
                    "Set one with: toadb list | toadb device N"
                )

            if serial != last_serial:
                log(f"Watching device: {serial} ({device_model(serial)})")
                last_serial = serial

            wait_for_authorized(serial)

            if sync_once(serial, drift_threshold=drift_threshold):
                had_success = True

            if oneshot:
                return

            if had_success:
                time.sleep(refresh_interval)
            else:
                if time.monotonic() - start_time >= startup_window:
                    log(
                        "Startup window expired without a successful sync; "
                        "exiting until next boot."
                    )
                    sys.exit(0)
                time.sleep(discovery_interval)

        except Exception as exc:
            log(f"[warn] loop error: {exc}")
            if oneshot:
                return
            if had_success:
                time.sleep(refresh_interval)
            else:
                if time.monotonic() - start_time >= startup_window:
                    log(
                        "Startup window expired after errors; exiting until next boot."
                    )
                    sys.exit(0)
                time.sleep(discovery_interval)


def main():
    _open_log_file()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle_signal)
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
        print(
            """toadb commands:
  toadb                Run daemon (15 min discovery window, then 10 min refresh after success)
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
"""
        )
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
