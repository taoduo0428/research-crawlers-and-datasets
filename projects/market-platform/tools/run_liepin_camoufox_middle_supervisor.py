#!/usr/bin/env python3
"""Disabled compatibility entry point for the removed midpoint strategy."""

from __future__ import annotations

def main() -> int:
    print(
        "Midpoint mode has been removed. Use "
        "tools/run_liepin_camoufox_reverse_2_supervisor.py for the second tail worker.",
        flush=True,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
