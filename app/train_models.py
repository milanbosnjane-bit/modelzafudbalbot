"""Deprecated alias — koristi app.calibrate_models (Dixon-Coles MLE, v3)."""

from __future__ import annotations

import sys

from app.calibrate_models import main as calibrate_main


def main() -> int:
    print(
        "[DEPRECATED] train_models → calibrate_models\n"
        "             ML treniranje uklonjeno u v3 — koristi Dixon-Coles MLE kalibraciju.\n"
    )
    return calibrate_main()


if __name__ == "__main__":
    raise SystemExit(main())
