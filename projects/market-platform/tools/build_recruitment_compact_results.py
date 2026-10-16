"""Build four-column recruitment datasets without changing source exports."""

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
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("metadata")
    return value if isinstance(value, dict) else {}


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _requirements(row: dict[str, Any]) -> str:
    """Use detail JD first, then only source-provided structured requirements."""
    description = _first_nonempty(
        row.get("description_raw"),
        row.get("description"),
        row.get("job_description"),
    )
    if description:
        return description

    meta = _metadata(row)
    parts: list[str] = []
    labels = _first_nonempty(row.get("job_labels"), row.get("tags"))
    if labels:
        parts.append(labels)
    for label, key in (("经验", "experience"), ("学历", "education")):
        value = _text(meta.get(key))
        if value and not any(value in part for part in parts):
            parts.append(f"{label}：{value}")
    skills = _first_nonempty(row.get("skills"), meta.get("skills"))
    if skills and not any(skills in part for part in parts):
        parts.append(f"技能：{skills}")
    if parts:
        return "；".join(parts)

    source_labels = meta.get("labels")
    if isinstance(source_labels, list):
        fallback = [_text(item) for item in source_labels[:2] if _text(item)]
        return "；".join(fallback)
    return ""


def _salary(row: dict[str, Any]) -> str:
    evidence = row.get("price_evidence")
    evidence_text = ""
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict):
                evidence_text = _first_nonempty(item.get("raw_text"))
                if evidence_text:
                    break
    return _first_nonempty(
        row.get("budget_raw"),
        row.get("salary"),
        row.get("price_raw_joined"),
        evidence_text,
    )


def compact_row(row: dict[str, Any]) -> dict[str, str]:
    return {
        "职位名字": _first_nonempty(row.get("title_raw"), row.get("title")),
        "分类": _text(row.get("category_path_raw")),
        "岗位要求": _requirements(row),
        "薪资情况": _salary(row),
    }


def read_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} 第 {line_number} 行不是合法 JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path} 第 {line_number} 行不是对象")
            yield row


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


def _atomic_write_site(rows: list[dict[str, str]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="compact-", dir=output_dir) as temp_dir:
        temp = Path(temp_dir)
        targets = {
            "csv": output_dir / "岗位精简数据.csv",
            "jsonl": output_dir / "岗位精简数据.jsonl",
            "xlsx": output_dir / "岗位精简数据.xlsx",
        }
        _write_csv(temp / targets["csv"].name, rows)
        _write_jsonl(temp / targets["jsonl"].name, rows)
        _write_xlsx(temp / targets["xlsx"].name, rows)
        for key, target in targets.items():
            os.replace(temp / target.name, target)


def build_site(site: str, input_root: Path, output_root: Path) -> dict[str, int | str]:
    source = input_root / site / "岗位数据.jsonl"
    if not source.is_file():
        raise FileNotFoundError(f"缺少 {site} 原始数据：{source}")
    rows = [compact_row(row) for row in read_rows(source)]
    _atomic_write_site(rows, output_root / site)
    return {
        "site": site,
        "source": str(source),
        "rows": len(rows),
        "description_or_requirements": sum(bool(row["岗位要求"]) for row in rows),
        "salary": sum(bool(row["薪资情况"]) for row in rows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=Path("outputs/招聘--完整数据"))
    parser.add_argument("--output-root", type=Path, default=Path(".crawler-state/recruitment-legacy/招聘处理结果"))
    parser.add_argument("--site", choices=SITE_NAMES, action="append", dest="sites")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sites = args.sites or list(SITE_NAMES)
    summary = [build_site(site, args.input_root, args.output_root) for site in sites]
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "处理统计.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for item in summary:
        print(
            f"{item['site']}: {item['rows']} 条，"
            f"岗位要求/描述 {item['description_or_requirements']} 条，"
            f"薪资 {item['salary']} 条"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
