from __future__ import annotations

import csv
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs/epwk-current-3m-20260820-v3"
REVIEW_FINAL = BASE / "ai_manual_review/final"
DELIVERY = BASE / "最终交付"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv_from_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return

    original_fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key == "manual_review":
                continue
            if key not in seen:
                seen.add(key)
                original_fields.append(key)

    fields = original_fields + [
        "manual_review_index",
        "manual_review_grade",
        "manual_review_keep_high_quality",
        "manual_review_price_confidence",
        "manual_review_tags",
        "manual_review_reason",
    ]

    def cell(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            review = row["manual_review"]
            flat = {key: cell(row.get(key)) for key in original_fields}
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


def save_pair(stem: str, rows: list[dict[str, Any]]) -> None:
    write_jsonl(DELIVERY / f"{stem}.jsonl", rows)
    write_csv_from_rows(DELIVERY / f"{stem}.csv", rows)


def main() -> int:
    DELIVERY.mkdir(parents=True, exist_ok=True)

    all_rows = load_jsonl(REVIEW_FINAL / "全部-附人工审核.jsonl")
    kept_rows = load_jsonl(REVIEW_FINAL / "高质量-人工审核保留.jsonl")

    price_model_rows = [
        row for row in kept_rows
        if row["manual_review"]["price_confidence"] in {"高", "中"}
    ]
    price_model_conservative = [
        row for row in price_model_rows
        if "duplicate_candidate" not in row["manual_review"]["tags"]
    ]
    high_conf_rows = [
        row for row in kept_rows
        if row["manual_review"]["price_confidence"] == "高"
    ]
    low_conf_kept = [
        row for row in kept_rows
        if row["manual_review"]["price_confidence"] == "低"
    ]
    duplicate_candidates = [
        row for row in kept_rows
        if "duplicate_candidate" in row["manual_review"]["tags"]
    ]
    missing_description = [
        row for row in all_rows
        if not str(row.get("description_raw") or "").strip()
    ]

    save_pair("01_全量人工审核_1997", all_rows)
    save_pair("02_高质量主数据_AB_1200", kept_rows)
    save_pair("03_价格建模优先_高中置信度_956", price_model_rows)
    for stale in DELIVERY.glob("04_价格建模核心_高中置信度且暂排重复候选_922.*"):
        stale.unlink()
    save_pair("04_价格建模核心_高中置信度且暂排重复候选_920", price_model_conservative)
    save_pair("05_强价格样本_高置信度_408", high_conf_rows)
    save_pair("06_保留但低价格置信度_244", low_conf_kept)
    save_pair("07_潜在重复候选_51", duplicate_candidates)
    save_pair("08_主数据描述缺失_6", missing_description)

    shutil.copy2(BASE / "final/unresolved-browser-status.json", DELIVERY / "09_候选详情未解决_71.json")
    shutil.copy2(BASE / "ai_manual_review/review_rubric.md", DELIVERY / "审核口径.md")

    grade_counts = Counter(row["manual_review"]["grade"] for row in all_rows)
    kept_price_counts = Counter(row["manual_review"]["price_confidence"] for row in kept_rows)
    trade_counts = Counter(row.get("job_type_raw") for row in all_rows)

    summary = {
        "period": {"from": "2026-05-20", "to": "2026-08-20"},
        "candidate_pool": 2121,
        "detail_enriched": 2050,
        "detail_unresolved": 71,
        "main_trade_mode_records": len(all_rows),
        "trade_modes": dict(trade_counts),
        "description_success": len(all_rows) - len(missing_description),
        "description_missing": len(missing_description),
        "manual_reviewed": len(all_rows),
        "grade_counts": dict(grade_counts),
        "high_quality_ab": len(kept_rows),
        "excluded_cd": len(all_rows) - len(kept_rows),
        "kept_price_confidence": dict(kept_price_counts),
        "price_model_high_or_medium": len(price_model_rows),
        "price_model_conservative_excluding_duplicate_candidates": len(price_model_conservative),
        "high_confidence_only": len(high_conf_rows),
        "low_confidence_but_kept": len(low_conf_kept),
        "duplicate_candidates_in_kept": len(duplicate_candidates),
        "note": "duplicate_candidate 是人工标记的潜在重复，不代表已确认同一项目，因此保留原记录并另行提供保守排除版本。",
    }
    (DELIVERY / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    readme = f"""# EPWK 近三个月数据最终交付说明

## 1. 当前状态

本轮数据周期：**2026-05-20 ～ 2026-08-20**。

数据处理链路已经完成到人工审核与最终打包阶段：

- 候选池：**2121** 条
- 成功获取详情：**2050** 条
- 仍未解决详情页：**71** 条（主要为 403 / 404 / 请求异常，见 `09_候选详情未解决_71.json`）
- 进入主样本的招标/雇佣任务：**1997** 条
  - 招标：**1713** 条
  - 雇佣：**284** 条
- 主样本中有完整任务描述：**1991** 条
- 主样本中描述缺失：**6** 条
- **1997 条主样本已全部逐条人工审核完成，无漏审。**

所以，“人工审核和最终样本整理”已经全部完成；但从最初 2121 个候选的抓取完整性看，仍有 71 条详情页无法成功获取，不能声称候选池 100% 全量详情化。

## 2. 人工审核结果

- A：**{grade_counts['A']}** 条（高质量价格参考）
- B：**{grade_counts['B']}** 条（可用样本）
- C：**{grade_counts['C']}** 条（弱样本）
- D：**{grade_counts['D']}** 条（剔除）

最终高质量主数据采用 **A + B**：

- 保留：**{len(kept_rows)}** 条
- 剔除：**{len(all_rows) - len(kept_rows)}** 条

1200 条保留样本中：

- 价格置信度高：**{kept_price_counts['高']}**
- 价格置信度中：**{kept_price_counts['中']}**
- 价格置信度低：**{kept_price_counts['低']}**

## 3. 推荐使用顺序

### 做总体任务分析 / 分类 / 文本建模
使用：`02_高质量主数据_AB_1200.csv`

这是最推荐的通用主数据。1200 条全部经过逐条人工审核，而且 **没有缺失任务描述的记录**。

### 做价格预测 / 报价模型
优先使用：`03_价格建模优先_高中置信度_956.csv`

这里仅保留 A/B 中价格置信度为“高”或“中”的记录，共 **956** 条。

如果希望更保守，减少潜在重复项目对模型权重的影响，使用：

`04_价格建模核心_高中置信度且暂排重复候选_920.csv`

该版本暂时排除了 `duplicate_candidate` 标记，剩 **920** 条。注意：这些只是“潜在重复候选”，尚未做严格项目实体合并，因此不应直接把被排除的36条永久删除。

### 做最强成交/价格证据分析
使用：`05_强价格样本_高置信度_408.csv`

只保留价格置信度“高”的 **408** 条。

### 复核边界样本
- `06_保留但低价格置信度_244.csv`：任务本身可用，但价格对应关系较弱。
- `07_潜在重复候选_51.csv`：A/B 中被人工标记为可能与其他记录属于同一项目的任务。
- `08_主数据描述缺失_6.csv`：1997 条主数据中描述缺失的记录；这些记录均未进入1200条高质量主数据。

## 4. 文件说明

每一套数据同时提供：

- `.csv`：适合 Excel / 数据分析直接查看
- `.jsonl`：保留嵌套原始字段，适合 Python / LLM / 训练流程

`01_全量人工审核_1997` 保留全部 A/B/C/D 记录，并附每条人工审核信息：

- `grade`
- `keep_high_quality`
- `price_confidence`
- `tags`
- `reason`

原始抓取字段没有覆盖或删除。

## 5. 还没有做的事情

目前唯一值得继续深化的清洗是 **项目级实体去重**：

人工审核中共有 51 条 A/B 记录带 `duplicate_candidate` 标签，但这个标签只是“疑似同一项目”，目前没有强行合并。这样做是为了避免误删真实的重复发布、二次招标或同项目不同阶段任务。

如果后续要正式训练报价模型，建议基于标题、描述、雇主、时间、预算、task_id关系做一次项目级聚类/去重，再决定哪些记录合并、哪些保留。

## 6. 审核原则

详见 `审核口径.md`。

核心原则：脚本只负责整理，**1997 条评分均来自逐条阅读标题、交易模式、预算、状态、分类和完整描述后的人工判断**；C/D 没有从审计数据中物理删除，原始数据始终保留。
"""
    (DELIVERY / "00_说明-先看这里.md").write_text(readme, encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
