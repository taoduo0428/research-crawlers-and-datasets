"""Build complete and cleaned recruitment datasets for 51job, Liepin, and BOSS.

The original exports are read-only inputs.  Liepin detail-page enrichments are
joined back to the original metadata by ``record_id`` (falling back to the
detail URL), while 51job and BOSS already carry their JD in ``description_raw``.  Each
site is exported as JSONL, CSV and XLSX, then a second export keeps only rows
with a usable JD and the six fields requested for downstream modelling.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from itertools import zip_longest
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell


SITES = ("前程无忧", "猎聘", "BOSS")
FULL_FIELDS = (
    "record_id",
    "site_name",
    "content_category",
    "category_level_1_raw",
    "category_level_2_raw",
    "category_level_3_raw",
    "category_path_raw",
    "category_source",
    "category_source_url",
    "category_identity_verified",
    "title_raw",
    "detail_title_raw",
    "price_raw_joined",
    "status_raw",
    "job_type_raw",
    "budget_raw",
    "work_period_raw",
    "publisher_name_raw",
    "location_raw",
    "published_at_raw",
    "publication_scope",
    "publication_boundary_interval",
    "publication_boundary_note",
    "description_raw",
    "list_url",
    "detail_url",
    "page_no",
    "list_position",
    "capture_method",
    "source_state",
    "price_evidence",
    "metadata",
    "captured_at",
)
CLEAN_FIELDS = ("职位", "JD", "薪资", "一级分类", "二级分类", "三级分类")
EMPTY_JD_VALUES = {
    "",
    "-",
    "--",
    "n/a",
    "na",
    "none",
    "null",
    "无",
    "暂无",
    "暂无职位描述",
    "暂无岗位描述",
}
HTML_BREAK_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]{0,200}>")
SPACE_RE = re.compile(r"[^\S\r\n]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")
ZERO_WIDTH_RE = re.compile("[\u200b-\u200f\u2060\ufeff]")


XML_ILLEGAL_CHARACTERS_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)


class DatasetBuildError(RuntimeError):
    """Raised when a source or generated dataset fails validation."""


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetBuildError(f"{path} 第 {line_number} 行不是合法 JSON") from exc
            if not isinstance(value, dict):
                raise DatasetBuildError(f"{path} 第 {line_number} 行不是 JSON 对象")
            yield value


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _record_key(row: Mapping[str, Any]) -> str:
    return _text(row.get("record_id") or row.get("detail_url"))


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def clean_text(value: Any, *, multiline: bool = False) -> str:
    text = unicodedata.normalize("NFC", _text(value))
    if not text:
        return ""
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = ZERO_WIDTH_RE.sub("", text)
    text = HTML_BREAK_RE.sub("\n", text)
    text = HTML_TAG_RE.sub("", text)
    text = XML_ILLEGAL_CHARACTERS_RE.sub("", text)
    if not multiline:
        return SPACE_RE.sub(" ", text.replace("\n", " ")).strip()
    lines = [SPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    return BLANK_LINES_RE.sub("\n\n", text)


def _usable_jd(value: Any) -> str:
    text = clean_text(value, multiline=True)
    return "" if text.casefold() in EMPTY_JD_VALUES else text


def _salary(row: Mapping[str, Any]) -> str:
    text = clean_text(
        _first_text(row.get("budget_raw"), row.get("price_raw_joined"), row.get("salary"))
    )
    return re.sub(r"(?<=\d)k\b", "K", text, flags=re.IGNORECASE)


def _json_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _xlsx_cell(value: Any) -> tuple[Any, bool]:
    value = _json_cell(value)
    if not isinstance(value, str):
        return value, False
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = XML_ILLEGAL_CHARACTERS_RE.sub("", value)
    return (value[:32767], len(value) > 32767)


def write_complete_xlsx(jsonl_path: Path, xlsx_path: Path, fields: list[str]) -> dict[str, int]:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("完整元数据")
    sheet.append(fields)
    rows = 0
    truncated_cells = 0
    for row in read_jsonl(jsonl_path):
        rows += 1
        xlsx_row: list[Any] = []
        for field in fields:
            value, truncated = _xlsx_text_safe_cell(sheet, row.get(field))
            truncated_cells += int(truncated)
            xlsx_row.append(value)
        sheet.append(xlsx_row)
    workbook.save(xlsx_path)
    return {"tabular_rows": rows, "xlsx_truncated_cells": truncated_cells}


def _xlsx_text_safe_cell(sheet: Any, value: Any) -> tuple[Any, bool]:
    value, truncated = _xlsx_cell(value)
    if isinstance(value, str):
        cell = WriteOnlyCell(sheet, value=value)
        cell.data_type = "s"
        return cell, truncated
    return value, truncated


def _ordered_fields(discovered: Iterable[str]) -> list[str]:
    seen = set(discovered)
    return [field for field in FULL_FIELDS if field in seen] + sorted(seen - set(FULL_FIELDS))


def _create_detail_index(detail_path: Path, db_path: Path) -> dict[str, int]:
    counts = {"detail_rows": 0, "detail_keys": 0, "detail_with_jd": 0}
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE details (
                record_key TEXT PRIMARY KEY,
                description_raw TEXT NOT NULL,
                has_usable_jd INTEGER NOT NULL,
                detail_title_raw TEXT NOT NULL,
                detail_fetch_status TEXT NOT NULL,
                detail_capture_method TEXT NOT NULL
            )
            """
        )
        for row in read_jsonl(detail_path):
            counts["detail_rows"] += 1
            key = _record_key(row)
            if not key:
                continue
            raw_jd = row.get("description_raw")
            jd = raw_jd if isinstance(raw_jd, str) else _text(raw_jd)
            has_usable_jd = int(bool(_usable_jd(jd)))
            if not has_usable_jd:
                jd = ""
            connection.execute(
                """
                INSERT INTO details VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_key) DO UPDATE SET
                    description_raw = CASE
                        WHEN excluded.has_usable_jd = 1 THEN excluded.description_raw
                        ELSE details.description_raw
                    END,
                    has_usable_jd = MAX(details.has_usable_jd, excluded.has_usable_jd),
                    detail_title_raw = CASE
                        WHEN excluded.detail_title_raw <> '' THEN excluded.detail_title_raw
                        ELSE details.detail_title_raw
                    END,
                    detail_fetch_status = CASE
                        WHEN excluded.detail_fetch_status <> '' THEN excluded.detail_fetch_status
                        ELSE details.detail_fetch_status
                    END,
                    detail_capture_method = CASE
                        WHEN excluded.detail_capture_method <> '' THEN excluded.detail_capture_method
                        ELSE details.detail_capture_method
                    END
                """,
                (
                    key,
                    jd,
                    has_usable_jd,
                    _text(row.get("detail_title_raw")),
                    _text(row.get("detail_fetch_status")),
                    _text(row.get("detail_capture_method")),
                ),
            )
        connection.commit()
        counts["detail_keys"] = connection.execute("SELECT COUNT(*) FROM details").fetchone()[0]
        counts["detail_with_jd"] = connection.execute(
            "SELECT COUNT(*) FROM details WHERE has_usable_jd = 1"
        ).fetchone()[0]
    finally:
        connection.close()
    return counts


