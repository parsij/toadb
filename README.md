<h1 align="center">Toad-B</h1>
<p align="center"><em>Time Over ADB — sync your PC’s clock from your Android device</em></p>

<p align="center">
  <a href="https://github.com/parsij/toadb"><img src="https://img.shields.io/badge/Platform-Linux%20%7C%20Windows-blue" alt="Platform"></a>
  <img src="https://img.shields.io/badge/Windows-Experimental-orange" alt="Windows Experimental">
  <a href="https://github.com/parsij/toadb/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green" alt="License"></a>
</p>


---

**What’s “toadb”?**  
toadb = Time Over ADB(Android Debugging Bridge).

> Windows port is **experimental**. Linux is the primary, well-tested target.

---

## 🚀 One-liner install


### Linux (wget)

This installs ADB if needed, drops `main.py` into `/usr/local/share/adb_time/`, installs a `toadb` CLI shim, and sets up a **systemd timer** to start **30s after boot**.

```bash
bash -c 'wget -qO- https://raw.githubusercontent.com/parsij/toadb/main/toadb.sh | bash'
````

Logs: `journalctl -fu adb_time.service`

### Windows (PowerShell — run as Administrator)

This downloads and runs the bootstrap installer. It self-elevates, lays files in `C:\Program Files\adb_time_sync\`, creates a startup task that runs **30s after boot** as **SYSTEM**, adds a `toadb` command, and tries to install ADB/Python via winget if missing.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -c "iwr -useb https://raw.githubusercontent.com/parsij/toadb/main/toadb_bootstrap.ps1 | iex"
```

First run under SYSTEM triggers the Android USB debugging prompt. Tick **“Always allow from this computer.”**
Logs: `C:\Program Files\adb_time_sync\toadb.log`

#### Windows (wget variant)

```cmd
wget -O %TEMP%\toadb_bootstrap.ps1 https://raw.githubusercontent.com/parsij/toadb/main/toadb_bootstrap.ps1 && powershell -NoProfile -ExecutionPolicy Bypass -File %TEMP%\toadb_bootstrap.ps1
```

**Behind a proxy?**

```bash
bash -c 'HTTPS_PROXY=http://PROXY_IP_ADDRES:PORT_NUMBER HTTP_PROXY=http://PROXY_IP_ADDRES:PORT_NUMBER wget -qO- https://raw.githubusercontent.com/parsij/toadb/main/toadb.sh | bash'
```

## Need more help or don't know how what to do ?

**Watch this YouTube video dedicated to Toad-B**

<p align="center">
  <a href="https://www.youtube.com/watch?v=iSBMmWQCinE" target="_blank">
    <img src="https://img.youtube.com/vi/iSBMmWQCinE/maxresdefault.jpg" alt="Toad-B setup & usage video" width="480">
  </a>
</p>

<p align="center">
  <a href="https://www.youtube.com/watch?v=iSBMmWQCinE"><strong>▶ Watch the Toad-B setup video on YouTube</strong></a>
</p>

## ✨ What it does

* **On boot:** probe every 5 seconds for up to **15 minutes** (startup window). If no phone authorizes, it exits quietly until next boot.
* **After the first success:** resync every **10 minutes** to avoid clock drift.
* Works over **USB** or **Wi-Fi ADB** (`ADB_CONNECT=host:port`).

## 🧰 CLI (what each command does)

```text
toadb                 # run daemon in the foreground (same behavior as on boot)
toadb oneshot         # one-time sync, then exit (waits for authorization)
toadb resync          # force a sync now (waits for authorization)
toadb list            # list connected devices with their state
toadb device N        # select device by number (persists selection)
toadb reset           # clear saved selection/config
```

**Best practice right after install:**

1. Plug your phone, enable USB debugging, then run:

   ```bash
   toadb list
   ```
2. Pick your device:

   ```bash
   toadb device N   # N is the number shown by "toadb list"
   ```

## ⚙️ Configuration

### Linux: `/etc/default/adb_time`

Copy/paste and edit as needed:

```bash
# Optional Wi-Fi ADB
# ADB_CONNECT=192.168.49.1:9800

# Probe cadence before first success (seconds)
DISCOVERY_INTERVAL=5

# Give up quietly if no device authorizes within this window (seconds)
STARTUP_WINDOW=900

# After first success, refresh every N seconds
REFRESH_INTERVAL=600

# Ignore drift under N seconds
DRIFT_THRESHOLD=1

# Optional proxy env if you later add HTTP calls
# HTTP_PROXY=http://proxy:3128
# HTTPS_PROXY=http://proxy:3128
# NO_PROXY=localhost,127.0.0.1,::1,192.168.0.0/16
```

Apply changes:

```bash
sudo systemctl daemon-reload
sudo systemctl restart adb_time.timer
```

### Windows

* Optional Wi-Fi ADB or tuning: edit `C:\Program Files\adb_time_sync\run_daemon.cmd` and uncomment the env vars at the top.
* Make sure your timezone is correct:

  ```powershell
  tzutil /g
  # tzutil /s "Pacific Standard Time"
  ```

## ✅ Requirements

* Android device with **USB debugging** enabled (Developer options).
* ADB available on the host (installers try to add it).
* For Wi-Fi ADB (optional): device and host on the same network; set `ADB_CONNECT=host:port`.

## 🧹 Uninstall

### Linux

```bash
sudo systemctl disable --now adb_time.timer
sudo systemctl disable --now adb_time.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/adb_time.timer /etc/systemd/system/adb_time.service
sudo rm -f /usr/local/bin/toadb
sudo rm -rf /usr/local/share/adb_time /etc/default/adb_time /etc/toadb
sudo systemctl daemon-reload
```

### Windows (elevated PowerShell)

```powershell
schtasks /Delete /TN toadbDaemon /F
Remove-Item -Force "C:\Windows\System32\toadb.cmd"
Remove-Item -Recurse -Force "C:\Program Files\adb_time_sync"
```

## 🤝 Credit

If you use, distribute, or modify this project, **you must give credit** to:

**Parsa Poosti (Parsij)**

Keep the attribution in your README, docs, about box, or wherever you present the project.

## 💼 Work with me

Happy to help on your projects. Hiring or collab: **[parsapoosti@gmail.com](mailto:parsapoosti@gmail.com)**

## 📝 License (MIT)

This project is MIT-licensed. The license text is in `LICENSE`. Attribution to **Parsa Poosti (Parsij)** must remain in derivative works and distributions.