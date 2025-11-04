#!/usr/bin/env python3
# toadb main.py — ADB time sync with boot startup window + periodic refresh
# - On boot: scan every DISCOVERY_INTERVAL (default 5s) for up to STARTUP_WINDOW (default 900s).
#   If no device authorizes, exit quietly until next boot.
# - If a sync succeeds during the window, stay running and resync every REFRESH_INTERVAL (default 600s).
# - CLI: `toadb`, `toadb resync`, `toadb list`, `toadb device N`, `toadb reset`.

import os, sys, time, json, shutil, platform, subprocess
from typing import List, Tuple, Optional

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
    r = run(["adb", "devices"])
    return parse_adb_devices(r.stdout)

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
    # Wait for device to appear at all
    subprocess.run(["adb", "-s", serial, "wait-for-device"], capture_output=True, text=True)
    # Then wait until authorized and responsive
    while True:
        state = dict(adb_devices()).get(serial, "")
        if state == "device":
            t = subprocess.run(["adb", "-s", serial, "shell", "echo", "ok"], capture_output=True, text=True)
            if t.returncode == 0 and "ok" in (t.stdout or ""):
                print("ADB device authorized.")
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

def maybe_connect_tcp(target: str):
    if target:
        subprocess.run(["adb", "connect", target], capture_output=True, text=True)

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
        print("Need root privileges but neither pkexec nor sudo is available.")
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

# ---------- set time ----------
def set_time_linux_epoch(epoch: int) -> bool:
    if have("timedatectl"):
        subprocess.run(["timedatectl", "set-ntp", "false"], capture_output=True)
    p = subprocess.run(["date", "-u", "-s", f"@{epoch}"], capture_output=True, text=True)
    ok = (p.returncode == 0)
    if not ok:
        print("Failed to set Linux time:", (p.stderr or "").strip())
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
        print("Failed to set Windows time:", (p.stderr or "").strip())
    subprocess.run(start, capture_output=True, text=True)
    return ok

# ---------- actions ----------
def sync_once(serial: str, drift_threshold: int = 1) -> bool:
    pe = phone_epoch(serial)
    if pe is None:
        print("Failed to read epoch from phone.")
        return False
    host = int(time.time())
    drift = pe - host
    print(f"Phone epoch: {pe} | Host epoch: {host} | Drift: {drift}s")
    if abs(drift) < drift_threshold:
        print("Drift below threshold; no change.")
        return True
    if os_is_windows():
        elevate_windows()
        ok = set_time_windows_epoch(pe)
    else:
        elevate_linux()
        ok = set_time_linux_epoch(pe)
    if ok: print("✅ System time updated.")
    return ok

def cmd_list() -> int:
    devs = adb_devices()
    if not devs:
        print("No ADB devices found.")
        return 1
    print("Detected devices:")
    for i, (s, st) in enumerate(devs, 1):
        print(f"  {i}: {s} [{st}]")
    return 0

def cmd_device(args: List[str]) -> int:
    devs = adb_devices()
    if not devs:
        print("No ADB devices found. Connect or `adb connect host:port`.")
        return 1
    if not args:
        return cmd_list()
    try:
        idx = int(args[0])
    except ValueError:
        print("Usage: toadb device N   (use 'toadb list' first)")
        return 1
    if idx < 1 or idx > len(devs):
        print("Invalid device number.")
        return 1
    serial = devs[idx - 1][0]
    cfg = load_cfg()
    cfg["selected_serial"] = serial
    save_cfg(cfg)
    print(f"Selected device: {serial}")
    return 0

def cmd_reset() -> int:
    reset_cfg()
    print("toadb config reset.")
    return 0

def cmd_resync() -> int:
    cfg = load_cfg()
    s = pick_serial(cfg.get("selected_serial"))
    if not s:
        print("No devices detected. Waiting for one...")
        subprocess.run(["adb", "wait-for-device"], capture_output=True, text=True)
        s = pick_serial(None)
        if not s:
            print("Still no device.")
            return 1
    print(f"Using device: {s}")
    wait_for_authorized(s)
    return 0 if sync_once(s) else 1

# ---------- daemon with startup window ----------
def run_boot_cycle():
    # Env knobs
    connect_target = os.environ.get("ADB_CONNECT", "").strip()
    discovery_interval = int(os.environ.get("DISCOVERY_INTERVAL", "5"))     # seconds between checks before first success
    startup_window = int(os.environ.get("STARTUP_WINDOW", "900"))           # 15 minutes default
    refresh_interval = int(os.environ.get("REFRESH_INTERVAL", "600"))       # 10 minutes default
    drift_threshold = int(os.environ.get("DRIFT_THRESHOLD", "1"))

    print(f"toadb daemon: discovery every {discovery_interval}s for {startup_window}s, "
          f"then refresh every {refresh_interval}s on success.")

    run(["adb", "start-server"])
    start = time.monotonic()
    had_success = False
    last_choice = None

    while True:
        try:
            if connect_target:
                maybe_connect_tcp(connect_target)

            devs = adb_devices()
            if not devs:
                # No devices at all
                if not had_success and (time.monotonic() - start >= startup_window):
                    print("No device authorized within startup window; exiting until next boot.")
                    sys.exit(0)
                time.sleep(discovery_interval)
                continue

            # Choose device
            cfg = load_cfg()
            prefer = cfg.get("selected_serial")
            serial = pick_serial(prefer)
            online_count = sum(1 for _, st in devs if st == "device")
            if online_count > 1 and not prefer:
                print("2+ online devices detected: using the first. Set one with: toadb list  |  toadb device N")
            if serial != last_choice:
                print(f"Watching device: {serial}")
                last_choice = serial

            # Wait for authorization
            wait_for_authorized(serial)

            # Attempt sync
            if sync_once(serial, drift_threshold=drift_threshold):
                had_success = True

            # If we had success, switch to periodic refresh
            if had_success:
                time.sleep(refresh_interval)
            else:
                # still within startup window but not yet synced
                if time.monotonic() - start >= startup_window:
                    print("Startup window expired without a successful sync; exiting until next boot.")
                    sys.exit(0)
                time.sleep(discovery_interval)

        except Exception as e:
            # Don't crash the daemon over transient junk
            print(f"[warn] loop error: {e}")
            # Respect time mode
            if had_success:
                time.sleep(refresh_interval)
            else:
                if time.monotonic() - start >= startup_window:
                    print("Startup window expired after errors; exiting until next boot.")
                    sys.exit(0)
                time.sleep(discovery_interval)

# ---------- CLI ----------
def main():
    args = sys.argv[1:]
    if not args:
        run_boot_cycle()
        return

    cmd = args[0].lower()
    rest = args[1:]

    if cmd in ("help", "-h", "--help"):
        print("""toadb commands:
  toadb                Run daemon (boot behavior: 15 min discovery window, then 10 min refresh after success)
  toadb resync         One-shot sync now (waits for authorization)
  toadb list           List connected devices
  toadb device [N]     Select device by number
  toadb reset          Clear saved selection/config

Env vars:
  ADB_CONNECT=host:port
  DISCOVERY_INTERVAL=5
  STARTUP_WINDOW=900
  REFRESH_INTERVAL=600
  DRIFT_THRESHOLD=1
""")
        return

    if cmd == "resync":
        sys.exit(cmd_resync())
    if cmd == "list":
        sys.exit(cmd_list())
    if cmd == "device":
        sys.exit(cmd_device(rest))
    if cmd == "reset":
        sys.exit(cmd_reset())

    print("Unknown command. Try 'toadb help'.")
    sys.exit(1)

if __name__ == "__main__":
    main()
