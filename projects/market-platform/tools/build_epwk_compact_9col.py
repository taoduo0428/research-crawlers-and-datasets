from __future__ import annotations

import csv
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "outputs/epwk-current-3m-20260820-v3/最终交付/02_高质量主数据_AB_1200.jsonl"
OUTPUT_DIR = ROOT / "outputs/epwk-current-3m-20260820-v3/最终交付"
CSV_OUT = OUTPUT_DIR / "10_最终精简版_9列_1200.csv"
JSONL_OUT = OUTPUT_DIR / "10_最终精简版_9列_1200.jsonl"
SUMMARY_OUT = OUTPUT_DIR / "10_最终精简版_9列_1200_summary.json"

HEADERS = [
    "任务/岗位",
    "分类一级",
    "分类二级",
    "分类三级",
    "价格",
    "任务描述",
    "人工等级",
    "价格可信度",
    "审核理由",
]


def _load() -> list[dict[str, Any]]:
    return [json.loads(line) for line in INPUT.read_text(encoding="utf-8").splitlines() if line.strip()]


def _decimal_number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    return int(integral) if value == integral else float(value)


def _price_source_text(row: dict[str, Any]) -> str:
    evidence = row.get("price_evidence")
    items = evidence if isinstance(evidence, list) else []
    priorities = ("detail_summary", "detail_summary_nuxt", "list_page_embedded_state", "list_page", "platform_estimate")
    for source in priorities:
        for item in items:
            if isinstance(item, dict) and item.get("price_source") == source:
                raw = str(item.get("raw_text") or "").strip()
                if raw:
                    return raw
    for key in ("budget_raw", "price_raw_joined"):
        raw = str(row.get(key) or "").strip()
        if raw:
            return raw.split("|")[0].strip()
    return ""


def _parse_amount_token(token: str) -> Decimal | None:
    token = token.strip()
    if not token:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(万)?", token)
    if not m:
        return None
    value = Decimal(m.group(1))
    if m.group(2):
        value *= Decimal(10000)
    return value


def _clean_price(raw: str) -> int | float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    text = re.sub(r"\s+", "", text)
    text = text.replace("￥", "").replace("¥", "").replace("人民币", "")
    text = text.replace("，", ",").replace(",", "")
    text = text.replace("—", "-").replace("–", "-").replace("~", "-").replace("至", "-")
    text = text.replace("万元", "万").replace("元/月", "").replace("元", "")
    text = text.replace("预算", "").replace("约", "")

    if any(marker in text for marker in ("面议", "待报价", "待服务商报价", "详谈", "再议")):
        # If text contains a numeric amount before an advisory suffix, still try numeric extraction below.
        if not re.search(r"\d", text):
            return None

    # exact amount, e.g. 15000 / 1.5万
    exact = re.fullmatch(r"(\d+(?:\.\d+)?)(万)?", text)
    if exact:
        amount = Decimal(exact.group(1)) * (Decimal(10000) if exact.group(2) else Decimal(1))
        return _decimal_number(amount)

    # upper/lower bounds, e.g. 1000以内 / 1万以上
    bound = re.fullmatch(r"(\d+(?:\.\d+)?)(万)?(以内|以下|以上|起)", text)
    if bound:
        amount = Decimal(bound.group(1)) * (Decimal(10000) if bound.group(2) else Decimal(1))
        return _decimal_number(amount)

    # range where unit may appear on either/both sides: 5000-10000, 1万-2万, 5000-1万, 10-20万
    rng = re.fullmatch(r"(\d+(?:\.\d+)?)(万)?-(\d+(?:\.\d+)?)(万)?", text)
    if rng:
        low_num, low_wan, high_num, high_wan = rng.groups()
        low = Decimal(low_num)
        high = Decimal(high_num)
        # If only the high side carries 万, formats like 10-20万 conventionally mean both are in 万.
        if high_wan and not low_wan and Decimal(low_num) < Decimal(1000):
            low_wan = "万"
        if low_wan:
            low *= Decimal(10000)
        if high_wan:
            high *= Decimal(10000)
        if low > high:
            low, high = high, low
        return _decimal_number((low + high) / Decimal(2))

    # fallback: extract the first obvious range/amount from noisy text.
    m = re.search(r"(\d+(?:\.\d+)?)(万)?-(\d+(?:\.\d+)?)(万)?", text)
    if m:
        low_num, low_wan, high_num, high_wan = m.groups()
        low = Decimal(low_num)
        high = Decimal(high_num)
        if high_wan and not low_wan and Decimal(low_num) < Decimal(1000):
            low_wan = "万"
        if low_wan:
            low *= Decimal(10000)
        if high_wan:
            high *= Decimal(10000)
        return _decimal_number((low + high) / Decimal(2))

    m = re.search(r"(\d+(?:\.\d+)?)(万)?", text)
    if m:
        amount = Decimal(m.group(1)) * (Decimal(10000) if m.group(2) else Decimal(1))
        return _decimal_number(amount)
    return None


def main() -> int:
    rows = _load()
    out: list[dict[str, Any]] = []
    missing_price: list[dict[str, str]] = []
    for row in rows:
        review = row.get("manual_review") if isinstance(row.get("manual_review"), dict) else {}
        raw_price = _price_source_text(row)
        price = _clean_price(raw_price)
        if price is None:
            missing_price.append({
                "record_id": str(row.get("record_id") or ""),
                "title": str(row.get("detail_title_raw") or row.get("title_raw") or ""),
                "raw_price": raw_price,
            })
        out.append({
            "任务/岗位": str(row.get("detail_title_raw") or row.get("title_raw") or "").strip(),
            "分类一级": str(row.get("category_level_1_raw") or "").strip(),
            "分类二级": str(row.get("category_level_2_raw") or "").strip(),
            "分类三级": str(row.get("category_level_3_raw") or "").strip(),
            "价格": price,
            "任务描述": str(row.get("description_raw") or "").strip(),
            "人工等级": str(review.get("grade") or "").strip(),
            "价格可信度": str(review.get("price_confidence") or "").strip(),
            "审核理由": str(review.get("reason") or "").strip(),
        })

    with CSV_OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(out)

    with JSONL_OUT.open("w", encoding="utf-8", newline="") as handle:
        for row in out:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "input_records": len(rows),
        "output_records": len(out),
        "columns": HEADERS,
        "numeric_price_count": sum(1 for row in out if isinstance(row["价格"], (int, float))),
        "missing_price_count": len(missing_price),
        "missing_price_examples": missing_price[:20],
        "price_policy": "沿用旧版：精确金额直接取值；区间取中点；N元/万元以内取上限；N元/万元以上取下限；无法确定时留空。",
    }
    SUMMARY_OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
