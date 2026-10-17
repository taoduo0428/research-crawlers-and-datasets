# BOSS Zhipin Scraper · Job Crawler v2.2 (Chrome CDP / Plaintext Salary)

Archived upstream English instructions for the separate BOSS CDP tool. The commands below refer to the original project and its Hermes integration, not necessarily to the structure of this research monorepo. Read the [Chinese project note](README.md) first if you are working in the lab repository.
> 🌐 中文文档：[README.md](./README.md)

![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)
![Version](https://img.shields.io/badge/version-2.2.1-orange.svg)

A lightweight **BOSS Zhipin scraper / crawler** (a.k.a. spider) for job listings on [zhipin.com](https://www.zhipin.com). Instead of driving a heavy Selenium/Playwright browser, it connects to your **already-logged-in Chrome** via the Chrome DevTools Protocol (CDP), reuses the real session, and calls the in-page search API directly — bypassing the front-end font-based anti-scraping so you get the **plaintext salary** in every record. Output goes to JSON / CSV, plus an aggregated salary/skill analysis and a copy-paste prompt for polishing your job-application materials. Also ships as a Hermes Agent Skill.

> 📌 **In one sentence**: no Selenium/Playwright — connect to your logged-in Chrome over CDP, hit the search API with the real session, get JSON/CSV with plaintext salaries, plus salary-distribution, skill-frequency stats and a résumé-optimization prompt.

---

## ⚠️ Disclaimer

This project is for **learning and technical research purposes only**. It is intended to explore Chrome DevTools Protocol, front-end anti-scraping mechanisms, and data-collection techniques. Do **not** use it for any purpose that violates the [BOSS Zhipin Terms of Service](https://www.zhipin.com/about/protocol.html) or applicable laws and regulations, including commercial resale, malicious scraping, or any activity that imposes undue load on the target site. Users are solely responsible for the consequences of using this project; the author is not liable for any misuse.

---

## 🚀 30-Second Quick Start

```bash
# 1. Clone + install deps
git clone https://github.com/eatmoreduck/boss-zhipin-scraper.git
cd boss-zhipin-scraper
pip install -r requirements.txt          # or: uv sync

# 2. Launch an isolated Chrome and log in (only once; session persists)
python3 scripts/boss_cdp_raw.py --setup-chrome

# 3. Scrape + analyze
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --analysis

# Cities nationwide are supported (incl. tier-3/4/5), e.g.:
python3 scripts/boss_cdp_raw.py --keyword "前端" --city 赣州 --pages 3
# List supported cities: --list-cities [keyword]
python3 scripts/boss_cdp_raw.py --list-cities 江

# 4. Generate an aggregated summary + prompt after scraping (reads the latest result)
python3 scripts/job_summary.py
```

Right after scraping you get: salary ranges, experience requirements, top skill keywords, and a job-application optimization prompt. The prompt is based solely on the scraped job data — it never reads your local résumé file and never scores personal-job match.

## ✨ Features

- Plaintext salary (API mode, bypasses font-based obfuscation)
- Dual JSON / CSV output
- Detail-page JD scraping + skill analysis
- Aggregated summary + copy-paste prompt after scraping
- Incremental writes (no data loss on crash)
- One-shot environment check + persistent isolated Chrome CDP profile
- Multi-dimension filters (scale, funding, salary, experience, degree, industry)
- macOS + Linux support (a Windows code path is reserved but untested — not guaranteed to work)

<details>
<summary>🔍 Why not a Selenium / Playwright crawler?</summary>

- Selenium/Playwright spins up a full instrumented browser — it's heavy, has an obvious fingerprint, and is easily flagged by BOSS Zhipin's risk-control / CAPTCHA.
- This tool connects to your own already-logged-in Chrome (via CDP), reusing a real fingerprint and session, and calls the same legitimate search API the page uses. The `salaryDesc` it returns is already plaintext — no need to parse font-obfuscated DOM salaries.
- The result is more stable than traditional DOM-scraping crawlers and harder to flag as automated traffic.

</details>

## Installation

### Option 1: Clone then install locally (recommended)

Because `hermes skills install` may not reach GitHub directly in some environments, clone the repo first and install locally:

```bash
# 1. Clone the repo
git clone https://github.com/eatmoreduck/boss-zhipin-scraper.git
cd boss-zhipin-scraper

# 2. Copy into the Hermes skills directory
mkdir -p ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts
cp SKILL.md ~/.hermes/skills/data-science/boss-zhipin-scraper/
cp scripts/boss_cdp_raw.py ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts/
cp scripts/job_summary.py ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts/
```

### Option 2: One-line curl install

No need to clone the whole repo — download just the files you need:

```bash
mkdir -p ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts && \
curl -sL https://raw.githubusercontent.com/eatmoreduck/boss-zhipin-scraper/master/SKILL.md \
  -o ~/.hermes/skills/data-science/boss-zhipin-scraper/SKILL.md && \
curl -sL https://raw.githubusercontent.com/eatmoreduck/boss-zhipin-scraper/master/scripts/boss_cdp_raw.py \
  -o ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts/boss_cdp_raw.py && \
curl -sL https://raw.githubusercontent.com/eatmoreduck/boss-zhipin-scraper/master/scripts/job_summary.py \
  -o ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts/job_summary.py
```

### Option 3: `hermes skills install` (requires direct GitHub access)

```bash
hermes skills install https://raw.githubusercontent.com/eatmoreduck/boss-zhipin-scraper/master/SKILL.md --category data-science
```

> Note: this depends on the hermes process being able to reach GitHub directly. If you hit a timeout or connection failure, use Option 1 or 2.

### Verify the installation

```bash
# Check that the files exist
ls ~/.hermes/skills/data-science/boss-zhipin-scraper/SKILL.md
ls ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts/boss_cdp_raw.py
ls ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts/job_summary.py
```

After installing, just say in a Hermes conversation: "Search BOSS Zhipin for AI Agent jobs in Shanghai."

## Use as a CLI tool

You don't have to install it as a Skill — use it as a plain CLI:

```bash
# 1. Clone + install deps
git clone https://github.com/eatmoreduck/boss-zhipin-scraper.git
cd boss-zhipin-scraper
pip install -r requirements.txt

# 2. Start Chrome CDP
python3 scripts/boss_cdp_raw.py --setup-chrome
# First run won't copy your main Chrome session; log in to zhipin.com in the dedicated BOSS browser that pops up
# setup waits for login to finish and confirms the API returns plaintext salaries

# 3. Check the environment
python3 scripts/boss_cdp_raw.py --check

# Optional: real browser/API smoke test (writes no result files)
python3 scripts/boss_cdp_raw.py --smoke-test

# 4. Scrape
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --format csv --analysis

# Keep only jobs whose raw salary contains an explicit non-zero digit
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --numeric-price-only --no-detail

# Supplement real job-description text for an existing JSONL list; both files resume safely
python3 scripts/boss_cdp_raw.py --input jobs.jsonl --detail --max-details 500 \
  --detail-output details.jsonl --detail-checkpoint details_checkpoint.jsonl

# Cover every official third-level occupation, keeping at most 5 jobs per code;
# rerun the same command to resume from the atomic checkpoint
python3 scripts/boss_cdp_raw.py --all-positions --city 全国 --pages 3 \
  --per-position-limit 5 --numeric-price-only --output boss_jobs.json

# Bounded smoke: run only the first 3 pending codes; rerun to continue
python3 scripts/boss_cdp_raw.py --all-positions --max-positions 3 --pages 1 \
  --per-position-limit 1 --output boss_smoke.json

# Keep at most 50 jobs per unique occupation; pagination may continue until
# quota, hasMore=false, or the existing hard limit of 10 pages
python3 scripts/boss_cdp_raw.py --all-positions --pages 1 \
  --per-position-limit 50 --output boss_position_jobs.json

# 5. Summary + prompt after scraping
python3 scripts/job_summary.py --top 15
```

## Parameters

| Parameter | Description |
|-----------|-------------|
| `--keyword` | Search keyword (default "AI Agent") |
| `--city` | City (Chinese name or code, default Shanghai). **Supports cities nationwide** (300+, incl. tier-3/4/5); city codes auto-sync from BOSS at runtime. See [`data/city_codes.json`](data/city_codes.json), or run `--list-cities` |
| `--list-cities [keyword]` | Print the supported city list, optional keyword filter, e.g. `--list-cities 江` |
| `--list-positions [keyword]` | Print official third-level occupation codes, optionally filtered by code or category path |
| `--pages` | Pages in normal mode; single-page position targets keep this budget, while multi-page targets such as 50 may continue until quota, `hasMore=false`, or the 10-page hard limit |
| `--format` | json / csv; csv also exports list and detail CSVs |
| `--detail` | Scrape the real detail-page job description (on by default) |
| `--no-detail` | Do not scrape detail pages |
| `--max-details` | Maximum unfinished details for this invocation; bounded by the 500-request run limit by default |
| `--detail-output` | Detail output; use `.jsonl` for large runs so each success is appended and fsynced |
| `--detail-checkpoint` | JSONL status log for success, failure, expired login, or verification pages; derived from the detail output by default |
| `--detail-load-timeout` | Bounded render wait per detail in seconds (default 15) |
| `--detail-retries` | Retries after one detail fails (default 1) |
| `--analysis` | Analysis report |
| `--merge FILE` | Merge existing data (deduped by job_id) |
| `--all-positions` | Sample every official third-level occupation code; duplicate taxonomy paths for one code are requested once |
| `--position CODE` | Sample only a specific third-level occupation code; repeatable |
| `--per-position-limit` | Maximum jobs kept per occupation (default 5, max 100) |
| `--max-positions` | Limit only this invocation's pending occupations for smoke/batches; does not change the coverage denominator |
| `--category-interval-seconds` | Fixed delay between occupation categories (default 12 seconds) |
| `--category-jitter-seconds` | Maximum random jitter added to the fixed delay (default 8, for a 12-20 second interval) |
| `--block-cooldown-seconds` | First cooldown after a site restriction (default 3600 seconds; repeated blocks back off exponentially to 14400; `Retry-After` takes priority) |
| `--checkpoint` | Position-sampling checkpoint path; derived from `--output` by default |
| `--coverage-output` | Position coverage report path; derived from `--output` by default |
| `--numeric-price-only` | Keep only jobs whose raw salary contains `[1-9]`; multi-page position targets may continue within the 10-page hard limit to find enough qualifying records |
| `--allow-dom-fallback` | Allow DOM extraction fallback when the API has no data; off by default, salaries may be unreliable |
| `--check` | Environment check (CDP + deps + login state) |
| `--smoke-test` | Run one real Chrome/CDP BOSS search API smoke test, writes no result files |
| `--setup-chrome` | One-shot launch of Chrome CDP (persistent isolated profile) |
| `--copy-login-state` | Manually import the main Chrome's Local State + cookie-related files into the isolated profile (never copied by default, on first run, or on repeated runs) |
| `--reset-chrome-profile` | Rebuild the dedicated BOSS Chrome profile; clears the login state inside this dedicated browser |
| `--no-wait-login` | With `--setup-chrome`, do not wait for login to finish |
| `--login-timeout` | Seconds to wait for login under `--setup-chrome` (default 300) |
| `--stop-chrome` | Close the dedicated BOSS CDP Chrome (matched precisely by the isolated profile; never touches your main Chrome) |
| `--close-chrome` | Auto-close the dedicated Chrome after a scrape finishes normally (off by default; not triggered on errors, so the login state is kept) |
| `--output` | List output path; normal mode uses a timestamped file, while position sampling uses the stable `~/.boss-zhipin-scraper/job-result/boss_position_jobs.json` by default for resumability |
| `--cdp-port` | CDP port (default 9222) |
| `--scale/--salary/--experience/--degree` | Filters |

## Post-Scrape Summary & Prompt

`scripts/job_summary.py` only reads the already-scraped `boss_jobs_*.json` and `boss_details_*.json`, does simple aggregation, and produces a copy-paste prompt. It never reads your local résumé file, pulls in no PDF dependency, and never scores a person against a job.

```bash
# Read the newest boss_jobs_*.json under the default result dir and auto-match the same-timestamp or newest detail file
python3 scripts/job_summary.py

# Specify list and detail files
python3 scripts/job_summary.py \
  --input ~/.boss-zhipin-scraper/job-result/boss_jobs_20260625_1200.json \
  --details ~/.boss-zhipin-scraper/job-result/boss_details_20260625_1200.json \
  --top 15

# Only emit the prompt
python3 scripts/job_summary.py --prompt-only
```

After installing the package you can also use the entry command:

```bash
uv run boss-summary --top 15
```

The summary covers: salary ranges, experience requirements, degree requirements, regional distribution, top companies, skill tags, frequent JD terms. The prompt asks the model to use these stats to fill in résumé keywords, suggest project-story rewrite directions, and produce an interview-prep checklist — while explicitly instructing it not to fabricate experience.

## File Structure

```
boss-zhipin-scraper/
├── SKILL.md              # Hermes Skill definition
├── README.md             # Chinese docs
├── README.en.md          # English docs
├── CHANGELOG.md
├── LICENSE
├── pyproject.toml
├── scripts/
│   ├── boss_cdp_raw.py   # Main scraping script
│   └── job_summary.py    # Post-scrape summary + prompt
└── requirements.txt
```

## How It Works

This is a Chrome-CDP-based BOSS Zhipin crawler. Core flow:

1. Connect to an already-open Chrome via the Chrome DevTools Protocol (CDP)
2. Regular keyword mode calls the search API in-page; position sampling and the pre-run login check navigate to real search pages and read the page's own response through CDP Network, without injecting a duplicate first-page XHR
3. The API returns plaintext `salaryDesc`, bypassing the front-end font obfuscation
4. The list API preserves `securityId` / `lid` context, carried into the detail page
5. Each page is written to disk immediately, deduped by `job_id`

Position sampling first reads BOSS's official three-level occupation tree from `getCityShowPosition`, then navigates search pages with each third-level code as the `position` parameter. Every page consumes that page's own `joblist.json` response, keeping the page URL, Referer, cookies, and site-script context aligned instead of staying on the first occupation page while injecting cross-occupation XHRs. One third-level code can occur under several first/second-level paths. The scraper requests that code once, writes its primary path to `category_level_1_raw`, `category_level_2_raw`, `category_level_3_raw`, and `category_path_raw`, and preserves every official alias in `category_paths_raw` and `category_matches`. Job titles are never used to guess categories. Each sampled job also exposes the shared-project fields `site_name`, `content_category`, `title_raw`, `price_evidence`, `list_url`, `detail_url`, and `record_id`, while retaining the original `title`, `salary`, and `job_link` fields.

After each occupation, job data is atomically replaced before the checkpoint and coverage report are advanced. Re-running the same command skips completed codes. A checkpoint whose parameters differ is rejected to avoid mixing datasets. `--max-positions N` only takes the first N currently pending codes for this invocation and is not part of the dataset definition, so repeated batches keep the full catalog as their coverage denominator. The default delay is 12-20 seconds between categories. A site restriction such as `code: 37` or HTTP 403/429 stops the run immediately without a retry and persists the block time, attempt count, and cooldown deadline. If the login probe is restricted before an occupation catalog exists, the scraper writes only an `awaiting_catalog` bootstrap checkpoint, never an empty catalog, empty output, or coverage report. After cooldown and a successful login probe, position sampling loads the official catalog and resumes. An existing full checkpoint only receives an atomic restriction update and retains all progress. A rerun during cooldown opens no CDP session and sends no request; standalone `--check` only reports status and never writes a collection checkpoint. `Retry-After` takes priority when present; otherwise the first cooldown is one hour and repeated blocks back off exponentially up to four hours. Restricted occupations and other non-terminal errors enter `deferred` and are placed after every unattempted occupation on resume. An ordinary category error becomes terminal `failed` only after three attempts across runs; it is then removed from the request queue and cannot block other occupations. Existing schema-1 checkpoints are upgraded in memory without discarding progress. By default the run creates `*_checkpoint.json` and `*_coverage.json`; the report counts mutually exclusive `completed`, `pending`, `deferred`, and `failed` codes and paths, plus first-level summaries and per-code sample counts. Detail scraping is off by default for position sampling and runs only when `--detail` is explicit.

A target of 50 follows the site's native pagination. If numeric-salary filtering or cross-page duplicates leave it short, low-frequency page navigation continues until quota, `hasMore=false`, or the 10-page hard limit. Coverage summarizes `quota_met`, `exhausted_below_quota`, `empty`, `page_limit_below_quota`, and `failed` from `records_fetched` and `stop_reason`. An occupation being processed therefore does not imply that its 50-record target was met, and site exhaustion is never conflated with page-budget exhaustion.

DOM extraction is not used for the list by default, since DOM salaries may be hit by font-based obfuscation. Only when `--allow-dom-fallback` is explicitly passed will it fall back to DOM when the API returns no data.

With `--numeric-price-only`, a record is kept only when its raw salary contains at least one non-zero digit `[1-9]`, such as `20-40K` or `300k-500k`. Text-only values such as `negotiable`, `quote pending`, or `0` are removed. The same rule is applied page by page, when loading `--input`, and after `--merge`; JSON output and the terminal summary include `filtered_out_count`. Rejected records never trigger extra pages, so the final count may be lower than the raw number of jobs in the requested pages.

For detail pages, the scraper only extracts a section containing the job-description heading. Full-page `body` text is diagnostic input for detecting login walls and navigation shells and is never written directly as a JD. If the page contains the login-to-view-full-content marker, the crawl fails explicitly and stops before truncated text, recruiter metadata, company sections, or recommended jobs can be saved as a complete JD. For a large existing list, use `--input ...jsonl --detail --detail-output ...jsonl`: one CDP detail tab is reused, successful identities are skipped on resume, each success is fsynced to JSONL, and login/verification/parse failures are recorded in the separate checkpoint log.

`--input ... --analysis --no-detail` first loads `--detail-output`, then the `boss_details_*.json` with the same timestamp in the same dir as the input list, and finally the newest detail file under `~/.boss-zhipin-scraper/job-result`.

## Chrome Profile Security Policy

`--setup-chrome` uses a persistent isolated profile by default — it neither symlinks nor copies your main Chrome data. First launch and subsequent launches only create or reuse this dedicated profile:

- `~/.boss-zhipin-scraper/chrome-profile`

Without an explicit `--output` or `--detail-output`, scraping results are saved under:

- `~/.boss-zhipin-scraper/job-result`

On first use you must log in to BOSS Zhipin manually inside this dedicated Chrome. `--setup-chrome` waits for the login to finish and uses the search API to confirm it can get plaintext `salaryDesc` before returning. The session is stored inside the dedicated profile and survives reboots; re-running `--setup-chrome` does not wipe it and does not affect your main Chrome, Gmail, GitHub, or other accounts.

Each login-probe round sends one search request, rotates across keyword/city targets, and backs off from 3 seconds to at most 15 seconds. Probe requests count toward the same 500-request global budget. Logged-out sessions, empty probe samples, API restrictions, and malformed responses are reported separately. A confirmed restriction such as `code: 31` or `code: 37` stops probing immediately. Unknown response codes are also treated as restricted when their messages indicate frequent access, security checks, slider verification, or similar risk controls, instead of prompting for another login or continuing frequent retries.

The interactive login page opened by `--setup-chrome` is brought to the foreground. The pre-run login check and position sampling also activate one dedicated search tab so visibility and `document.hasFocus()` retain normal browser semantics. A whole position batch reuses that single tab rather than opening or activating one per occupation. Regular list and detail temporary tabs still run in the background.

If you really need to import the BOSS session from your main Chrome, run explicitly:

```bash
python3 scripts/boss_cdp_raw.py --setup-chrome --copy-login-state
```

`--copy-login-state` overwrites the corresponding cookie-related files inside the isolated profile on every run; do not pass this for daily launches. It only copies `Local State` and `Default/Cookies*`, `Default/Network/Cookies*`-style cookie database files — not password stores, history, extensions, or a full profile. To wipe the dedicated browser's login state:

```bash
python3 scripts/boss_cdp_raw.py --setup-chrome --reset-chrome-profile
```

### Tearing down when you're done

After a scrape/analysis finishes, the dedicated Chrome is **not** closed automatically (the login state is kept by default so you can run the next scrape right away). When you're sure you no longer need it, tear it down manually:

```bash
python3 scripts/boss_cdp_raw.py --stop-chrome
```

`--stop-chrome` only closes the Chrome process(es) that belong to the scraper's isolated profile (`--user-data-dir`). It **never** kills by port or process name, so it cannot accidentally take down your main Chrome, Gmail, GitHub, or other signed-in sessions.

If you'd rather have a particular scrape close the dedicated Chrome once it finishes normally, add `--close-chrome`:

```bash
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --close-chrome
```

`--close-chrome` is off by default, and it only fires on the **success path** of a completed scrape — login failures, crashes, and other early exits leave the Chrome running so the login state is preserved.

## 📌 TODO

- [ ] Strengthen the detail-page `Referer` and request fingerprinting to further reduce risk-control triggers

## License

MIT

## Friends

- [LINUX DO](https://linux.do/) — A sincere, friendly, and vibrant tech community. This project endorses and recommends it.

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=eatmoreduck/boss-zhipin-scraper&type=Date)](https://star-history.com/#eatmoreduck/boss-zhipin-scraper&Date)
