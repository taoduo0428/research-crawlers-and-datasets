from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "outputs/epwk-current-3m-20260820-v3/final/招标-雇佣-近三个月.jsonl"
DEFAULT_REVIEWS = ROOT / "outputs/epwk-current-3m-20260820-v3/ai_manual_review/reviews"
DEFAULT_OUTPUT = ROOT / "outputs/epwk-current-3m-20260820-v3/ai_manual_review/final"

REVIEW_REQUIRED = {
    "index",
    "record_id",
    "grade",
    "keep_high_quality",
    "price_confidence",
    "tags",
    "reason",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_no}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Expected object at {path}:{line_no}")
            rows.append(value)
    return rows


def load_reviews(review_dir: Path) -> list[dict[str, Any]]:
    files = sorted(review_dir.glob("batch_*.jsonl"))
    if not files:
        raise FileNotFoundError(f"No review files found in {review_dir}")

    rows: list[dict[str, Any]] = []
    for path in files:
        batch = load_jsonl(path)
        for item in batch:
            missing = REVIEW_REQUIRED - set(item)
            if missing:
                raise ValueError(f"Review {path} missing fields: {sorted(missing)}")
        rows.extend(batch)
    return rows


def json_cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], original_fields: list[str]) -> None:
    review_fields = [
        "manual_review_index",
        "manual_review_grade",
        "manual_review_keep_high_quality",
        "manual_review_price_confidence",
        "manual_review_tags",
        "manual_review_reason",
    ]
    fieldnames = original_fields + [name for name in review_fields if name not in original_fields]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = {key: json_cell(row.get(key)) for key in original_fields}
            review = row["manual_review"]
            flat.update(
                {
                    "manual_review_index": review["index"],
                    "manual_review_grade": review["grade"],
                    "manual_review_keep_high_quality": review["keep_high_quality"],
                    "manual_review_price_confidence": review["price_confidence"],
                    "manual_review_tags": json.dumps(review["tags"], ensure_ascii=False),
                    "manual_review_reason": review["reason"],
                }
            )
            writer.writerow(flat)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply EPWK manual AI reviews to the source records.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--reviews-dir", type=Path, default=DEFAULT_REVIEWS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    input_path = args.input if args.input.is_absolute() else ROOT / args.input
    review_dir = args.reviews_dir if args.reviews_dir.is_absolute() else ROOT / args.reviews_dir
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    source_rows = load_jsonl(input_path)
    reviews = load_reviews(review_dir)

    if len(reviews) != len(source_rows):
        raise ValueError(f"Count mismatch: source={len(source_rows)} reviews={len(reviews)}")

    indices = [int(item["index"]) for item in reviews]
    expected_indices = list(range(1, len(source_rows) + 1))
    if sorted(indices) != expected_indices:
        missing = sorted(set(expected_indices) - set(indices))
        duplicated = [key for key, value in Counter(indices).items() if value > 1]
        raise ValueError(f"Review index mismatch: missing={missing[:20]} duplicates={duplicated[:20]}")

    review_by_id: dict[str, dict[str, Any]] = {}
    for review in reviews:
        record_id = str(review["record_id"])
        if record_id in review_by_id:
            raise ValueError(f"Duplicate review record_id: {record_id}")
        review_by_id[record_id] = review

    source_ids = [str(row.get("record_id") or "") for row in source_rows]
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("Source contains duplicate record_id values")
    missing_reviews = [record_id for record_id in source_ids if record_id not in review_by_id]
    extra_reviews = sorted(set(review_by_id) - set(source_ids))
    if missing_reviews or extra_reviews:
        raise ValueError(
            f"record_id mismatch: missing_reviews={missing_reviews[:20]} extra_reviews={extra_reviews[:20]}"
        )

    original_fields: list[str] = []
    seen_fields: set[str] = set()
    for row in source_rows:
        for key in row:
            if key not in seen_fields and key != "manual_review":
                seen_fields.add(key)
                original_fields.append(key)

    reviewed_rows: list[dict[str, Any]] = []
    for expected_index, row in enumerate(source_rows, 1):
        record_id = str(row["record_id"])
        review = dict(review_by_id[record_id])
        if int(review["index"]) != expected_index:
            raise ValueError(
                f"Ordering mismatch for {record_id}: source index={expected_index} review index={review['index']}"
            )
        merged = dict(row)
        merged["manual_review"] = review
        reviewed_rows.append(merged)

    high_quality_rows = [row for row in reviewed_rows if row["manual_review"]["keep_high_quality"] is True]

    write_jsonl(output_dir / "全部-附人工审核.jsonl", reviewed_rows)
    write_jsonl(output_dir / "高质量-人工审核保留.jsonl", high_quality_rows)
    write_csv(output_dir / "全部-附人工审核.csv", reviewed_rows, original_fields)
    write_csv(output_dir / "高质量-人工审核保留.csv", high_quality_rows, original_fields)

    grade_counts = Counter(row["manual_review"]["grade"] for row in reviewed_rows)
    price_counts = Counter(row["manual_review"]["price_confidence"] for row in reviewed_rows)
    kept_price_counts = Counter(row["manual_review"]["price_confidence"] for row in high_quality_rows)
    tag_counts = Counter(tag for row in reviewed_rows for tag in row["manual_review"]["tags"])
    kept_grade_counts = Counter(row["manual_review"]["grade"] for row in high_quality_rows)

    summary = {
        "source": str(input_path),
        "reviews_dir": str(review_dir),
        "source_record_count": len(source_rows),
        "review_record_count": len(reviews),
        "review_batch_count": len(list(review_dir.glob("batch_*.jsonl"))),
        "index_coverage": [1, len(source_rows)],
        "unique_record_ids": len(set(source_ids)),
        "grade_counts": dict(sorted(grade_counts.items())),
        "keep_high_quality_count": len(high_quality_rows),
        "excluded_count": len(reviewed_rows) - len(high_quality_rows),
        "kept_grade_counts": dict(sorted(kept_grade_counts.items())),
        "price_confidence_counts": dict(price_counts),
        "kept_price_confidence_counts": dict(kept_price_counts),
        "tag_counts": dict(tag_counts.most_common()),
        "outputs": {
            "all_jsonl": str(output_dir / "全部-附人工审核.jsonl"),
            "all_csv": str(output_dir / "全部-附人工审核.csv"),
            "high_quality_jsonl": str(output_dir / "高质量-人工审核保留.jsonl"),
            "high_quality_csv": str(output_dir / "高质量-人工审核保留.csv"),
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(summary, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
