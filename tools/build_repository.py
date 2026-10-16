
#!/usr/bin/env python3
"""Prepare a privacy-reviewed, reproducible, offline monorepo copy.

Run locally: python tools/build_repository.py
Sources are read only. Original project directories are never modified.
No network calls. Never place credentials or captured login state in Git.
"""
from __future__ import annotations
from pathlib import Path
import collections
import csv
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

OUT = Path(__file__).resolve().parents[1]
DESKTOP = Path.home() / "Desktop"
KNOW = DESKTOP / "个人知识库" / "项目"
MARKET = DESKTOP / "外包+岗位爬虫"
FACEBOOK = DESKTOP / "facebook"
BLIB = KNOW / "爬虫" / "B站爬虫"
BLIB_EXTRA = KNOW / "B站"
KEEP = KNOW / "爬虫" / "keep爬虫"
DOUBAN = KNOW / "豆瓣" / "fcrs2"
DOUBAN_OLD = DESKTOP / "大学" / "大二下" / "网络爬虫" / "自学爬虫" / "dbfcrs2"
AMAZON = KNOW / "爬虫" / "亚马逊"
AMAZON_EXTRA = KNOW / "亚马逊"
DATE = datetime.now(timezone.utc).isoformat(timespec="seconds")

PHONE = re.compile(r"(?<![0-9])1[3-9][0-9]{9}(?![0-9])")
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
AUTH_LITERAL = re.compile(r"""(?:password|passwd|pwd|api[_-]?key|client[_-]?secret|access[_-]?token|rank-login-user|authorization)\s*[:=]\s*['"][^'"]{7,}['"]""", re.I)
AUTH_TOKEN = re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{15,}|sk-[A-Za-z0-9_-]{20,}|Bearer\s+eyJ[A-Za-z0-9._-]{24,})", re.I)
SECRET_LINE = re.compile(r"(?im)^\s*(?:(?:[\"'](?:password|passwd|pwd|token|secret|authorization|api[_-]?key|client[_-]?secret)[\"']\s*:)|(?:password|passwd|pwd|secret|api[_-]?key|token|client[_-]?secret)\s*=)\s*[\"'][^\"']{8,}[\"']")
DATA_PHONE = re.compile(r"(?<![0-9])1[3-9][0-9]{9}(?![0-9])")
PII_FIELDS = re.compile(r"(?:mobile|phone|email|mail|contact|publisher_name|联系人|电话|手机号|手机|邮箱|微信|微信号|qq号|雇主名称)", re.I)
SKIP_DIRS = {".git", ".venv", ".venv-standalone", "venv", "node_modules", "__pycache__", ".pytest_cache",
             "dist", "build", ".cache", ".codegraph", ".waylog", ".serena", "site-packages"}
TRACKED = []
SOURCES = []
EXCLUDED = collections.Counter()
COPIED = 0

def digest(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for part in iter(lambda: f.read(2**20), b""):
            h.update(part)
    return h.hexdigest()

def write(rel: str, text: str):
    dst = OUT / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text.rstrip() + "\n", encoding="utf-8")
    return dst

def write_if_missing(rel: str, text: str):
    """Never overwrite audited project handbooks on a data rebuild."""
    path = OUT / rel
    return path if path.exists() else write(rel, text)

def scrub_text(value: str) -> str:
    text = EMAIL.sub("[EMAIL_REDACTED]", str(value))
    text = DATA_PHONE.sub("[PHONE_REDACTED]", text)
    text = AUTH_TOKEN.sub("[CREDENTIAL_REDACTED]", text)
    return text

def scrub_python(content: str, original: Path) -> str:
    """Patch known legacy literal credentials in *copies* of Amazon scripts."""
    t = content
    amazon_part = any(x in str(original).lower() for x in ("jiimore", "sellersprite", "亚马逊"))
    if amazon_part:
        # Legacy Python assignments, including the Jiimore main entrypoint.
        pat = re.compile(r"""(?m)^(\s*)(phone_num|password|pwd|passwd)\s*=\s*(['"])([^'"]+)\3""", re.I)
        def assign(m):
            var = m.group(2).lower()
            name = "JIIMORE_PHONE" if var == "phone_num" else "JIIMORE_PASSWORD"
            return m.group(1) + m.group(2) + " = os.environ.get('" + name + "', '')"
        t = pat.sub(assign, t)
        t = re.sub(r"""(?P<prefix>['"]rank-login-user['"]\s*:\s*)['"][^'"]+['"]""",
                   lambda m: m.group("prefix") + "os.environ.get('SELLERSPRITE_COOKIE', '')", t)
        # Both single and double-quoted hardcoded demo/real credentials in main().
        t = re.sub(r"""main\(\s*(['"])Makeup\1\s*,\s*(['"])1[3-9][0-9]{9}\2\s*,\s*(['"])[^'"]+\3\s*\)""",
                   "main('Makeup', os.environ.get('JIIMORE_PHONE', ''), os.environ.get('JIIMORE_PASSWORD', ''))", t)
        # All remaining non-example literal Chinese mobile numbers are withheld.
        t = PHONE.sub("00000000000", t)
        if "os.environ" in t and not re.search(r"(?m)^\s*(?:import os|from os import)", t):
            t = "import os\n" + t
    return t

def safety_check_source(text: str, original: Path) -> bool:
    # Reject candidate files still containing production-like credentials.
    if AUTH_TOKEN.search(text):
        return False
    # Keep original code if a variable named "password" is referenced,
    # but reject assignments with concrete long literals.
    if SECRET_LINE.search(text):
        return False
    # Most readmes contain examples; only code with actual phone literals is blocked.
    if original.suffix.lower() in (".py", ".js", ".mjs", ".ps1") and PHONE.search(text):
        return False
    return True

