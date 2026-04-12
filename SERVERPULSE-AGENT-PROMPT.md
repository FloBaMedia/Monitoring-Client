# ServerPulse Agent – Implementierungsplan

## Technologie-Entscheidung

### Warum Python 3 (stdlib only)?

| Kriterium | Bash | **Python 3** | Go Binary | Node.js |
|---|---|---|---|---|
| Vorinstalliert auf Linux | ✅ | ✅ | ❌ | ❌ |
| Windows-Support | ❌ | ✅ | ✅ | ✅ |
| Keine externen Deps | ✅ | ✅ | ✅ | ❌ |
| JSON-Handling | Schlecht | ✅ | ✅ | ✅ |
| Komplexe Systemabfragen | Fragil | ✅ | ✅ | ✅ |
| Single-File | ✅ | ✅ | ❌ (build nötig) | ❌ |
| Installation | Simpel | Simpel | Download Binary | npm install |

**Entscheidung: Python 3 mit ausschließlich stdlib** (`json`, `urllib.request`, `configparser`, `subprocess`, `platform`, `socket`, `os`).

Python 3.6+ ist auf allen modernen Linux-Distributionen vorinstalliert:
- Ubuntu 18.04+ ✅
- Debian 9+ ✅
- CentOS 7+ (als `python3` oder `python36`) ✅
- RHEL 8+ ✅
- Fedora ✅
- Arch ✅
- Alpine (`apk add python3` – eine Zeile) ✅
- Windows (Python 3 installierbar, `wmic`-basierte Metriken) ✅

---

## Dateistruktur

```
agent/
├── agent.py              ← Einzige Datei, die auf den Server kommt
├── install.sh            ← Installer (curl | bash)
└── install-windows.ps1   ← Windows-Installer (PowerShell)
```

Der Agent ist **eine einzelne `.py` Datei** – kein Verzeichnis, keine Dependencies, kein Build-Schritt.

---

## Konfiguration

### Datei: `/etc/serverpulse/agent.conf`

```ini
[serverpulse]
api_url  = https://api.yourdomain.com
api_key  = sp_live_xxxxxxxxxxxxxxxxxxxxxxxx
```

Alternativ per **Umgebungsvariablen** (haben Vorrang über Konfig-Datei):
```bash
SERVERPULSE_API_URL=https://api.yourdomain.com
SERVERPULSE_API_KEY=sp_live_…
```

Config-Suche in dieser Reihenfolge:
1. ENV-Variablen `SERVERPULSE_API_URL` + `SERVERPULSE_API_KEY`
2. `/etc/serverpulse/agent.conf`
3. `~/.config/serverpulse/agent.conf` (User-Level, für non-root)
4. `./agent.conf` (lokales Verzeichnis, nützlich für Windows)

---

## Gesammelte Metriken

Alle Felder entsprechen exakt dem `PostMetricsBody` der API.

### Linux – Quellen

| Feld | Quelle |
|---|---|
| `cpuUsagePercent` | `/proc/stat` – zwei Reads mit 0.1s Pause → Delta-Berechnung |
| `cpuCores` | `/proc/cpuinfo` – Anzahl `processor`-Zeilen |
| `loadAvg1/5/15` | `/proc/loadavg` – erste drei Felder |
| `memTotalMb` / `memUsedMb` / `memUsagePercent` | `/proc/meminfo` – `MemTotal`, `MemAvailable` |
| `swapTotalMb` / `swapUsedMb` | `/proc/meminfo` – `SwapTotal`, `SwapFree` |
| `diskUsages` | `os.statvfs()` auf alle Mountpoints aus `/proc/mounts` (filtert tmpfs/devtmpfs/etc.) |
| `networkInterfaces` | `/proc/net/dev` – rx/tx bytes + packets |
| `processCount` | Anzahl Verzeichnisse in `/proc/` die nur Ziffern enthalten |
| `openFiles` | `/proc/sys/fs/file-nr` – erstes Feld |
| `ioReadKbps` / `ioWriteKbps` | `/proc/diskstats` – Delta über 0.1s (nur physische Disks: `sd*`, `nvme*`, `vd*`) |
| `os` | `/etc/os-release` – `PRETTY_NAME` |
| `kernelVersion` | `platform.release()` |
| `uptimeSeconds` | `/proc/uptime` – erstes Feld |
| `agentVersion` | Hardcoded im Script, z.B. `"1.0.0"` |

