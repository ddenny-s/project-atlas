from __future__ import annotations

import argparse
from pathlib import Path

from .runtime import calculate_with_retry, write_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write a deterministic counter state")
    parser.add_argument("value", type=int)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = calculate_with_retry(lambda: args.value + 1, max_attempts=2)
    write_state(args.state, {"input": args.value, "result": result}, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