def copy_source(src: Path, rel: str, *, notebook: bool = False):
    global COPIED
    if not src.is_file():
        EXCLUDED["source_missing"] += 1
        return
    ext = src.suffix.lower()
    try:
        payload = src.read_bytes()
        if payload and payload.count(b"\x00") > len(payload) * 0.9:
            EXCLUDED["source_corrupt_null_bytes"] += 1
            return
        if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
            raw = payload.decode("utf-16")
        elif b"\x00" in payload[:4096]:
            raw = payload.decode("utf-16-le", errors="replace")
        else:
            raw = payload.decode("utf-8-sig", errors="replace")
    except (OSError, UnicodeError):
        EXCLUDED["source_error"] += 1
        return
    if notebook or ext == ".ipynb":
        try:
            nb = json.loads(raw)
            for cell in nb.get("cells", []):
                if cell.get("cell_type") == "code":
                    t = "".join(cell.get("source", []))
                    t = scrub_python(t, src)
                    cell["source"] = t.splitlines(True)
                    cell["outputs"] = []
                    cell["execution_count"] = None
            nb["metadata"] = {"kernelspec": {"name":"python3", "display_name":"Python 3", "language":"python"}}
            raw = json.dumps(nb, ensure_ascii=False, indent=2)
        except Exception:
            EXCLUDED["notebook_parse_error"] += 1
            return
    elif ext == ".py":
        raw = scrub_python(raw, src)
    elif ext in (".md",".txt",".json",".yaml",".yml",".html",".css",".js",".mjs",".ps1",".sh",".cmd",".bat",".toml",".xml"):
        raw = scrub_text(raw) if ext in (".md", ".txt") else raw
    if not safety_check_source(raw, src):
        EXCLUDED["source_sensitive_blocked"] += 1
        # Only paths, never contents. Store locally in build log.
        return
    dst = write(rel, raw)
    COPIED += 1
    SOURCES.append({"path": dst.relative_to(OUT).as_posix(),
                    "source_rel_desktop": src.relative_to(DESKTOP).as_posix(),
                    "source_sha256": digest(src), "published_sha256": digest(dst),
                    "changed_for_publication": digest(src) != digest(dst)})

def copy_source_tree(src: Path, rel: str, extensions: set[str] | None = None, *, max_bytes: int = 900_000,
                     excluded: set[str] | None = None):
    allowed = extensions or {".py",".md",".txt",".json",".toml",".yaml",".yml",".ps1",".sh",".bat",".cmd",
                             ".html",".css",".js",".mjs",".cfg",".lock",".xml",".java"}
    excludes = SKIP_DIRS | (excluded or set())
    if not src.exists():
        EXCLUDED["source_tree_missing"] += 1
        return
    for here, dirs, names in os.walk(src, followlinks=False):
        dirs[:] = sorted([d for d in dirs if d not in excludes and not d.startswith(".")])
        for name in sorted(names):
            source = Path(here) / name
            if source.is_symlink():continue
            if source.suffix.lower() not in allowed:continue
            if source.stat().st_size > max_bytes:continue
            if re.search(r"(?:secret|credential|cookie|token|password|[.]env|[.]private)",name,re.I):
                EXCLUDED["source_named_sensitive"] += 1
                continue
            sub = source.relative_to(src).as_posix()
            copy_source(source, rel.rstrip("/") + "/" + sub)

def save_data(src: Path, rel: str, *, data_kind: str, safe_csv=True):
    """Upload-friendly CSV with PII/contact tokens withheld; origin digest retained."""
    if not src.is_file():
        EXCLUDED["data_missing"] += 1
        return
    dst = OUT / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    n = None
    privacy = "source copy"
    if safe_csv and src.suffix.lower() == ".csv":
        with src.open("r",encoding="utf-8-sig",errors="replace",newline="") as fi, dst.open("w",encoding="utf-8",newline="") as fo:
            r=csv.DictReader(fi)
            if not r.fieldnames:
                EXCLUDED["data_empty_header"]+=1
                dst.unlink(missing_ok=True)
                return
            w=csv.DictWriter(fo,fieldnames=r.fieldnames,extrasaction="ignore",lineterminator="\n")
            w.writeheader()
            n=0
            for row in r:
                cleaned={}
                for k,v in row.items():
                    if k is None:continue
                    if PII_FIELDS.search(k):
                        cleaned[k] = "[REDACTED]" if v and str(v).strip() else ""
                    else:cleaned[k]=scrub_text(v or "")
                w.writerow(cleaned);n+=1
        privacy="contact fields, emails, phone numbers and token literals redacted"
    else:
        shutil.copy2(src,dst)
    TRACKED.append({"path":rel,"origin":src.relative_to(DESKTOP).as_posix(),
                    "source_sha256":digest(src),"sha256":digest(dst),"bytes":dst.stat().st_size,
                    "records":n,"privacy_processing":privacy,"dataset":data_kind})
    print(json.dumps({"copied":rel,"MiB":round(dst.stat().st_size/1048576,2),"rows":n},ensure_ascii=True),flush=True)

def stable_private_id(x: object) -> str:
    """One-way pseudonym without publishing its secret seed."""
    import hmac
    import base64
    digest_bytes = hmac.new(PRIVATE_SEED, str(x).encode("utf-8"), hashlib.sha256).digest()[:12]
    return base64.b32encode(digest_bytes).decode("ascii").rstrip("=")

# Volatile salt: not saved in Git. Dataset joins work within one export.
PRIVATE_SEED = os.urandom(32)
SENSITIVE_KEEP = re.compile(r"(?:name|username|nickname|nick|avatar|photo|url|email|mail|phone|mobile|address|location|longitude|latitude|geo|street|ipaddr|device|cookie|token|password|credential|session|authorization|bio|signature|openid|open_id|account|content|description|text|title|comment|image|video|remark)",re.I)
ID_FIELD = re.compile(r"^(?:id|_id|uid|userid|user_id|postid|post_id|entryid|entry_id|authorid|author_id)$",re.I)

def anonymize_keep(value, key="", depth=0):
    if depth>48:return None
    if isinstance(value, dict):
        new={}
        for k,v in value.items():
            if re.search(r"token|secret|cookie|password|credential|session|authorization|openid",k,re.I):continue
            if ID_FIELD.fullmatch(k) and isinstance(v,(str,int)):
                new[k]=stable_private_id(v)
            elif SENSITIVE_KEEP.search(k):
                # Preserve schema for parsing while never shipping original text/identifiers.
                if isinstance(v,dict):new[k]=anonymize_keep(v,k,depth+1)
                elif isinstance(v,list):new[k]=[anonymize_keep(x,k,depth+1) for x in v[:400]]
                else:new[k]=None
            else:new[k]=anonymize_keep(v,k,depth+1)
        return new
    if isinstance(value,list):return [anonymize_keep(x,key,depth+1) for x in value[:2500]]
    if isinstance(value, str):
        return scrub_text(value)[:512] if len(value) <= 512 else None
    if isinstance(value, int) and PHONE.fullmatch(str(value)):
        return stable_private_id("numeric-private:" + str(value))
    return value

def export_keep():
    """The maintained tools/keep_release.py is the only Keep publication logic."""
    from keep_release import publish
    return publish(KEEP, OUT, TRACKED, digest, stable_private_id)