### Windows – Quellen

| Feld | Quelle |
|---|---|
| `cpuUsagePercent` | `wmic cpu get LoadPercentage` |
| `cpuCores` | `wmic cpu get NumberOfCores` |
| `loadAvg*` | Nicht nativ verfügbar → CPU-Usage als Approximation, loadAvg = 0 |
| `memTotal/Used` | `wmic OS get TotalVisibleMemorySize,FreePhysicalMemory` |
| `diskUsages` | `wmic logicaldisk get Size,FreeSpace,DeviceID` |
| `networkInterfaces` | `netstat -e` oder `wmic nic` |
| `processCount` | `wmic process get processid \| find /c /v ""` |
| `os` | `platform.version()` |
| `kernelVersion` | `platform.release()` |
| `uptimeSeconds` | `wmic os get LastBootUpTime` → delta zu jetzt |

---

## Agent-Ablauf (pro Ausführung)

```
1. Config laden (ENV > Datei > Default)
2. Plattform erkennen (linux / windows)
3. Alle Metriken sammeln (inkl. CPU-Delta über 100ms)
4. JSON-Payload bauen
5. POST /api/v1/agent/metrics mit X-Server-Key Header
6. Bei Fehler: in /var/log/serverpulse-agent.log schreiben (max. 1 MB, dann rotieren)
7. Exit 0 (success) oder Exit 1 (error)
```

Kein Daemon, kein Loop – jede Ausführung ist **stateless**. Der Crontab-Job managed die Frequenz.

---

## Ausführungsintervall

**Empfehlung: jede Minute via Crontab**

```cron
* * * * * /usr/bin/python3 /etc/serverpulse/agent.py >> /var/log/serverpulse-agent.log 2>&1
```

Begründung:
- API speichert Rohmetriken per Minute
- Retention-Default: 30 Tage → ~43.200 Datenpunkte pro Server – vertretbar
- Dashboard-Charts mit 5m/1h/1d Aggregation bleiben nutzbar
- Crontab-Minimum ist 1 Minute; für feinere Auflösung wäre ein Systemd-Timer oder ein Daemon nötig

**Systemd-Timer Alternative** (für Sub-Minuten-Frequenz, z.B. alle 30s):
```ini
# /etc/systemd/system/serverpulse-agent.timer
[Timer]
OnBootSec=30s
OnUnitActiveSec=30s
```

---

## Installer (`install.sh`)

### Was der Installer macht:
1. Python 3 prüfen (mind. 3.6), abbrechen wenn nicht vorhanden
2. `agent.py` nach `/etc/serverpulse/agent.py` kopieren
3. Konfig-Datei anlegen (fragt `API_URL` und `API_KEY` interaktiv ab)
4. Crontab-Eintrag für root hinzufügen (falls nicht schon vorhanden)
5. Einen ersten Test-Run ausführen und Ergebnis zeigen

### One-Liner Installation:
```bash
curl -fsSL https://raw.githubusercontent.com/FloBaMedia/Monitoring-API/main/agent/install.sh | bash
```

Oder manuell:
```bash
# 1. Agent herunterladen
sudo mkdir -p /etc/serverpulse
sudo curl -o /etc/serverpulse/agent.py \
  https://raw.githubusercontent.com/FloBaMedia/Monitoring-API/main/agent/agent.py

# 2. Konfig anlegen
sudo tee /etc/serverpulse/agent.conf <<EOF
[serverpulse]
api_url = https://api.yourdomain.com
api_key = sp_live_DEIN_KEY_HIER
EOF
sudo chmod 600 /etc/serverpulse/agent.conf

# 3. Crontab einrichten
(sudo crontab -l 2>/dev/null; echo "* * * * * /usr/bin/python3 /etc/serverpulse/agent.py") | sudo crontab -

# 4. Test-Run
sudo python3 /etc/serverpulse/agent.py
```

