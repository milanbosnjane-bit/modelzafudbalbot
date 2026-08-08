# Football ROI Bot — iOS App

Native **SwiftUI** iOS aplikacija za Football ROI Bot backend. Komunicira isključivo preko REST API-ja (Tailscale) — **ne dira Python bot kod**.

## Arhitektura

```
iPhone (ios_app/)  ──HTTP──▶  FastAPI (:8001/api/v1)  ──▶  SQLite + DC Bot
```

> **PrelaziBot** koristi port **8000** na istom serveru — Football ROI API je na **8001** i ne dira PrelaziBot.

Python core (`app/predictions/`, `app/models/`, `app/services/`, `app/database/`, `app/features/`) ostaje netaknut.

Dodati su samo **read-only mobile endpointi** u `app/api/mobile_routes.py`:
- `GET /api/v1/status` — bot status + DC engine
- `GET /api/v1/picks/recent` — poslednji rešeni tipovi
- `GET /api/v1/odds/tracker` — live 1X2 kvote sa smerom (↑/↓)

## UI (Cyber Neon)

Replikuje mockup:
- Crna pozadina + cyber grid
- Glassmorphism paneli sa neon borderima
- Cyan / Green / Red / Purple paleta
- Pulsing `DC ENGINE: ONLINE` badge
- RILTAJM PRATIOC KVOTA panel
- 2×3 action grid (ROI, LIVE PICKS, REZULTATI, SETTLE, RESTART, STATUS)

## Preduslovi

### Na Mac-u (build)
- macOS 13+
- Xcode 15+
- Apple ID (besplatan developer nalog dovoljan za AltStore)

### Na serveru (backend)
FastAPI mora biti pokrenut i dostupan preko Tailscale:

```bash
cd /home/miki/football-dc-bot
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Proveri:
```bash
curl http://<TAILSCALE_IP>:8001/api/v1/health
curl http://<TAILSCALE_IP>:8001/api/v1/odds/tracker
```

Na iPhone-u instaliraj **Tailscale** i uključi se u istu mrežu.

---

## Korak 1: Otvori projekat u Xcode

```bash
cd ios_app
open FootballROIBot.xcodeproj
```

1. Izaberi target **FootballROIBot**
2. **Signing & Capabilities** → Team: tvoj Apple ID
3. Bundle Identifier: promeni u jedinstven (npr. `com.tvojeime.footballroi`)

---

## Korak 2: Podesi API URL u aplikaciji

Default: `http://100.122.226.3:8001/api/v1` (čuva se u `@AppStorage("baseURL")`)

U aplikaciji: **⚙️ STATUS BOTA** → unesi Tailscale IP (port **8001**):
```
http://100.122.226.3:8001/api/v1
```

Poll interval: 10–60 sekundi (default 20s).

`Info.plist` već dozvoljava HTTP (`NSAllowsArbitraryLoads`) za Tailscale LAN.

---

## Korak 3: Build & Run (simulator ili uređaj)

```
Product → Run (⌘R)
```

Za fizički iPhone:
1. Poveži USB kablom
2. Na telefonu: Settings → General → VPN & Device Management → Trust
3. Run iz Xcode-a

---

## Korak 4: Export .ipa za AltStore

### A) Archive (preporučeno)

1. Xcode → Product → **Archive**
2. Organizer → **Distribute App**
3. **Custom** → **App Store Connect** ili **Development**
4. Export `.ipa` na disk

### B) AltStore sideload

1. Instaliraj [AltServer](https://altstore.io) na Mac/Windows
2. Instaliraj AltStore na iPhone (preko AltServer-a)
3. AltStore → **My Apps** → **+** → izaberi `.ipa`
4. AltStore obnavlja app svakih 7 dana (besplatan Apple ID)

### C) ios-deploy (CLI, bez Archive)

```bash
xcodebuild -project FootballROIBot.xcodeproj \
  -scheme FootballROIBot \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath build/FootballROIBot.xcarchive archive

xcodebuild -exportArchive \
  -archivePath build/FootballROIBot.xcarchive \
  -exportPath build/export \
  -exportOptionsPlist ExportOptions.plist
```

Kreiraj `ExportOptions.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>method</key>
    <string>development</string>
    <key>teamID</key>
    <string>YOUR_TEAM_ID</string>
</dict>
</plist>
```

---

## API mapiranje (dugmad → endpointi)

| Dugme | HTTP | Endpoint |
|-------|------|----------|
| DC ENGINE badge | GET | `/health`, `/status` |
| RILTAJM KVOTA | GET | `/odds/tracker?limit=5` |
| ROI STATISTIKA | GET | `/paper/evaluate?days=30` |
| LIVE PICKS | GET | `/picks/today` |
| POSLEDNJI REZULTATI | GET | `/picks/recent?limit=10` |
| SETTLE NOW | POST | `/paper/settle` |
| RESTART RUN | POST | `/ingest` + `/predict` |
| STATUS BOTA | GET | `/status`, `/config` |

Background polling (`AppState.startPolling()`) osvežava health + odds tracker svakih N sekundi.

---

## Struktura projekta

```
ios_app/
├── FootballROIBot.xcodeproj/
├── FootballROIBot/
│   ├── FootballROIBotApp.swift      # @main entry
│   ├── Design/CyberTheme.swift      # Neon design system
│   ├── Models/APIModels.swift       # Codable API models
│   ├── Services/AppConfig.swift     # @AppStorage keys + URL normalize
│   ├── Services/AppServices.swift   # APIClient + polling
│   ├── Views/DashboardView.swift    # Main UI + sheets
│   ├── Views/SettingsView.swift     # Tailscale URL + Test Konekcije
│   ├── Assets.xcassets/
│   └── Info.plist
└── README.md
```

---

## Troubleshooting

| Problem | Rešenje |
|---------|---------|
| `DC ENGINE: OFFLINE` | Proveri Tailscale, FastAPI port **8001**, firewall |
| Prazan odds tracker | Nema NS mečeva/kvota u bazi — pokreni ingest |
| HTTP blocked | Proveri `NSAppTransportSecurity` u Info.plist |
| AltStore expired | Re-sign svakih 7 dana (besplatan Apple ID) |
| 404 on `/status` | Deploy `app/api/mobile_routes.py` na server |

---

## Bezbednost

- App **ne** pristupa bazi direktno
- App **ne** menja DC model, pick selector ni scheduler
- Samo REST pozivi ka postojećem FastAPI backend-u
- API URL se čuva lokalno u UserDefaults

---

**Verzija app:** 1.0.0 · **Backend:** v3.1 · **Min iOS:** 16.0 · **SwiftUI**

Automatski build: vidi [GITHUB_BUILD.md](GITHUB_BUILD.md) (GitHub Actions → `.ipa` artifact).