def export_facebook():
    folder=FACEBOOK/"runs"
    seen=set();pairs={"reactions":set(),"shares":set()}
    for p in sorted(folder.rglob("*.csv")):
        if not p.is_file():continue
        try:
            fingerprint=digest(p)
            if fingerprint in seen:continue
            seen.add(fingerprint)
            with p.open(encoding="utf-8-sig",errors="replace",newline="") as f:
                r=csv.DictReader(f);h=r.fieldnames or []
                if "source_post_id" in h and "user_id" in h:kind,usercol="reactions","user_id"
                elif "source_post_id" in h and "actor_id" in h:kind,usercol="shares","actor_id"
                else:continue
                for rec in r:
                    post=(rec.get("source_post_id") or "").strip()
                    user=(rec.get(usercol) or "").strip()
                    if post and user:pairs[kind].add((stable_private_id("p:"+post),stable_private_id("u:"+user)))
        except (OSError,UnicodeError,csv.Error):
            EXCLUDED["facebook_unreadable_csv"]+=1
    for kind,values in pairs.items():
        rel=f"projects/social/facebook/data/sanitized_{kind}.csv"
        dst=OUT/rel;dst.parent.mkdir(parents=True,exist_ok=True)
        with dst.open("w",encoding="utf-8",newline="") as f:
            w=csv.writer(f,lineterminator="\n")
            w.writerow(["source_post_key","actor_key"])
            for pair in sorted(values):w.writerow(pair)
        TRACKED.append({"path":rel,"origin":"Derived across facebook/runs CSV, excluding exact duplicate files","source_sha256":None,
          "sha256":digest(dst),"bytes":dst.stat().st_size,"records":len(values),
          "privacy_processing":"salted one-way pseudonymization of post and user IDs; all names and URLs omitted",
          "dataset":"facebook-"+kind})
    return {k:len(v) for k,v in pairs.items()}

def export_douban():
    src=DOUBAN/"movies.txt"
    if not src.exists():return
    dest=OUT/"projects/social/douban/data/movies_anonymized.txt"
    dest.parent.mkdir(parents=True,exist_ok=True)
    text=src.read_text(encoding="utf-8-sig",errors="replace")
    # Preserve review text for private academic analysis; anonymize review author names.
    text=re.sub(r"(?m)(\bID:\s*)([^,\n]+)(,\s*Title:)",lambda m:m.group(1)+stable_private_id(m.group(2))+m.group(3),text)
    text=scrub_text(text)
    dest.write_text(text,encoding="utf-8")
    TRACKED.append({"path":dest.relative_to(OUT).as_posix(),"origin":src.relative_to(DESKTOP).as_posix(),
     "source_sha256":digest(src),"sha256":digest(dest),"bytes":dest.stat().st_size,"records":6,
     "privacy_processing":"reviewer names pseudonymized; email and phone masked","dataset":"douban-movies"})
    other=DOUBAN_OLD/"review.txt"
    if other.exists():
        dst=OUT/"projects/social/douban/data/historical_reviews_text.txt"
        dst.parent.mkdir(parents=True,exist_ok=True)
        dst.write_text(scrub_text(other.read_text(encoding="utf-8-sig",errors="replace")),encoding="utf-8")
        TRACKED.append({"path":dst.relative_to(OUT).as_posix(),"origin":other.relative_to(DESKTOP).as_posix(),
         "source_sha256":digest(other),"sha256":digest(dst),"bytes":dst.stat().st_size,"records":None,
         "privacy_processing":"email and phone masked","dataset":"douban-older-reviews"})

def export_boss_inventory():
    """Canonical, conservative ID inventory from locally persisted shards.

    Extract only a known whitelist of fields, never raw HTTP payloads or cookies.
    """
    files=[
     MARKET/".crawler-state/boss-original-run/shards/worker-1.jsonl",
     MARKET/".crawler-state/boss-detail-parallel/shards/worker-1.jsonl",
     MARKET/".crawler-state/boss-detail-parallel/shards/worker-2.jsonl",
     MARKET/".crawler-state/boss-detail-parallel/shards/worker-3.jsonl",
     MARKET/".crawler-state/recruitment-details/BOSS/workers/worker-1.jsonl",
     MARKET/".crawler-state/recruitment-details/BOSS/workers/worker-2.jsonl",
     MARKET/".crawler-state/recruitment-details/BOSS/workers/worker-3.jsonl"
    ]
    unique={}
    cols=["record_id","title_raw","site_name","category_path_raw","category_level_1_raw","category_level_2_raw","category_level_3_raw",
          "published_at_raw","description_raw","budget_raw","status_raw","list_url","detail_url","source_file"]
    for src in files:
        if not src.exists():continue
        with src.open(encoding="utf-8-sig",errors="replace") as f:
            for line in f:
                try:r=json.loads(line)
                except:continue
                if not isinstance(r,dict):continue
                rid=r.get("record_id") or r.get("job_id") or r.get("id")
                if not rid:continue
                rid=str(rid)
                if rid not in unique:unique[rid]={}
                rec=unique[rid]
                for key in cols[:-1]:
                    v=r.get(key)
                    if isinstance(v,(str,int,float)) and str(v).strip():
                        if not rec.get(key):rec[key]=scrub_text(str(v))
                if not rec.get("source_file"):rec["source_file"]=src.name
    rel="projects/recruitment/boss/data/source_id_inventory.csv"
    dest=OUT/rel;dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open("w",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=cols,lineterminator="\n");writer.writeheader()
        for record in unique.values():writer.writerow(record)
    TRACKED.append({"path":rel,"origin":"Derived from seven historical local BOSS JSONL shards",
       "source_sha256":None,"sha256":digest(dest),"bytes":dest.stat().st_size,"records":len(unique),
       "privacy_processing":"projected safe fields only; raw sessions/HTTP packets omitted","dataset":"boss-id-inventory"})
    return len(unique)