def _merged_rows(
    site: str,
    source_path: Path,
    detail_db: Path | None,
) -> Iterator[dict[str, Any]]:
    connection = sqlite3.connect(detail_db) if detail_db else None
    try:
        for source_row in read_jsonl(source_path):
            row = dict(source_row)
            if site == "猎聘":
                key = _record_key(row)
                detail = (
                    connection.execute(
                        """
                        SELECT description_raw, detail_title_raw,
                               detail_fetch_status, detail_capture_method
                        FROM details WHERE record_key = ?
                        """,
                        (key,),
                    ).fetchone()
                    if connection is not None and key
                    else None
                )
                if detail:
                    description, detail_title, fetch_status, capture_method = detail
                    row["description_raw"] = description
                    if detail_title:
                        row["detail_title_raw"] = detail_title
                    row["detail_fetch_status"] = fetch_status
                    row["detail_capture_method"] = capture_method
                else:
                    row["description_raw"] = ""
                    row["detail_fetch_status"] = "missing"
                    row["detail_capture_method"] = ""
            yield row
    finally:
        if connection is not None:
            connection.close()


def _write_complete_jsonl(
    site: str,
    source_path: Path,
    detail_db: Path | None,
    target: Path,
) -> tuple[dict[str, int], list[str]]:
    stats = {"source_rows": 0, "rows_with_jd": 0, "rows_without_jd": 0, "duplicate_ids": 0}
    fields: set[str] = set()
    seen_ids: set[str] = set()
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for row in _merged_rows(site, source_path, detail_db):
            stats["source_rows"] += 1
            fields.update(row)
            key = _record_key(row)
            if key:
                if key in seen_ids:
                    stats["duplicate_ids"] += 1
                seen_ids.add(key)
            jd = _usable_jd(row.get("description_raw"))
            if jd:
                stats["rows_with_jd"] += 1
            else:
                stats["rows_without_jd"] += 1
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return stats, _ordered_fields(fields)


