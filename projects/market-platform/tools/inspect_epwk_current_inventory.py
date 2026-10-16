from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from drv_crawler.core import ProjectConfig, ResilientHttpClient
from drv_crawler.models import SiteResult
from drv_crawler.sites import EpwkAdapter

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "outputs/外包--完整数据/一品威客/epwk/records.jsonl"
START = date(2026, 5, 20)
NEW_START = date(2026, 7, 22)
END = date(2026, 8, 20)


def old_snapshot() -> tuple[dict[str, date], int, int]:
    values: dict[str, date] = {}
    retained = 0
    total = 0
    cutoff = date(2026, 5, 20)
    with OLD.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            total += 1
            payload = json.loads(line)
            url = str(payload.get("detail_url") or "").strip()
            canonical_url = url.rstrip("/") + "/" if url else ""
            raw = str(payload.get("published_at_raw") or "").strip().replace(".", "-")
            try:
                published = date.fromisoformat(raw[:10])
            except ValueError:
                continue
            if canonical_url:
                values[canonical_url] = published
            if cutoff <= published <= END:
                retained += 1
    return values, total, retained


def main() -> int:
    config = ProjectConfig(ROOT / "crawler.config.yaml")
    site = config.site("epwk")
    state = ROOT / str(site.get("storage_state") or ".crawler-state/epwk-storage-state.json")
    result = SiteResult(site_name="epwk", requested_limit=1)
    known, old_total, old_retained = old_snapshot()
    with ResilientHttpClient(config, site_name="epwk") as client:
        if state.exists():
            client.load_storage_state(state, allowed_domains={"epwk.com", "task.epwk.com"})
        adapter = EpwkAdapter(config, client, lambda *_: None)
        inventory = adapter._load_publication_sitemap_inventory(result)
    candidates = []
    overlap_exact = 0
    overlap_mismatch: list[tuple[str, str, str]] = []
    for item in inventory.entries:
        try:
            d = date.fromisoformat(item.published_at)
        except ValueError:
            continue
        if START <= d <= END:
            url = item.detail_url.rstrip("/") + "/"
            old_date = known.get(url)
            if old_date is not None:
                if old_date == d:
                    overlap_exact += 1
                else:
                    overlap_mismatch.append((url, old_date.isoformat(), d.isoformat()))
            candidates.append((d.isoformat(), item.task_key, url, old_date is not None, item.sitemap_url))
    candidates.sort()
    payload = {
        "inventory_complete": inventory.complete,
        "configured_files": inventory.configured_files,
        "loaded_files": inventory.loaded_files,
        "dated_tasks_total": len(inventory.entries),
        "window": [START.isoformat(), END.isoformat()],
        "old_snapshot_total": old_total,
        "old_snapshot_retained_2026_05_20_through_2026_08_20": old_retained,
        "window_candidates": len(candidates),
        "already_in_old_snapshot": sum(x[3] for x in candidates),
        "new_vs_old_snapshot": sum(not x[3] for x in candidates),
        "overlap_exact_date_matches": overlap_exact,
        "overlap_date_mismatches": len(overlap_mismatch),
        "overlap_mismatch_examples": overlap_mismatch[:10],
        "first": candidates[:5],
        "last": candidates[-5:],
        "runtime_notes": result.runtime_notes,
        "failures": [f.to_dict() for f in result.failures],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
