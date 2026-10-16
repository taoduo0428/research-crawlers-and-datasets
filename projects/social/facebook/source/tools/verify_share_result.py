"""Validate an existing capture result or completed Reaction phase; never launch a collector."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from facebook_capture_api import ApiError, build_api_result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument('--reaction-phase', action='store_true', help='Validate the completed Reaction phase before combined Share navigation.')
    parser.add_argument('--reaction-scroll-limit', type=int, default=None)
    args = parser.parse_args()
    try:
        request = {"collect": ["reactions" if args.reaction_phase else "shares"]}
        if args.reaction_phase and args.reaction_scroll_limit is not None:
            if args.reaction_scroll_limit <= 0:
                raise ApiError('Reaction phase bounded limit must be positive.')
            request.update(reaction_scroll_limit=args.reaction_scroll_limit, reaction_scroll_limit_explicit=True)
        result = build_api_result(args.run_dir, request)
    except ApiError as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=True))
        return 2
    if args.reaction_phase:
        print(json.dumps({"status": "completed", "phase": "reactions", "completion_scope": result["completion_scope"],
                          "reactions": result["completeness"]["reactions"]}, ensure_ascii=True))
        return 0
    print(json.dumps({"status": "completed", "completion_scope": result["completion_scope"],
                      "shares": result["completeness"]["shares"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