def _write_tabular_exports(
    jsonl_path: Path,
    csv_path: Path,
    xlsx_path: Path,
    fields: list[str],
) -> dict[str, int]:
    rows = 0
    with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_handle:
        writer = csv.DictWriter(csv_handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in read_jsonl(jsonl_path):
            rows += 1
            converted = {field: _json_cell(row.get(field)) for field in fields}
            writer.writerow(converted)
        csv_handle.flush()
        os.fsync(csv_handle.fileno())
    xlsx_stats = write_complete_xlsx(jsonl_path, xlsx_path, fields)
    if xlsx_stats["tabular_rows"] != rows:
        raise DatasetBuildError("完整元数据 CSV 与 XLSX 写入行数不一致")
    return xlsx_stats


def _clean_row(row: Mapping[str, Any]) -> dict[str, str] | None:
    jd = _usable_jd(row.get("description_raw"))
    if not jd:
        return None
    return {
        "职位": clean_text(_first_text(row.get("title_raw"), row.get("detail_title_raw"))),
        "JD": jd,
        "薪资": _salary(row),
        "一级分类": clean_text(row.get("category_level_1_raw")),
        "二级分类": clean_text(row.get("category_level_2_raw")),
        "三级分类": clean_text(row.get("category_level_3_raw")),
    }


def _write_clean_exports(
    complete_jsonl: Path,
    jsonl_path: Path,
    csv_path: Path,
    xlsx_path: Path,
) -> dict[str, int]:
    stats = {
        "clean_rows": 0,
        "excluded_without_jd": 0,
        "missing_title": 0,
        "missing_salary": 0,
        "missing_level_1": 0,
        "missing_level_2": 0,
        "missing_level_3": 0,
        "xlsx_truncated_cells": 0,
    }
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("岗位数据")
    sheet.append(CLEAN_FIELDS)
    with (
        jsonl_path.open("w", encoding="utf-8", newline="\n") as json_handle,
        csv_path.open("w", encoding="utf-8-sig", newline="") as csv_handle,
    ):
        writer = csv.DictWriter(csv_handle, fieldnames=CLEAN_FIELDS)
        writer.writeheader()
        for source_row in read_jsonl(complete_jsonl):
            row = _clean_row(source_row)
            if row is None:
                stats["excluded_without_jd"] += 1
                continue
            stats["clean_rows"] += 1
            stats["missing_title"] += int(not row["职位"])
            stats["missing_salary"] += int(not row["薪资"])
            stats["missing_level_1"] += int(not row["一级分类"])
            stats["missing_level_2"] += int(not row["二级分类"])
            stats["missing_level_3"] += int(not row["三级分类"])
            json_handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            writer.writerow(row)
            xlsx_row: list[Any] = []
            for field in CLEAN_FIELDS:
                value, truncated = _xlsx_text_safe_cell(sheet, row[field])
                stats["xlsx_truncated_cells"] += int(truncated)
                xlsx_row.append(value)
            sheet.append(xlsx_row)
        json_handle.flush()
        csv_handle.flush()
        os.fsync(json_handle.fileno())
        os.fsync(csv_handle.fileno())
    workbook.save(xlsx_path)
    return stats


def _count_csv(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return max(sum(1 for _ in csv.reader(handle)) - 1, 0)


def _count_xlsx(path: Path) -> int:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook[workbook.sheetnames[0]]
        return max(sum(1 for _ in sheet.iter_rows(values_only=True)) - 1, 0)
    finally:
        workbook.close()


def _validate_three_formats(directory: Path, expected_rows: int) -> None:
    json_rows = sum(1 for _ in read_jsonl(directory / "岗位数据.jsonl"))
    csv_rows = _count_csv(directory / "岗位数据.csv")
    xlsx_rows = _count_xlsx(directory / "岗位数据.xlsx")
    actual = {"jsonl": json_rows, "csv": csv_rows, "xlsx": xlsx_rows}
    if set(actual.values()) != {expected_rows}:
        raise DatasetBuildError(f"{directory} 三种格式行数不一致：{actual}")


def _validate_metadata_preservation(site: str, source_path: Path, complete_path: Path) -> int:
    allowed_changes = (
        {"description_raw", "detail_title_raw", "detail_fetch_status", "detail_capture_method"}
        if site == "猎聘"
        else set()
    )
    sentinel = object()
    rows = 0
    for line_number, pair in enumerate(
        zip_longest(read_jsonl(source_path), read_jsonl(complete_path), fillvalue=sentinel),
        1,
    ):
        source_row, complete_row = pair
        if source_row is sentinel or complete_row is sentinel:
            raise DatasetBuildError(f"{site} 完整元数据与源文件行数不一致")
        source_key = _record_key(source_row)
        complete_key = _record_key(complete_row)
        if source_key != complete_key:
            raise DatasetBuildError(
                f"{site} 第 {line_number} 行 record_id 错位：{source_key!r} != {complete_key!r}"
            )
        source_evidence = {
            key: value for key, value in source_row.items() if key not in allowed_changes
        }
        complete_evidence = {
            key: value for key, value in complete_row.items() if key not in allowed_changes
        }
        if source_evidence != complete_evidence:
            raise DatasetBuildError(f"{site} 第 {line_number} 行非 JD 元数据发生变化")
        rows += 1
    return rows


def build_site(
    site: str,
    input_root: Path,
    enrichment_root: Path,
    complete_root: Path,
    clean_root: Path,
) -> dict[str, Any]:
    source_path = input_root / site / "岗位数据.jsonl"
    if not source_path.is_file():
        raise FileNotFoundError(f"缺少原始元数据：{source_path}")
    detail_path = enrichment_root / site / "详情补充.jsonl"
    if site == "猎聘" and not detail_path.is_file():
        raise FileNotFoundError(f"缺少猎聘详情补充：{detail_path}")

    complete_dir = complete_root / site
    clean_dir = clean_root / site
    complete_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{site}-dataset-", dir=complete_root) as temp_name:
        temp = Path(temp_name)
        target_jsonl = complete_dir / "岗位数据.jsonl"
        validation_source = source_path
        if source_path.resolve() == target_jsonl.resolve():
            validation_source = temp / "source-岗位数据.jsonl"
            try:
                os.link(source_path, validation_source)
            except OSError:
                shutil.copy2(source_path, validation_source)
        detail_db = temp / "details.sqlite3" if site == "猎聘" else None
        detail_stats: dict[str, int] = {}
        if detail_db is not None:
            detail_stats = _create_detail_index(detail_path, detail_db)

        full_jsonl = temp / "完整-岗位数据.jsonl"
        full_csv = temp / "完整-岗位数据.csv"
        full_xlsx = temp / "完整-岗位数据.xlsx"
        full_stats, fields = _write_complete_jsonl(site, validation_source, detail_db, full_jsonl)
        full_stats.update(_write_tabular_exports(full_jsonl, full_csv, full_xlsx, fields))

        clean_jsonl = temp / "清洗-岗位数据.jsonl"
        clean_csv = temp / "清洗-岗位数据.csv"
        clean_xlsx = temp / "清洗-岗位数据.xlsx"
        clean_stats = _write_clean_exports(
            full_jsonl, clean_jsonl, clean_csv, clean_xlsx
        )

        if full_stats["tabular_rows"] != full_stats["source_rows"]:
            raise DatasetBuildError(f"{site} 完整元数据写入行数不一致")
        if clean_stats["clean_rows"] + clean_stats["excluded_without_jd"] != full_stats["source_rows"]:
            raise DatasetBuildError(f"{site} 清洗筛选行数不守恒")

        for source, target in (
            (full_jsonl, complete_dir / "岗位数据.jsonl"),
            (full_csv, complete_dir / "岗位数据.csv"),
            (full_xlsx, complete_dir / "岗位数据.xlsx"),
            (clean_jsonl, clean_dir / "岗位数据.jsonl"),
            (clean_csv, clean_dir / "岗位数据.csv"),
            (clean_xlsx, clean_dir / "岗位数据.xlsx"),
        ):
            os.replace(source, target)

        preserved_rows = _validate_metadata_preservation(
            site, validation_source, target_jsonl
        )

    _validate_three_formats(complete_dir, full_stats["source_rows"])
    _validate_three_formats(clean_dir, clean_stats["clean_rows"])
    return {
        "site": site,
        "complete_metadata": full_stats,
        "detail_enrichment": detail_stats,
        "cleaned": clean_stats,
        "metadata_preservation_rows": preserved_rows,
        "complete_directory": str(complete_dir),
        "clean_directory": str(clean_dir),
    }


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=Path("outputs/招聘--完整数据"))
    parser.add_argument("--enrichment-root", type=Path, default=Path(".crawler-state/recruitment-details"))
    parser.add_argument("--complete-root", type=Path, default=Path("outputs/招聘--完整数据"))
    parser.add_argument("--clean-root", type=Path, default=Path("outputs/招聘--最终结果"))
    parser.add_argument("--site", action="append", choices=SITES, dest="sites")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.complete_root.mkdir(parents=True, exist_ok=True)
    args.clean_root.mkdir(parents=True, exist_ok=True)
    summary = [
        build_site(
            site,
            args.input_root,
            args.enrichment_root,
            args.complete_root,
            args.clean_root,
        )
        for site in (args.sites or list(SITES))
    ]
    _atomic_write_json(args.clean_root / "处理统计.json", summary)
    for item in summary:
        full = item["complete_metadata"]
        clean = item["cleaned"]
        print(
            f"{item['site']}: 完整元数据 {full['source_rows']} 条，"
            f"有 JD {full['rows_with_jd']} 条，清洗结果 {clean['clean_rows']} 条"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
