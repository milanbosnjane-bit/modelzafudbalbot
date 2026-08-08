# GitHub Actions — iOS Build (bez lokalnog Xcode-a)

Workflow `.github/workflows/build-ios.yml` automatski pravi `FootballROIBot.ipa` na GitHub macOS runner-u.

## Kada se pokreće

- **Push** na `main` ili `master` (samo ako su menjani `ios_app/**` ili sam workflow)
- **Ručno:** GitHub → Actions → *Build iOS IPA* → *Run workflow*

## Preuzimanje IPA

1. Otvori repozitorijum na GitHub-u
2. **Actions** → poslednji zeleni run *Build iOS IPA*
3. Skroluj do **Artifacts**
4. Preuzmi **FootballROIBot-ipa** (zip sa `FootballROIBot.ipa`)

## Instalacija (AltStore)

1. Prebaci `.ipa` na iPhone (AirDrop / Files)
2. AltStore → **My Apps** → **+** → izaberi IPA
3. AltStore potpisuje app tvojim Apple ID-jem (re-sign na 7 dana)

> **Napomena:** CI build je **unsigned** (`CODE_SIGNING_ALLOWED=NO`). AltStore će app ipak potpisati pri sideload-u. Ako instalacija ne uspe, potreban je lokalni Xcode + Apple ID signing.

## Default API URL u app-u

```
http://100.122.226.3:8001/api/v1
```

(Port **8001** — PrelaziBot koristi **8000** i ne dira se.)

Promena IP-a kasnije: u app-u **STATUS BOTA** → TextField, ili izmena `AppConfig.defaultBaseURL` u kodu.

---

## Git commit i push (Windows)

Otvori terminal u root folderu projekta (`modelzafudbalbot`):

```powershell
cd "C:\Users\Miki\Desktop\modelzafudbalbot"

# Proveri status
git status

# Dodaj iOS app + GitHub workflow
git add ios_app/
git add .github/workflows/build-ios.yml
git add app/api/mobile_routes.py
git add app/api/routes.py
git add scripts/deploy_mobile_api.sh
git add scripts/deploy_mobile_api.py
git add scripts/server/fastapi.sh

# Commit
git commit -m "Add iOS SwiftUI app and GitHub Actions IPA build workflow"

# Prvi put — poveži remote (zameni URL svojim repo-om)
git remote add origin https://github.com/TVOJ_USER/modelzafudbalbot.git

# Push na main (ili master)
git branch -M main
git push -u origin main
```

### Ako repo već postoji na GitHub-u

```powershell
git pull origin main --rebase
git push origin main
```

### Ručno pokretanje build-a bez push-a

GitHub → **Actions** → **Build iOS IPA** → **Run workflow** → **Run workflow**

---

## Lokalna provera pre push-a (opciono)

Na Mac-u sa Xcode-om:

```bash
cd ios_app
xcodebuild -project FootballROIBot.xcodeproj -scheme FootballROIBot -configuration Release \
  -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO build
```

Na Windows-u nije moguć lokalni iOS build — koristi GitHub Actions.
