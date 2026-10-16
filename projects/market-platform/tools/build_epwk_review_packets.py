from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "outputs/epwk-current-3m-20260820-v3/final/招标-雇佣-近三个月.jsonl"
DEFAULT_OUTPUT = ROOT / "outputs/epwk-current-3m-20260820-v3/ai_manual_review/packets"


def fmt_bool(value):
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "未知"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args()

    input_path = args.input if args.input.is_absolute() else ROOT / args.input
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    with input_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))

    for start in range(0, len(rows), args.batch_size):
        chunk = rows[start:start + args.batch_size]
        batch_no = start // args.batch_size + 1
        parts = [
            f"EPWK人工审核包 {batch_no:03d}",
            f"记录范围: {start + 1}-{start + len(chunk)} / {len(rows)}",
            "说明: 本文件只整理原始字段，不做任何自动评分。",
            "",
        ]
        for offset, row in enumerate(chunk):
            idx = start + offset + 1
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            prices = []
            for item in row.get("price_evidence") or []:
                if isinstance(item, dict) and item.get("raw_text"):
                    value = str(item["raw_text"]).strip()
                    if value and value not in prices:
                        prices.append(value)
            desc = str(row.get("description_raw") or "").strip()
            parts.extend([
                f"### [{idx:04d}] {row.get('title_raw') or ''}",
                f"record_id: {row.get('record_id') or ''}",
                f"task_id: {meta.get('task_id') or ''}",
                f"交易模式: {row.get('job_type_raw') or ''}",
                f"预算/价格: {row.get('budget_raw') or '无'} | {' ; '.join(prices) if prices else '无价格证据'}",
                f"状态: {row.get('status_raw') or '未知'} | 圆满完成={fmt_bool(meta.get('is_completed'))} | 有中标者={fmt_bool(meta.get('has_winner'))}",
                f"发布时间: {row.get('published_at_raw') or meta.get('current_window_candidate_date') or '未知'}",
                f"分类: {row.get('category_path_raw') or row.get('category_level_1_raw') or '未知'}",
                f"URL: {row.get('detail_url') or ''}",
                f"描述长度: {len(desc)}",
                "任务描述:",
                desc if desc else "[无任务描述]",
                "",
                "---",
                "",
            ])
        (output_dir / f"batch_{batch_no:03d}.md").write_text("\n".join(parts), encoding="utf-8")

    manifest = {
        "input": str(input_path),
        "record_count": len(rows),
        "batch_size": args.batch_size,
        "batch_count": (len(rows) + args.batch_size - 1) // args.batch_size,
    }
    (output_dir.parent / "packet_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
