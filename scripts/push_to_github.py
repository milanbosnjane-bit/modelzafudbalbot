#!/usr/bin/env python3
"""Create GitHub repo and push using git credential manager token (Windows)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OWNER = "milanbosnjane-bit"
REPO = "modelzafudbalbot"
REMOTE = f"https://github.com/{OWNER}/{REPO}.git"


def run(cmd: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, encoding="utf-8", errors="replace")


def get_github_token() -> tuple[str, str]:
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )
    username = password = ""
    for line in proc.stdout.splitlines():
        if line.startswith("username="):
            username = line.split("=", 1)[1]
        elif line.startswith("password="):
            password = line.split("=", 1)[1]
    if not password:
        raise RuntimeError(
            "Nema sacuvanog GitHub tokena. Pokreni: gh auth login -h github.com -p https -w"
        )
    return username, password


def gh_api(method: str, path: str, token: str, payload: dict | None = None) -> tuple[int, dict | str]:
    url = f"https://api.github.com{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "football-roi-bot-push",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = body
        return exc.code, parsed


def ensure_repo(token: str) -> None:
    status, data = gh_api("GET", f"/repos/{OWNER}/{REPO}", token)
    if status == 200:
        print(f"Repo vec postoji: https://github.com/{OWNER}/{REPO}")
        return
    if status != 404:
        raise RuntimeError(f"GitHub GET repo failed ({status}): {data}")

    status, data = gh_api(
        "POST",
        "/user/repos",
        token,
        {
            "name": REPO,
            "description": "Football ROI Bot + iOS SwiftUI app + GitHub Actions IPA build",
            "private": False,
            "auto_init": False,
        },
    )
    if status not in (200, 201):
        raise RuntimeError(f"GitHub create repo failed ({status}): {data}")
    print(f"Kreiran repo: https://github.com/{OWNER}/{REPO}")


def push_main(token: str, username: str) -> None:
    run(["git", "remote", "remove", "origin"])
    # Embed token for non-interactive push; remote URL is local git config only.
    auth_remote = f"https://{username}:{token}@github.com/{OWNER}/{REPO}.git"
    run(["git", "remote", "add", "origin", auth_remote])
    proc = run(["git", "push", "-u", "origin", "main"])
    # Scrub tokenized remote URL after push.
    run(["git", "remote", "set-url", "origin", REMOTE])
    if proc.returncode != 0:
        raise RuntimeError(f"git push failed:\n{proc.stdout}\n{proc.stderr}")
        print("Push uspesan: origin/main")


def trigger_actions_hint() -> None:
    print("\nSledece:")
    print(f"  Actions: https://github.com/{OWNER}/{REPO}/actions")
    print("  Workflow 'Build iOS IPA' bi trebalo da krene automatski posle push-a.")


def main() -> int:
    try:
        username, token = get_github_token()
        print(f"GitHub auth OK (user: {username})")
        ensure_repo(token)
        push_main(token, username)
        trigger_actions_hint()
        return 0
    except Exception as exc:
        print(f"[GRESKA] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
