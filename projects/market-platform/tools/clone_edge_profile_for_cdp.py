#!/usr/bin/env python3
"""Clone the local Edge Default profile into a project-local CDP user-data dir."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = PROJECT_ROOT / ".crawler-state" / "liepin-edge-cdp-local"
DEFAULT_EDGE_ROOT = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data"
)

SKIP_DIRECTORIES = frozenset(
    {
        "browsermetrics",
        "cache",
        "code cache",
        "component_crx_cache",
        "crashpad",
        "dawncache",
        "extensions",
        "gpucache",
        "grshadercache",
        "optimizationguidepredictionmodels",
        "shadercache",
        "webstore downloads",
    }
)
SKIP_FILES = frozenset(
    {
        "devtoolsactiveport",
        "lock",
        "singletoncookie",
        "singletonlock",
        "singletonsocket",
    }
)


def ignored_names(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name.casefold() in SKIP_DIRECTORIES or name.casefold() in SKIP_FILES
    }


def clone_profile(source_root: Path, state_root: Path) -> dict[str, object]:
    source_root = source_root.resolve()
    state_root = state_root.resolve()
    expected_state_root = STATE_ROOT.resolve()
    if state_root != expected_state_root:
        raise ValueError(f"destination must be {expected_state_root}")
    source_profile = source_root / "Default"
    local_state = source_root / "Local State"
    if not source_profile.is_dir() or not local_state.is_file():
        raise FileNotFoundError("Edge Default profile or Local State is missing")

    state_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = state_root / f"profile-{stamp}"
    destination.mkdir()
    shutil.copy2(local_state, destination / "Local State")
    shutil.copytree(
        source_profile,
        destination / "Default",
        ignore=ignored_names,
        copy_function=shutil.copy2,
    )

    files = [path for path in destination.rglob("*") if path.is_file()]
    payload = {
        "profile": str(destination),
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    pointer = state_root / "latest-profile.json"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=state_root,
        prefix=".latest-profile.",
        suffix=".tmp",
    ) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, pointer)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_EDGE_ROOT)
    parser.add_argument("--state-root", type=Path, default=STATE_ROOT)
    args = parser.parse_args()
    print(json.dumps(clone_profile(args.source_root, args.state_root), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
