#!/usr/bin/env python3
"""
Deploy market filter changes (supported markets + disabled market rules) and restart services.

Uploads only the three filter files, never .env. PrelaziBot on port 8000 is untouched.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")

FILES = (
    "app/config.py",
    "app/services/ingestion.py",
    "app/predictions/probability_layer.py",
    "app/predictions/market_selection.py",
)

SERVICES = ("football-dc-scheduler", "football-dc-telegram", "football-dc-api")

VERIFY = r"""
from app.config import get_settings
from app.predictions.probability_layer import is_disabled_market
from app.predictions.market_selection import is_eligible_selection
from app.services.ingestion import DataIngestionService

s = get_settings()
svc = DataIngestionService.__new__(DataIngestionService)

print("supported_markets:", s.supported_markets)

print("\n--- glavni marketi MORAJU proci ---")
for name, expect in (("Match Winner", "match_winner"),
                     ("Goals Over/Under", "over_under"),
                     ("Both Teams Score", "btts")):
    got = svc._normalize_market(name)
    print(f"  {name!r:22s} -> {got}   {'OK' if got == expect else 'GRESKA'}")

print("\n--- ono sto se ranije provlacilo MORA biti None ---")
for name in ("Goals Over/Under First Half", "Home Team Total Goals(1st Half)",
             "Result/Total Goals", "Corners Over Under", "Cards Over/Under",
             "Home/Away", "Corners 1x2", "1x2 - 15 minutes", "Fouls. 1x2",
             "Both Teams Score - First Half", "Total Goals/Both Teams To Score",
             "Asian Handicap", "Double Chance", "Exact Score"):
    got = svc._normalize_market(name)
    print(f"  {name!r:36s} -> {got}   {'OK' if got is None else 'GRESKA'}")

print("\n--- kanonske selekcije ---")
for m, raw in (("match_winner", "Home"), ("match_winner", "1"), ("btts", "Yes"),
               ("over_under", "Over 2.5"), ("over_under", "Away/Over 2.5"),
               ("over_under", "Over 0.5")):
    print(f"  {m}/{raw!r:16s} -> {svc._canonical_selection(m, raw)}")

print("\n--- pick filteri jos rade ---")
for m, sel, line in (("match_winner", "Home", None), ("btts", "Yes", None), ("over_under", "Over 2.5", 2.5)):
    print(f"  {m}/{sel}: disabled={is_disabled_market(m)} eligible={is_eligible_selection(m, sel, line, live=True)}")
"""


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 300) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return (out + err).rstrip()


def main() -> int:
    for rel in FILES:
        if not (ROOT / rel).is_file():
            safe_print(f"[GRESKA] Nedostaje {rel}")
            return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    safe_print(f"Deploy market filtera -> {USER}@{HOST}  (PrelaziBot :8000 netaknut)")
    sftp = client.open_sftp()
    for rel in FILES:
        sftp.put(str(ROOT / rel), f"{REMOTE}/{rel}")
        safe_print(f"  upload {rel}")
    with sftp.open("/tmp/_verify_markets.py", "w") as fh:
        fh.write(VERIFY)
    sftp.close()

    safe_print("\n=== Efektivna konfiguracija posle uploada ===")
    safe_print(
        run(
            client,
            f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 LOCAL_MODE=true "
            f"venv/bin/python /tmp/_verify_markets.py 2>&1",
        )
    )

    safe_print("\n=== Restart servisa ===")
    safe_print(
        run(
            client,
            "systemctl --user restart " + " ".join(f"{s}.service" for s in SERVICES) + " 2>&1; "
            "sleep 6; systemctl --user is-active " + " ".join(f"{s}.service" for s in SERVICES) + " 2>&1",
        )
    )

    safe_print("\n=== API health ===")
    safe_print(run(client, "curl -sf -m 10 http://127.0.0.1:8001/api/v1/health; echo"))

    client.close()
    safe_print("\nDeploy zavrsen. Sledeci ingest ciklus vise ne treba da loguje double_chance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
