#!/usr/bin/env python3
"""检查并整理发布的 Amazon / Jiimore / SellerSprite 历史数据。

默认只读，不访问任何网站、不读取 Cookie，也不修改 data/ 中的原始文件。
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator

PROJECT = Path(__file__).resolve().parents[1]
DATA = PROJECT / "data"
ASIN_PATTERN = re.compile(r"^[A-Z0-9]{10}$")
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

TABLES = {
    "products": ("amazon/products.csv", ("ASIN",)),
    "tags": ("amazon/tags.csv", ("ASIN", "标签")),
    "asin": ("jiimore/asin.csv", ("ASIN",)),
    "weekly_clicks": ("jiimore/weekly_clicks.csv", ("ASIN", "日期")),
    "monthly_sales": ("sellersprite/monthly_sales.csv", ("ASIN", "月份")),
    "monthly_reviews": ("sellersprite/monthly_reviews.csv", ("ASIN", "月份")),
    "keepa_history": ("sellersprite/keepa_history.csv", ("ASIN", "日期")),
}
SALES = ("月销量", "月销售额", "平均单价")
REVIEWS = ("评分数增长值", "留评率")
MERGED_COLUMNS = ("ASIN", "月份") + SALES + REVIEWS + ("销量数据状态", "评论数据状态")
CONFLICT_COLUMNS = ("数据来源", "ASIN", "月份", "原始字段JSON")


def iterate(data_dir: Path, table: str) -> Iterator[tuple[dict[str, str], list[str]]]:
    relative, fields = TABLES[table]
    with (data_dir / relative).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        if len(columns) != len(set(columns)) or not set(fields).issubset(columns):
            raise ValueError(f"{relative}: 主键字段缺失或表头重复")
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"{relative}: CSV 列数与表头不一致")
            yield row, columns


def normalized_asin(value: str) -> str:
    return (value or "").strip().upper()


def key_for(row: dict, fields: tuple[str, ...]) -> tuple[str, ...]:
    values = [normalized_asin(row["ASIN"])]
    values.extend((row.get(column) or "").strip() for column in fields[1:])
    return tuple(values)


def valid_key(key: tuple[str, ...], fields: tuple[str, ...]) -> bool:
    if not ASIN_PATTERN.fullmatch(key[0]):
        return False
    for field, value in zip(fields[1:], key[1:]):
        if not value or (field == "月份" and not MONTH_PATTERN.fullmatch(value)):
            return False
    return True


def scan(data_dir: Path, name: str) -> tuple[dict, set[str]]:
    relative, fields = TABLES[name]
    rows = 0
    empty = Counter()
    asins: set[str] = set()
    first_values: dict[tuple[str, ...], tuple[str, ...]] = {}
    duplicate_rows = 0
    exact_duplicate_rows = 0
    conflict_keys: set[tuple[str, ...]] = set()
    rejected_key_rows = 0
    time_values: set[str] = set()
    columns: list[str] = []
    for row, cols in iterate(data_dir, name):
        columns = cols
        rows += 1
        for column in columns:
            if not row[column].strip():
                empty[column] += 1
        asin = normalized_asin(row["ASIN"])
        if ASIN_PATTERN.fullmatch(asin):
            asins.add(asin)
        key = key_for(row, fields)
        if not valid_key(key, fields):
            rejected_key_rows += 1
            continue
        period = row.get("月份") or row.get("日期") or ""
        if period.strip():
            time_values.add(period.strip())
        payload = tuple(row[column] for column in columns if column not in fields)
        if key in first_values:
            duplicate_rows += 1
            if first_values[key] == payload:
                exact_duplicate_rows += 1
            else:
                conflict_keys.add(key)
        else:
            first_values[key] = payload
    return {
        "file": relative,
        "rows": rows,
        "key_fields": list(fields),
        "valid_unique_asins": len(asins),
        "usable_unique_keys": len(first_values),
        "rejected_key_rows": rejected_key_rows,
        "repeated_key_rows": duplicate_rows,
        "exact_repeated_rows": exact_duplicate_rows,
        "conflicting_key_groups": len(conflict_keys),
        "empty_cells_by_column": dict(empty),
        "first_period": min(time_values) if time_values else None,
        "last_period": max(time_values) if time_values else None,
    }, asins


def audit(data_dir: Path = DATA) -> dict:
    tables = {}
    asins = {}
    for name in TABLES:
        tables[name], asins[name] = scan(data_dir, name)
    four = ("products", "weekly_clicks", "monthly_sales", "monthly_reviews")
    common = set.intersection(*(asins[k] for k in four))
    return {
        "data_root": str(data_dir),
        "network_used": False,
        "tables": tables,
        "coverage": {
            "four_main_tables_common_asins": len(common),
            "products_missing_monthly_sales": len(asins["products"] - asins["monthly_sales"]),
            "product_asins_missing_weekly_clicks": len(asins["products"] - asins["weekly_clicks"]),
            "tag_asins_not_in_products": len(asins["tags"] - asins["products"]),
            "keepa_asin_coverage": len(asins["keepa_history"]),
        },
        "notes": [
            "相同 ASIN + 月份的冲突必须人工确认，不能按行号拼表或随机去重。",
            "无值不等于 0；不同平台的时间粒度不同。",
            "所有结果是历史数据，不能当作实时销量或成交价格。",
        ],
    }


def assert_destination(path: Path, data_dir: Path, overwrite: bool = False) -> None:
    target = path.resolve()
    if target.is_relative_to(PROJECT.resolve()) or target.is_relative_to(data_dir.resolve()):
        raise ValueError("禁止将分析结果写进项目仓库或正式 data/ 目录，请保存到仓库外")
    if path.exists() and not overwrite:
        raise FileExistsError(f"文件已经存在，不会自动覆盖：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, columns: tuple[str, ...] | list[str],
              rows: Iterator[dict] | list[dict], data_dir: Path, *, overwrite=False) -> int:
    assert_destination(path, data_dir, overwrite)
    created = None
    count = 0
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8-sig", newline="", dir=path.parent,
            suffix=".partial", delete=False,
        ) as handle:
            created = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({col: row.get(col, "") for col in columns})
                count += 1
        os.replace(created, path)
    finally:
        if created and created.exists():
            created.unlink()
    return count


def monthly_index(data_dir: Path, name: str):
    fields = TABLES[name][1]
    assert fields == ("ASIN", "月份")
    accepted = {}
    conflicts = set()
    conflict_records = defaultdict(list)
    rejected = 0
    for row, _ in iterate(data_dir, name):
        key = key_for(row, fields)
        if not valid_key(key, fields):
            rejected += 1
            continue
        selected = {k: row[k] for k in row if k not in fields}
        if key not in accepted:
            accepted[key] = selected
        elif accepted[key] != selected:
            conflicts.add(key)
            if not conflict_records[key]:
                conflict_records[key].append(accepted[key])
            if selected not in conflict_records[key]:
                conflict_records[key].append(selected)
    return accepted, conflicts, conflict_records, rejected


def merge_monthly(data_dir: Path, target: Path, conflicts_target: Path,
                  *, overwrite=False) -> dict:
    if target.resolve() == conflicts_target.resolve():
        raise ValueError("合并表和冲突表不能用同一条路径")
    # Check both outputs before writing either one.
    assert_destination(target, data_dir, overwrite)
    assert_destination(conflicts_target, data_dir, overwrite)
    sales, bad_sales, sale_rows, bad_sales_rows = monthly_index(data_dir, "monthly_sales")
    reviews, bad_reviews, review_rows, bad_review_rows = monthly_index(data_dir, "monthly_reviews")
    all_keys = sorted(set(sales) | set(reviews))
    def merged():
        for asin, month in all_keys:
            s = sales.get((asin, month))
            r = reviews.get((asin, month))
            sale_status = "冲突待核对" if (asin, month) in bad_sales else "有记录" if s else "无该月份"
            review_status = "冲突待核对" if (asin, month) in bad_reviews else "有记录" if r else "无该月份"
            record = {"ASIN": asin, "月份": month,
                      "销量数据状态": sale_status, "评论数据状态": review_status}
            if s and (asin, month) not in bad_sales:
                record.update(s)
            if r and (asin, month) not in bad_reviews:
                record.update(r)
            yield record
    def conflicts():
        for source, groups in (("monthly_sales", sale_rows), ("monthly_reviews", review_rows)):
            for key in sorted(groups):
                for raw in groups[key]:
                    yield {"数据来源": source, "ASIN": key[0], "月份": key[1],
                           "原始字段JSON": json.dumps(raw, ensure_ascii=False, sort_keys=True)}
    n = write_csv(target, MERGED_COLUMNS, merged(), data_dir, overwrite=overwrite)
    conflict_n = write_csv(conflicts_target, CONFLICT_COLUMNS, conflicts(), data_dir,
                           overwrite=overwrite)
    return {"merged_rows": n, "sales_conflict_keys": len(bad_sales),
            "review_conflict_keys": len(bad_reviews), "conflict_source_rows": conflict_n,
            "sales_rows_with_invalid_keys": bad_sales_rows,
            "review_rows_with_invalid_keys": bad_review_rows,
            "merged_output": str(target), "conflict_details_output": str(conflicts_target),
            "policy": "相同键完全重复时保留一条；不同值的键保留在冲突文件，合并表的对应数值留空"}


def export_asin(data_dir: Path, asin: str, folder: Path, *, overwrite=False) -> dict:
    asin = normalized_asin(asin)
    if not ASIN_PATTERN.fullmatch(asin):
        raise ValueError("ASIN 应该是 10 位大写字母或数字")
    if (folder.resolve().is_relative_to(PROJECT.resolve()) or
            folder.resolve().is_relative_to(data_dir.resolve())):
        raise ValueError("输出必须保存到仓库外，不能写进正式 data/ 目录")
    if not any(normalized_asin(row["ASIN"]) == asin
               for row, _ in iterate(data_dir, "asin")):
        raise ValueError(f"ASIN 不在发布的商品清单中：{asin}")
    # First check every destination so an existing file does not leave a
    # half-exported directory with some older and some newer data.
    for name in TABLES:
        assert_destination(folder / (name + ".csv"), data_dir, overwrite)
    outputs = {}
    for name in TABLES:
        path = folder / (name + ".csv")
        columns = []
        matched = []
        for row, header in iterate(data_dir, name):
            columns = header
            if normalized_asin(row["ASIN"]) == asin:
                matched.append(row)
        count = write_csv(path, columns, matched, data_dir, overwrite=overwrite)
        outputs[name] = {"rows": count, "path": str(path)}
    return {"asin": asin, "network_used": False, "files": outputs}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="整理 Amazon / Jiimore / SellerSprite 历史数据；不会访问网站"
    )
    parser.add_argument("--data-root", type=Path, default=DATA)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit", help="查看七张表的行数、缺失、覆盖范围和重复键")
    monthly = sub.add_parser("merge-monthly", help="按 ASIN + 月份合并销量与评论，保留冲突")
    monthly.add_argument("--output", type=Path, required=True)
    monthly.add_argument("--conflicts", type=Path, help="冲突明细，默认是输出名加 _conflicts.csv")
    monthly.add_argument("--overwrite", action="store_true")
    sample = sub.add_parser("export-asin", help="按 ASIN 从七张表分别导出，不把周数据混到月数据")
    sample.add_argument("--asin", required=True)
    sample.add_argument("--outdir", type=Path, required=True)
    sample.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.command == "audit":
        report = audit(args.data_root)
    elif args.command == "merge-monthly":
        conflicts_path = args.conflicts or args.output.with_name(args.output.stem + "_conflicts.csv")
        report = merge_monthly(args.data_root, args.output, conflicts_path,
                               overwrite=args.overwrite)
    else:
        report = export_asin(args.data_root, args.asin, args.outdir,
                             overwrite=args.overwrite)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
