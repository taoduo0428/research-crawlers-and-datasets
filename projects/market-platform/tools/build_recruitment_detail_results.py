"""Build four-column recruitment results using verified detail-page JD data.

The source exports are never modified. Detail enrichments are optional JSONL
files keyed by ``record_id``; missing or failed enrichments remain blank rather
than falling back to list-page tags.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE


SITE_NAMES = ("BOSS", "前程无忧", "猎聘")
FIELDS = ("职位名字", "分类", "岗位要求", "薪资情况")


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} 第 {line_number} 行不是合法 JSON") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path} 第 {line_number} 行不是对象")
            yield value


def load_enrichments(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    output: dict[str, dict[str, Any]] = {}
    for item in read_jsonl(path):
        key = _first_nonempty(item.get("record_id"), item.get("job_id"), item.get("detail_url"))
        if key:
            output[key] = item
    return output


def _salary(row: dict[str, Any]) -> str:
    return _first_nonempty(row.get("budget_raw"), row.get("price_raw_joined"), row.get("salary"))


def _detail_text(row: dict[str, Any], enrichment: dict[str, Any] | None) -> str:
    if enrichment:
        # Only accept explicit detail-page fields from the enrichment layer.
        detail = _first_nonempty(
            enrichment.get("description_raw"),
            enrichment.get("jd"),
            enrichment.get("detail_description"),
        )
        if detail:
            return detail
    # 51job already provides a verified description in the list API response.
    if _text(row.get("site_name")).lower() in {"51job", "前程无忧"}:
        return _first_nonempty(row.get("description_raw"), row.get("description"))
    return ""


def compact_row(row: dict[str, Any], enrichment: dict[str, Any] | None) -> dict[str, str]:
    return {
        "职位名字": _first_nonempty(row.get("title_raw"), row.get("title")),
        "分类": _text(row.get("category_path_raw")),
        "岗位要求": _detail_text(row, enrichment),
        "薪资情况": _salary(row),
    }


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_xlsx(path: Path, rows: list[dict[str, str]]) -> None:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("招聘数据")
    sheet.append(list(FIELDS))
    for row in rows:
        sheet.append([ILLEGAL_CHARACTERS_RE.sub("", row[field]) for field in FIELDS])
    workbook.save(path)


def atomic_write_site(rows: list[dict[str, str]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        "csv": output_dir / "岗位详情数据.csv",
        "jsonl": output_dir / "岗位详情数据.jsonl",
        "xlsx": output_dir / "岗位详情数据.xlsx",
    }
    with tempfile.TemporaryDirectory(prefix="recruitment-detail-", dir=output_dir) as temp_dir:
        temp = Path(temp_dir)
        _write_csv(temp / targets["csv"].name, rows)
        _write_jsonl(temp / targets["jsonl"].name, rows)
        _write_xlsx(temp / targets["xlsx"].name, rows)
        for target in targets.values():
            os.replace(temp / target.name, target)


def build_site(site: str, input_root: Path, output_root: Path, enrichment_root: Path) -> dict[str, Any]:
    source = input_root / site / "岗位数据.jsonl"
    if not source.is_file():
        raise FileNotFoundError(f"缺少原始数据：{source}")
    candidates = [
        enrichment_root / site / "详情补充.jsonl",
        enrichment_root / f"{site}-详情补充.jsonl",
    ]
    enrichment_path = next((path for path in candidates if path.is_file()), None)
    enrichments = load_enrichments(enrichment_path)
    rows = [compact_row(row, enrichments.get(_text(row.get("record_id")))) for row in read_jsonl(source)]
    atomic_write_site(rows, output_root / site)
    return {
        "site": site,
        "rows": len(rows),
        "detail_enrichments": len(enrichments),
        "岗位要求": sum(bool(item["岗位要求"]) for item in rows),
        "薪资情况": sum(bool(item["薪资情况"]) for item in rows),
        "enrichment": str(enrichment_path) if enrichment_path else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=Path("outputs/招聘--完整数据"))
    parser.add_argument("--enrichment-root", type=Path, default=Path(".crawler-state/recruitment-details"))
    parser.add_argument("--output-root", type=Path, default=Path(".crawler-state/recruitment-legacy/招聘详情处理结果"))
    parser.add_argument("--site", choices=SITE_NAMES, action="append", dest="sites")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    summary = [
        build_site(site, args.input_root, args.output_root, args.enrichment_root)
        for site in (args.sites or list(SITE_NAMES))
    ]
    (args.output_root / "处理统计.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for item in summary:
        print(
            f"{item['site']}: {item['rows']} 条，详情补充 {item['detail_enrichments']} 条，"
            f"岗位要求 {item['岗位要求']} 条，薪资 {item['薪资情况']} 条"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