def generate_project_docs(stats: dict):
    READMES = {
"projects/market-platform/README.md": """
# 外包 / 招聘公共采集引擎

这是原桌面 `外包+岗位爬虫` 的**代码工作区**。四个外包适配器与 BOSS、前程无忧和猎聘共用 `drv_crawler`，因此仅保留一份代码，不重复复制多个可能分叉的版本。

## 安装
- Python 版本按原 `pyproject.toml` / `requirements.txt`。
- Windows：`py -m venv .venv`，激活后 `pip install -r requirements.txt`。
- 查看命令：`python run.py --help` 或 `python crawler.py --help`。
- 测试（不发起抓取）：`python -m pytest -q tests`。
- 站点选择、采集窗口和停止条件以 `crawler.config.yaml` 与当前 CLI 帮助为准，不能仅按示例参数直接对网站执行批量抓取。
- 自己准备网络、浏览器和合法账号；本仓库不提供登录态、IP 池密钥或本机 Cookie。相关配置仅在本机填写，禁止提交到 Git。

## 数据与使用边界
- 外包任务：`../outsourcing/`；招聘岗位：`../recruitment/`。
- 采集器含网页解析、分页、详情抓取、时间窗口判断、错误记录、断点/恢复、归档和数据后处理，部分历史入口需要具体平台状态。
- `tools/` 是数据清洗、恢复与最终交付工具；要复用请从 `projects/market-platform` 作为当前工作目录执行。
- 不包含本地历史浏览器用户目录（约数 GB）、.env、真实登录状态、完整包捕获和本机运行缓存。
- 第三方子组件保留其原始版权说明，研究使用前仍应阅读对应许可和平台规则。
""",
"projects/outsourcing/README.md": """
# 外包项目组：四个网站

统一代码位于 [公共引擎](../market-platform/README.md)。四站数据不可直接合计，存在项目任务、薪酬与平台估价口径差异。

| 网站 | 目录 | 当前交付 |
|---|---|---|
| 一品威客 | `epwk/` | 旧版 2,691 条 + 新版人工审核 1,997 条，多级可用数据 |
| 猪八戒 | `zbj/` | 7,082 条，列表阶段的正文和平台估价 |
| 实现网 | `shixian/` | 53 条，其中 46 项外包，7 个全职岗位 |
| 程序员客栈 | `proginn/` | 有适配器，没有经过确认的正式数据 |

采集命令请从 `../market-platform` 中 `run.py --help` 查看；发布数据已脱敏，不包含私人联系方式。近三个月筛选必须依据**真实任务发布日期**；站点地图 `lastmod` 只能作为候选时间，不得伪装为发布日期。
""",
"projects/outsourcing/epwk/README.md": """
# 一品威客（epwk）

## 源码
`../../market-platform/drv_crawler/sites.py` 中 `EpwkAdapter`；入口 `../../market-platform/run.py`。依赖原始项目配置、合法网络环境和可验证的任务详情。

## 数据
- `data/legacy-2691.csv`：旧版 2,691 条，任务描述为空，适合核对 ID，不适合正文分析。
- `data/reviewed-1997.csv`：新版本 1,997 条人工审核；A 677 / B 523 / C 424 / D 373。
- `data/high-quality-ab-1200.csv`：A/B 子集 1,200 条。
- `data/price-high-confidence-956.csv`：价格建模优先子集 956 条。
- 其他 `data/` 子集：去重候选、核心样本、强价格样本等，均来自同一审核主集，**不能与主表相加**。

## 数据局限与复现
新版有 376 条详情发布日期为空、使用 sitemap_lastmod 候选日期，A/B 子集 229 条、价格优先子集 195 条。未取得发布日期时不得宣称“已确认近三个月发布”。旧版原始 `run-report` 标记 `partial` / `window_complete=false`，不能按全站完全覆盖理解。
数据主键 `record_id`；预算文本可包含区间、标价、估价、协商价，须区分来源与单位。目录下的 CSV 对联系信息和邮箱/手机号做了脱敏，字段与本机原始记录可对齐。
""",
"projects/outsourcing/zbj/README.md": """
# 猪八戒（zbj）
源码：`../../market-platform/drv_crawler/sites.py` 的 `ZbjAdapter`。
数据：`data/records.csv`，7,082 条、record_id 无重复。`run-report` 报告 341 个列表页、`window_complete=true`，但 `details_fetched=0`；描述中位数 46 字，不能把列表摘要宣称为完整详情。只有约 1,668 条包含非零数字的预算或价格字段；另一些是平台估价或非数字报价。按本项目需求评估前先做价格类型、单位和真实性筛选。
""",
"projects/outsourcing/shixian/README.md": """
# 实现网（shixian）
源码：`../../market-platform/drv_crawler/sites.py` 的 `ShixianAdapter`。
`data/records.csv` 有 53 条：`content_category=outsourcing_task` 共 46，`full_time_job` 共 7。不能混合任务预算和全职薪酬。详情描述中位数约 224 字，数量很少；分析时应作为小样本或辅助来源。
""",
"projects/outsourcing/proginn/README.md": """
# 程序员客栈（proginn）
源码：`../../market-platform/drv_crawler/sites.py` 中 `ProginnAdapter.crawl`。
当前没有验证过的本地正式交付 CSV/JSONL，故没有人为制造数据文件。此适配器面向程序员人才及报价，**不能按“外包项目预算”解释薪酬**。运行前检查页面结构和用途，使用与平台条款一致的方式操作。
""",
"projects/recruitment/README.md": """
# 招聘网站项目组

三个网站共用 [采集与处理代码](../market-platform/README.md)。`data/final.csv` 均为统一六字段交付：`职位、JD、薪资、一级分类、二级分类、三级分类`。这种交付不含公司、源 ID、源 URL，不具备完整溯源能力；源层与交付层必须分别保存。

| 网站 | 最终行数 | 源层 |
|---|---:|---|
| BOSS直聘 | 39,594 | 历史记录 ID 约 40,747，保留 ID 投影表 |
| 前程无忧 / 51job | 27,552 | 大规模最终层可用；本机当前主要断点不足以恢复对应全量原始层 |
| 猎聘 | 46,607 | 完整源层 49,056，2,449 条缺 JD |

`final.csv` 不能凭六字段完全一致就删行：不同公司和来源可能存在同内容职位。回溯时使用来源站点、记录 ID 与岗位 URL，缺失时应标记不可回溯。
""",
"projects/recruitment/boss/README.md": """
# BOSS直聘
采集代码 `../../market-platform` 中的 `boss_*`、`drv_crawler/recruitment.py`、相关测试与工具。
`data/final.csv`：39,594 条清洗岗位。
`data/source_id_inventory.csv`：从本机 7 个不同采集/详情 worker JSONL 断点投影出的**岗位 ID 归档**（不是原始网页，也不等于完整 JD）；用于恢复分类和链接溯源。不得将断点文件重复行相加。
源记录历史统计约 40,750；以本机完整性审计和 manifest 为准。
""",
"projects/recruitment/job51/README.md": """
# 前程无忧 / 51job
项目代码 `../../market-platform/drv_crawler/job51/` 包括分类枚举、分页、断点 SQLite、导出与测试。
`data/final.csv`：27,552 条清洗结果，106 行薪资为空。
现存 `job51/output/jobs.jsonl` 只有少量历史试跑记录；本机 `.state` SQLite 也仅有少量测试/烟测记录，不是 27,552 条完整原始层，故不作为正式原始数据上传。保留六字段表，并在未来采集时先写入包含 record_id、URL、公司、JD 的原始层。
""",
"projects/recruitment/liepin/README.md": """
# 猎聘
源码 `../../market-platform/drv_crawler/liepin/`，包括请求、解析、断点和协调支持。
`data/full-source.csv`：49,056 条完整层（31 列、record_id 唯一）；其中 2,449 条 JD 空白。
`data/final.csv`：46,607 条六字段清洗版，JD 均非空。数量上两者相差 2,449，但单条原文完全一致验证未完成，不据此断言每条都逐一映射成功。
""",
"projects/social/bilibili/README.md": """
# B站：视频元数据、字幕与封面

## 源码
`source/` 保留爬取视频元数据、API 笔记本、字幕及封面采集脚本，含部分历史版本。对原有 notebook 已清空输出并做静态安全检查，不提供登录态。部分旧脚本使用绝对 Windows 路径，运行前须调整到当前文件目录，并根据 B站 API 更新字段和反爬策略。

## 历史数据
- `data/第一次字幕/结果.csv`：3,259 个 CID，其中 2,507 行有字幕。
- `data/结果.csv`：3,132 行、3,020 个 CID，有 112 个重复 CID。
- 合并两份主数据有 4,904 个 CID；3,667 个至少在某版本有字幕（覆盖率约 74.78%），1,237 个两份均无字幕。
- `data/` 还保存部分键盘/手环主题、视频元数据和 Excel，但不同版本有重叠，**不可相加当独立视频数**。

## 分析建议
主键使用 CID；同 CID 同时出现空字幕与非空字幕时优先非空，字幕文本不同时人工审查（已有至少 3 个冲突 CID）。文本与视频元数据属于第三方内容，限有权限的学术研究，勿公开再分发。
""",
"projects/social/facebook/README.md": """
# Facebook：Android 抓包、反应用户及分享采集

## 代码入口与安装
`source/` 来源于原项目 `release-files.txt` 白名单（而非整份个人浏览器/模拟器目录）。
Windows 先读 `source/WINDOWS_DEPLOYMENT.md`；Mac 先读 `source/macos/QUICKSTART.md` 和实际设备验收要求。安装 `source/requirements.txt`，由 `source/START.cmd` / `source/START.command` 启动；页面和 `/v1/jobs` 本地 API 负责提交、查询、取消、CSV 导出。需要 Android 模拟器、合法取得的 APK、人工登录、代理和合适网络，**本仓库不含这些敏感组件**。
`source/facebook_capture_api.py` + `source/facebook_android_capture.py` 为调度与 Android 采集链；`source/analyze_facebook_capture.py` 解析历史记录。

## 发布数据（去身份）
`data/sanitized_reactions.csv`、`data/sanitized_shares.csv` 使用每次导出随机密钥作不可逆假名，保留“帖子-用户”二元关系而不保留用户真实 ID、姓名、用户名、URL、帖子正文或任何 Cookie。来自历史多次试验并已去重，不代表一次生产全量采集。
源日志历史 670 个任务：210 completed、233 failed、226 cancelled、1 cancelling。状态 `completed` 也必须结合 target match / terminal / integrity 评估。
## 保密限制
个人登录态、HTTP MITM 完整流、证书、APK、raw JSONL、模拟器磁盘、个人资料及本机 `runs` **未上传**。如有获批的本地审计需求，由授权成员在隔离环境读取本机原件；不能放进 GitHub。组内也不能将假名数据与外部身份列表连接尝试重新识别。
""",
"projects/social/keep/README.md": """
# Keep：明确的两阶段项目

## 第一阶段：抓包 / 收集 JSON 响应
原目录 `用户数据/` 101 个 JSON 与 `动态爬取/chenggong1/` 105 个 JSON，来自抓包/历史人工采集产物。这里**没有证据确认存在一个可直接一键运行的第一阶段抓包程序**，也不会虚构；项目还保留指向历史“八爪鱼采集器”的快捷方式，但非可移植的安装包。
`data/phase1_anonymized/profiles/` 与 `activities/` 上传的是**仅保留研究必要字段的最小化匿名 JSON 摘要**。真实用户名、个人简介、内容、评论正文、媒体地址、原始 ID、Cookie 和位置信息没有上传；本机原始 JSON 完整保留。

## 第二阶段：结构化整理 / 输出
`source/phase2/` 是历史 JSON 转 CSV 脚本（原来硬编码旧 Windows 路径、混合评论行且存在缺失值问题）。
`data/phase2/profiles.csv`：按用户 ID 清洗资料；`posts.csv`：按动态 ID 清洗动态；`comments.csv`：按动态关联的示例评论索引。三张表共享第一阶段假名 ID。可以用 `python ../../../tools/normalize_keep.py --help` 查看新转换脚本；不允许直接将原始敏感 JSON 提交至仓库。

## 数据解释
原始动态共 1,146 条、1,050 个非重复动态 ID，评论示例合计 1,692 条；历史 CSV 2,838 行不等于 2,838 条动态。
101 份资料中对应 100 个不同用户 ID；动态里出现 95 个作者 ID，匹配的约 51 个用户。抓包第一阶段的原始字段和采样范围决定第二阶段的可关联性。
""",
"projects/social/douban/README.md": """
# 豆瓣：电影、演员与影评 Scrapy 实验

`source/fcrs2` 来自桌面 `个人知识库/项目/豆瓣/fcrs2`，可从其中的 `scrapy.cfg` 运行；另保留课程项目早期版本 `source/course-legacy`。需要 `pip install scrapy itemadapter`，进入 `source/fcrs2` 使用 `scrapy list` 查看 Spider，再按需运行 `scrapy crawl <spider-name>`。
`data/movies_anonymized.txt` 是原 `movies.txt` 的去身份副本，电影块 6 个、19 行影评、36 条演员信息，绝非 319 部电影。`data/historical_reviews_text.txt` 是更早课程数据，不能与主表重复相加。
当前 `pipelines.py` 以电影名称去重、覆盖写 `movies.txt`，回调并发可能影响完整性；重新采集前建议使用电影 ID 和流式输出，不要直接覆盖既有成果。
""",
"projects/commerce/amazon-jiimore/README.md": """
# 亚马逊 + Jiimore：一个多数据源商品研究项目

**一个项目，不将 Amazon、Jiimore 和 SellerSprite 作为彼此独立的三个课题。** 全部通过 `ASIN` 关联。
代码位于 `source/current`、`source/legacy`、`source/supplement`，包括 Amazon 商品详情、Jiimore 品类 ASIN 与周点击量、SellerSprite 月销量/留评率/Keepa 风格趋势。主运行入口推荐先看 `source/current/jiimore1.py` 和 `source/current/sellersprite.py`；有些早期脚本仍是历史实验，不保证每个入口都能独立工作。运行前安装 `requests pandas beautifulsoup4 DrissionPage openpyxl` 等所需依赖，配置 `JIIMORE_PHONE` / `JIIMORE_PASSWORD` / `SELLERSPRITE_COOKIE` 在本机环境变量，不要写到源码或提交 Git。

## 数据关联
- `data/amazon/products.csv`：2,006 行，其中 1,999 个有效唯一 ASIN；商品标题、价格、评分等。
- `data/amazon/tags.csv`：8,533 行，覆盖 1,342 个 ASIN。
- `data/jiimore/weekly_clicks.csv`：117,049 条、覆盖同样 1,999 个 ASIN（2023-01 至 2024-04）。
- `data/sellersprite/monthly_sales.csv`：66,871 行，覆盖 1,998 个 ASIN。
- `data/sellersprite/monthly_reviews.csv`：178,785 行，覆盖 1,999 个 ASIN。
- `data/sellersprite/keepa_history.csv`：25,824 行，仅覆盖 18 个 ASIN；接口在 SellerSprite 域名，不代表直接调用 Keepa。
四个主要表共同覆盖 1,998 个 ASIN。关联用 `ASIN` + （`日期` / `月份`），切勿只按 ASIN 去重。
已观察月销量 28 条、月评论 1 条“ASIN + 月份”非完全相同冲突。`amazon global shipping` 列现有商品表全部为空；其他时间序列亦有缺失。

## 认证与安全
所有发现的硬编码旧手机号/密码/Cookie 在发布副本中需替换为环境读取，原始文件留在本机；建议立即轮换此前使用过的有效凭据。原 ZIP/HTML 可能含认证会话，未上传。
"""
    }
    for path,body in READMES.items():
        # Project handbooks are curated independently after first publication.
        # Do not overwrite their audited corrections on a data rebuild.
        if not (OUT/path).exists():
            write(path,body)
    write_if_missing("README.md",f"""
# 课题组爬虫源码与数据总仓库

本仓库按原有课题分组整理指定的 **12 个研究目标**：外包 4 站、招聘 3 站、B站、Facebook、Keep、豆瓣、亚马逊+Jiimore 整合项目。项目源文件来源于本机，原目录只读；**仓库应一直保持 Private**。

## 快速导航

| 类别 | 子项目 | 说明 |
|---|---|---|
| 共用引擎 | [外包/招聘源码](projects/market-platform/README.md) | 共享 `drv_crawler`、运行入口、测试与导出工具 |
| 外包 | [四个平台](projects/outsourcing/README.md) | 一品威客、猪八戒、实现网、程序员客栈 |
| 招聘 | [三个平台](projects/recruitment/README.md) | BOSS直聘、前程无忧、猎聘 |
| 视频 | [B站](projects/social/bilibili/README.md) | 视频与字幕、多批次数据 |
| 社交 | [Facebook](projects/social/facebook/README.md) | Android 抓包、批次、反应/分享用户的脱敏研究表 |
| 运动社区 | [Keep](projects/social/keep/README.md) | 第一阶段抓包 JSON、第二阶段结构化处理 |
| 影视社区 | [豆瓣](projects/social/douban/README.md) | 电影与影评 |
| 电商 | [Amazon + Jiimore](projects/commerce/amazon-jiimore/README.md) | 以 ASIN 为主键的整合项目 |

## 首次获取
```bash
git clone https://github.com/taoduo0428/research-crawlers-and-datasets.git
cd research-crawlers-and-datasets
git lfs install
git lfs pull
python tools/verify_release.py
```

数据使用 Git LFS，普通网页上的小文件是指针不是完整数据；需要 `git lfs pull`。GitHub 私有仓库不等于允许公开转发第三方网站的版权内容或用户数据。本仓库已排除私人浏览器状态、认证信息、Facebook 原始 MITM 抓包，且对敏感社交数据进行脱敏。
数据清单与 SHA-256：`metadata/data-manifest.json`；来源及发布副本代码校验：`metadata/source-manifest.json`；限制与遗漏：`docs/SECURITY_AND_LIMITATIONS.md`。所有采集需要遵守所在地规则、平台协议和研究伦理，不允许通过发布的匿名数据尝试重识别个人。

## 研究复现
- 每个目标网站都有单独的 README，并标明其真实数据数量、缺失和最初代码入口。
- 不同批次的文件不直接相加；为学术分析应按 ID/时间关联。
- 请先执行离线验证或单元测试，不要直接用默认配置对网站发起大规模请求。
- 新增采集的本地私密原件必须先完成敏感信息审查再进入仓库。不要提交 .env、真实 Cookie、浏览器 Profile、APK、HAR/PCAP/MITM、认证密钥或尚未获授权的身份数据。
""")
    write_if_missing("docs/SECURITY_AND_LIMITATIONS.md",f"""
# 发布边界与局限（UTC {DATE}）

## 仓库访问
必须保持 GitHub **Private**，先审核课题组成员，再逐人授予必要权限。私有仓库不保证第三方素材拥有再许可权；研究组不应公开传播 B站字幕、豆瓣影评、网站用户身份或完整抓包。

## 已故意排除
- 每个项目的 .git、.env、Cookie、密钥、证书、browser Profile、各种本机日志、临时构建、虚拟环境与历史系统缓存；
- Facebook `runs` 原始 JSONL/完整 MITM HTTP Flow、Android 登录会话、证书、APK、Frida 二进制、完整用户个人信息；
- Keep 原始用户/动态 JSON、昵称、头像、位置、动态/评论正文，发布字段最小化的匿名 JSON 和匿名三表；
- Amazon 原始 ZIP/HTML 页面存档、网页抓取时产生的登录 Cookie；
- 任何在源码静态扫描中被识别为仍可能含长字符串凭据的文件；详见 `metadata/build-summary.json` 的排除计数。

## 重要的真实数据质量限制
- 一品威客 1,997 条中有 376 条使用 sitemap 更新日期作为**候选**时间；不是详情发布日。
- 猪八戒有 7,082 条记录，但未真正抓取详情，描述主要来自列表。
- BOSS 原始岗位 ID 断点投影不是完整网页原件；前程无忧最终层的对应完整原始库未恢复。
- B站不同结果文件重叠且有缺字幕；先按 CID 关联。
- Amazon Jiimore 商品数量与 SellerSprite、Keepa 时间序列覆盖不同，缺失量必须报告。
- Facebook 由于页面和账号可见性而有历史 failed/cancelled；completed 不一定代表全量可见。
- Keep 第二阶段导出不能将评论和动态视为同一种计量单位。
- 豆瓣目前只有 6 部电影历史样本。

## 密钥保护
不要使用从旧脚本、Git 提交或聊天中暴露过的真实密码；请轮换，再在个人本机环境变量中配置。此总仓库不保存身份/凭据映射盐，匿名导出不能逆向恢复原身份。

## 未声称
不声称在 {DATE[:10]} 重新访问或采集任何外部网站；不声称旧脚本全部适配当前网页/API；不声称历史数据许可允许公开分发。
""")
    write_if_missing("docs/DATA_GUIDE.md","""
# 数据目录与字典使用方法

请先访问各项目 README，再根据 `metadata/data-manifest.json` 的文件级 SHA-256、原始路径与脱敏说明核对数据。

字段含义：
- `record_id`：平台内部记录 ID，用来溯源与去重；不同网站的 ID 不能直接比较。
- `site_name` / `content_category`：来源站点、需求/岗位类型，先过滤再比较价格。
- `description_raw`：采集当时的描述，猪八戒以列表摘要为主。
- `published_at_raw`：原始发布日期，不能用 sitemap 更新时间伪造。
- `manual_review_grade`：一品威客质量等级 A/B/C/D。
- `CID` / `BV号`：B站视频相关标识，字幕按 CID 归并。
- `ASIN` + `日期` / `月份`：电商商品与时间序列联合键。
- `source_post_key` / `actor_key`：仅在本次 Facebook 导出中可关联的不可逆假名；不含原始用户 ID。
- `profile_id`、`post_id`、`comment_id`：Keep 脱敏第一阶段与第二阶段之间可关联的 ID。

大数据存储：`.gitattributes` 对数据表使用 Git LFS；按 `git lfs pull` 下载实际文件。验证 `python tools/verify_release.py` 将检测 hash（LFS 尚未拉取时需先 pull）。
""")
    write_if_missing("docs/RESEARCH_WORKFLOW.md","""
# 课题组使用流程

1. 项目负责人将成员按最小权限加入 Private 仓库；不得复制他人的浏览器登录状态。
2. 克隆仓库并运行 `git lfs pull`、`python tools/verify_release.py`。
3. 先选一个平台阅读该站 README，然后在对应 `source/` 中建立虚拟环境、安装依赖、执行离线测试。
4. 与网站通信的运行参数（账号、代理、配额、日期窗口）只写入本机独立配置，不上传。
5. 研究时先按 `record_id`、CID、ASIN/时间等正确主键合并，记录缺失与样本选择规则。
6. 分享研究成果时只提交脱敏数据、新生成的统计脚本、README 更新以及 `metadata` 校验；用户身份/原始抓包继续由获批研究者在本机保管。
7. 加入/更新大型数据：`git lfs track "projects/**/*.csv"`（仓库已配置）、`git add`、`git commit`、`git push`。源文件哈希清单应同时更新。
""")
    # normalize_keep.py is version-controlled; never regenerate over it.
    # verify_release.py is maintained separately; do not overwrite it.
    write(".gitattributes","""
# Git LFS: all research tables and binary research sheets.
projects/**/*.csv filter=lfs diff=lfs merge=lfs -text
projects/**/*.jsonl filter=lfs diff=lfs merge=lfs -text
projects/**/*.xlsx filter=lfs diff=lfs merge=lfs -text
projects/**/*.xls filter=lfs diff=lfs merge=lfs -text
projects/**/*.parquet filter=lfs diff=lfs merge=lfs -text
projects/**/*.sqlite filter=lfs diff=lfs merge=lfs -text
projects/**/*.sqlite3 filter=lfs diff=lfs merge=lfs -text
*.zip filter=lfs diff=lfs merge=lfs -text
""")
    write(".gitignore","""
.env
.env.*
!.env.example
**/.env
**/.env.*
**/node_modules/
**/.venv/
**/venv/
**/__pycache__/
**/.pytest_cache/
**/.mypy_cache/
**/.cache/
**/dist/
**/build/
**/runs/
**/api-jobs/
**/api-batches/
**/*.mitm
**/*.har
**/*.pcap
**/*.pcapng
**/*.pem
**/*.key
**/*credential*
**/*cookie*
**/*secret*
**/*password*
**/*profile-data*
**/browser-data/
**/Default/
**/*.apk
**/*.keystore
**/*.log
**/*local.json
**/raw-private/
**/.crawler-state/
""")
    write(".github/workflows/structure-check.yml","""
name: Structure and security gate
on:
  push:
  pull_request:
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          lfs: false
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: python tools/verify_release.py --manifest-only
""")