### Windows-Installation (PowerShell als Admin):
```powershell
# Verzeichnis anlegen
New-Item -ItemType Directory -Force -Path "C:\ProgramData\ServerPulse"

# Agent herunterladen
Invoke-WebRequest -Uri "https://.../agent.py" -OutFile "C:\ProgramData\ServerPulse\agent.py"

# Konfig anlegen
@"
[serverpulse]
api_url = https://api.yourdomain.com
api_key = sp_live_DEIN_KEY_HIER
"@ | Set-Content "C:\ProgramData\ServerPulse\agent.conf"

# Scheduled Task (jede Minute)
$action  = New-ScheduledTaskAction -Execute "python" -Argument "C:\ProgramData\ServerPulse\agent.py"
$trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 1) -Once -At (Get-Date)
Register-ScheduledTask -TaskName "ServerPulseAgent" -Action $action -Trigger $trigger -RunLevel Highest
```

---

## Fehlerbehandlung & Logging

```
/var/log/serverpulse-agent.log
```

- Maxgröße: **1 MB** → danach wird die Datei rotiert (simples truncate-Rotations-Schema im Script selbst, kein logrotate nötig)
- Format: `[2025-01-15 14:32:01] INFO  POST /api/v1/agent/metrics → 201 (0.23s)`
- Bei HTTP-Fehler (4xx/5xx): vollständige Response in Log schreiben
- Bei Netzwerkfehler: Exception-Nachricht loggen, Exit 1
- Kein Crash bei einzelnen fehlgeschlagenen Metriken (z.B. kein Swap → `swapTotal=0`)

---

## Sicherheit

- Config-Datei mit `chmod 600` (nur root lesbar)
- API-Key wird **nicht** in Umgebungsvariablen von Kindprozessen weitergegeben
- HTTPS-Verbindung mit Zertifikat-Validierung (Standard in `urllib`)
- Timeout: 10 Sekunden für den HTTP-Request (kein Hängen bei API-Ausfall)
- Agent läuft als **root** (notwendig für `/proc`-Lesezugriffe und vollständige Disk-Stats)
  - Alternative: dedizierter `serverpulse` User mit CAP_SYS_PTRACE wenn nötig

---

## API-Mapping

```python
payload = {
    # System
    "os": ...,              # /etc/os-release PRETTY_NAME
    "kernelVersion": ...,   # platform.release()
    "uptimeSeconds": ...,   # /proc/uptime
    "agentVersion": "1.0.0",

    # CPU
    "cpuUsagePercent": ..., # /proc/stat delta
    "cpuCores": ...,        # /proc/cpuinfo
    "loadAvg1": ...,        # /proc/loadavg[0]
    "loadAvg5": ...,        # /proc/loadavg[1]
    "loadAvg15": ...,       # /proc/loadavg[2]

    # Memory
    "memTotalMb": ...,
    "memUsedMb": ...,
    "memUsagePercent": ...,
    "swapTotalMb": ...,
    "swapUsedMb": ...,

    # Disk (Array)
    "diskUsages": [
        {
            "mountpoint": "/",
            "totalGb": ...,
            "usedGb": ...,
            "usagePercent": ...,
            "filesystem": "ext4"
        }
    ],

    # Network (Array)
    "networkInterfaces": [
        {
            "name": "eth0",
            "rxBytes": ...,
            "txBytes": ...,
            "rxPackets": ...,
            "txPackets": ...
        }
    ],

    # Processes
    "processCount": ...,    # /proc/ dirs
    "openFiles": ...,       # /proc/sys/fs/file-nr
    "ioReadKbps": ...,      # /proc/diskstats delta
    "ioWriteKbps": ...,     # /proc/diskstats delta
}
```

---

## Heartbeat vs. Metrics

Der Agent nutzt **nur** `POST /api/v1/agent/metrics` – kein separater Heartbeat nötig, da der Metrics-Endpoint intern `updateServerSeen()` aufruft und den Server auf ONLINE setzt.

Der `/api/v1/agent/heartbeat` Endpoint existiert für zukünftige leichtgewichtige Agenten (z.B. IoT-Devices), die keine vollen Metriken senden können.

---

## Offene Fragen

_Keine – Implementierung kann direkt beginnen._
