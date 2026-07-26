from __future__ import annotations

import argparse
from pathlib import Path

from .api import accept_parcel


def main() -> int:
    parser = argparse.ArgumentParser(description="Record one fixture parcel")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--parcel-id", default="fixture-parcel")
    args = parser.parse_args()
    accept_parcel(args.db, args.parcel_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