def main():
    if OUT.parent != DESKTOP:
        raise SystemExit("This build script is intended for the project created under Desktop.")
    if not (MARKET/"drv_crawler/sites.py").exists():
        raise SystemExit("Original market crawler source missing.")
    # Shared market crawler sources: copy core and test modules; no workstation cache.
    market_dest="projects/market-platform"
    for f in sorted(MARKET.iterdir()):
        if f.is_file() and f.suffix.lower() in {".py",".toml",".yaml",".yml",".txt",".md",".ps1",".lock"}:
            if f.name.startswith(".") or re.search("proxy|ip_pool|rollback|packet|smoke|camoufox",f.name,re.I):continue
            original_rel = "docs/ORIGINAL_README.md" if f.name == "README.md" else f.name
            copy_source(f,market_dest+"/"+original_rel)
    copy_source_tree(MARKET/"drv_crawler",market_dest+"/drv_crawler",excluded={"__pycache__","data-runtime","output","profile"})
    copy_source_tree(MARKET/"tools",market_dest+"/tools",excluded={"data","outputs","logs","private"})
    copy_source_tree(MARKET/"tests",market_dest+"/tests",excluded={"__pycache__","fixtures-private"})
    for f in ["docs/招聘数据说明.md","docs/招聘爬取失败原因复盘.md","docs/一品威客补充版交接说明.md","docs/第三方爬虫方案.md"]:
        copy_source(MARKET/f, market_dest+"/"+f)
    # Outsourcing: canonical datasets plus validated reviewed subsets.
    data_files={
     "projects/outsourcing/epwk/data/legacy-2691.csv":"outputs/外包--完整数据/一品威客/epwk/records.csv",
     "projects/outsourcing/epwk/data/reviewed-1997.csv":"outputs/epwk-current-3m-20260820-v3/最终交付/01_全量人工审核_1997.csv",
     "projects/outsourcing/epwk/data/high-quality-ab-1200.csv":"outputs/epwk-current-3m-20260820-v3/最终交付/02_高质量主数据_AB_1200.csv",
     "projects/outsourcing/epwk/data/price-high-confidence-956.csv":"outputs/epwk-current-3m-20260820-v3/最终交付/03_价格建模优先_高中置信度_956.csv",
     "projects/outsourcing/epwk/data/price-core-920.csv":"outputs/epwk-current-3m-20260820-v3/最终交付/04_价格建模核心_高中置信度且暂排重复候选_920.csv",
     "projects/outsourcing/epwk/data/strong-pricing-408.csv":"outputs/epwk-current-3m-20260820-v3/最终交付/05_强价格样本_高置信度_408.csv",
     "projects/outsourcing/epwk/data/low-pricing-confidence-244.csv":"outputs/epwk-current-3m-20260820-v3/最终交付/06_保留但低价格置信度_244.csv",
     "projects/outsourcing/epwk/data/duplicate-candidates-51.csv":"outputs/epwk-current-3m-20260820-v3/最终交付/07_潜在重复候选_51.csv",
     "projects/outsourcing/epwk/data/missing-description-6.csv":"outputs/epwk-current-3m-20260820-v3/最终交付/08_主数据描述缺失_6.csv",
     "projects/outsourcing/epwk/data/compact-ab-1200.csv":"outputs/epwk-current-3m-20260820-v3/最终交付/10_最终精简版_9列_1200.csv",
     "projects/outsourcing/zbj/data/records.csv":"outputs/外包--完整数据/猪八戒/zbj/records.csv",
     "projects/outsourcing/shixian/data/records.csv":"outputs/外包--完整数据/实现网/records.csv",
     "projects/recruitment/boss/data/final.csv":"outputs/招聘--最终结果/BOSS/岗位数据.csv",
     "projects/recruitment/job51/data/final.csv":"outputs/招聘--最终结果/前程无忧/岗位数据.csv",
     "projects/recruitment/liepin/data/final.csv":"outputs/招聘--最终结果/猎聘/岗位数据.csv",
     "projects/recruitment/liepin/data/full-source.csv":"outputs/招聘--完整数据/猎聘/岗位数据.csv",
    }
    for dest,source in data_files.items():save_data(MARKET/source,dest,data_kind=dest.split("/")[1])
    ids=export_boss_inventory()
    # Keep two stages: pseudonymized capture responses + normalized relational tables.
    copy_source_tree(KEEP/"动态爬取","projects/social/keep/source/phase2",extensions={".py"},excluded={"chenggong1"})
    copy_source(KEEP/"参加比赛.txt","projects/social/keep/source/context.txt")
    keep_stats=export_keep()
    # Douban versions and private data copy.
    copy_source_tree(DOUBAN,"projects/social/douban/source/fcrs2",extensions={".py",".cfg",".md"})
    copy_source_tree(DOUBAN_OLD,"projects/social/douban/source/course-legacy",extensions={".py",".cfg",".md"})
    export_douban()
    # Bilibili subtitle, topic and notebook sources.
    copy_source_tree(BLIB,"projects/social/bilibili/source/main",extensions={".py",".ipynb"})
    copy_source_tree(BLIB_EXTRA,"projects/social/bilibili/source/supplement",extensions={".py",".ipynb"})
    for f in sorted(BLIB.rglob("*.csv")):
        save_data(f,"projects/social/bilibili/data/"+f.relative_to(BLIB).as_posix(),data_kind="bilibili")
    for f in sorted(BLIB_EXTRA.rglob("*")):
        if f.suffix.lower() not in (".csv",".xlsx"):continue
        save_data(f,"projects/social/bilibili/data/supplement/"+f.relative_to(BLIB_EXTRA).as_posix(),data_kind="bilibili")
    # Facebook: only source-controlled release-file whitelist; no runtime artifacts.
    entries=(FACEBOOK/"release-files.txt").read_text(encoding="utf-8-sig",errors="replace").splitlines()
    for line in entries:
        item=line.strip()
        if not item or item.startswith("#") or item.startswith("- ") or item.startswith("!"):continue
        if item.startswith("/") or ".." in Path(item).parts:continue
        if re.search(r"(?:cookie|password|credential|private[_-]?key|[.]env)",item,re.I):continue
        if Path(item).suffix.lower() not in {".py",".md",".json",".toml",".txt",".ps1",".sh",".cmd",".bat",".js",".html",".css",".yml",".yaml",".xml",".java",".gitignore",".gitattributes"} and Path(item).name not in (".gitignore",".gitattributes"):continue
        copy_source(FACEBOOK/item,"projects/social/facebook/source/"+item)
    fb_stats=export_facebook()
    # Amazon + Jiimore is one codebase, preserving current and legacy versions.
    for source,sub in [(AMAZON/"成功","source/current"),(AMAZON/"新建文件夹","source/legacy"),(AMAZON,"source/misc"),
                       (AMAZON_EXTRA/"获取asin","source/supplement/get-asin")]:
        for f in sorted(source.glob("*.py")):
            copy_source(f,"projects/commerce/amazon-jiimore/"+sub+"/"+f.name)
    aa={
      "projects/commerce/amazon-jiimore/data/amazon/products.csv":"新建文件夹/Data/amazon/goods_infos.csv",
      "projects/commerce/amazon-jiimore/data/amazon/tags.csv":"新建文件夹/Data/amazon/customers_say_tags.csv",
      "projects/commerce/amazon-jiimore/data/jiimore/weekly_clicks.csv":"成功/Data/jiimore/jiimore.csv",
      "projects/commerce/amazon-jiimore/data/jiimore/asin.csv":"成功/Data/jiimore/asin.csv",
      "projects/commerce/amazon-jiimore/data/sellersprite/monthly_sales.csv":"成功/Data/sellersprite/monthly_sales.csv",
      "projects/commerce/amazon-jiimore/data/sellersprite/monthly_reviews.csv":"成功/Data/sellersprite/reviews.csv",
      "projects/commerce/amazon-jiimore/data/sellersprite/keepa_history.csv":"新建文件夹/Data/sellersprite/keepa.csv",
    }
    for target,origin in aa.items():save_data(AMAZON/origin,target,data_kind="amazon-jiimore")
    generate_project_docs({"keep":keep_stats,"facebook":fb_stats})
    TRACKED.sort(key=lambda x:x["path"]);SOURCES.sort(key=lambda x:x["path"])
    write("metadata/data-manifest.json",json.dumps({"created_at_utc":DATE,"schema_version":1,"files":TRACKED},ensure_ascii=False,indent=2))
    write("metadata/source-manifest.json",json.dumps({"created_at_utc":DATE,"schema_version":1,"files":SOURCES},ensure_ascii=False,indent=2))
    summary={
      "created_at_utc":DATE,"source_files_copied":COPIED,"data_files_prepared":len(TRACKED),
      "data_bytes_total":sum(v["bytes"] for v in TRACKED),"exclusions_count_by_reason":dict(EXCLUDED),
      "keep":keep_stats,"facebook":fb_stats,"boss_recovered_ids":ids,
      "publication_policy":"private, redacted research use only",
      "original_files_unchanged":True
    }
    write("metadata/build-summary.json",json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps({"build_complete":True,"summary":summary},ensure_ascii=True),flush=True)

if __name__=="__main__":
    main()
