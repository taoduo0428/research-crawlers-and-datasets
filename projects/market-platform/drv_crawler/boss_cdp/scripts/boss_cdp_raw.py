#!/usr/bin/env python3
"""
BOSS直聘职位抓取 + 分析 — 纯 CDP raw protocol

功能:
  1. 搜索特定职位 (关键词 + 城市)
  2. 筛选公司规模、融资阶段、薪资范围、经验、学历、行业
  3. 抓取详情页 JD 并分析薪资范围和技能要求
  4. 输出结构化 JSON + CSV + 终端分析报告
  5. 环境检查、Chrome CDP 自动启动、登录状态检测
  6. 按 BOSS 官方三级职业分类限量采样、断点续跑并输出覆盖报告

用法:
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --city 101020100 --pages 5
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --scale 305 --salary 406
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --analysis
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --detail
  uv run python3 scripts/boss_cdp_raw.py --check
  uv run python3 scripts/boss_cdp_raw.py --setup-chrome
  uv run python3 scripts/boss_cdp_raw.py --version
"""

__version__ = "2.2.1"

import json
import time
import random
import sys
import argparse
import os
import re
import hashlib
import csv
import glob
import math
import base64
import platform
import subprocess
import shutil
import signal
import logging
import ntpath
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from collections import Counter, deque
from enum import Enum
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
from urllib.request import Request, urlopen

if platform.system() == "Windows":
    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name, None)
        if _stream is not None and hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

websocket = None
requests = None

# ============================================================
# 全局常量
# ============================================================

# CDP 默认端口（可通过 --cdp-port 覆盖）
DEFAULT_CDP_PORT = 9222

# API 基础路径（便于统一修改）
API_JOB_LIST_PATH = "/wapi/zpgeek/search/joblist.json"
HOT_CITY_URL = "https://www.zhipin.com/wapi/zpgeek/search/job/hot/city.json"
CITY_GROUP_URL = "https://www.zhipin.com/wapi/zpCommon/data/cityGroup.json"
POSITION_TREE_URL = "https://www.zhipin.com/wapi/zpCommon/data/getCityShowPosition"

# 请求频率保护
MAX_PAGES = 10          # 单次最大页数
MAX_API_REQUESTS = 500  # 单次最大 API 请求数
MAX_POSITION_CATEGORIES = 1200
MAX_POSITION_RUN_REQUESTS = 10000
MAX_PER_POSITION_LIMIT = 100
DEFAULT_PER_POSITION_LIMIT = 5
POSITION_API_PAGE_SIZE = 30
DEFAULT_POSITION_OUTPUT_FILENAME = "boss_position_jobs.json"
POSITION_CATALOG_RETRIES = 3
POSITION_PAGE_RETRIES = 2
POSITION_TAB_RECOVERY_RETRIES = 1
POSITION_CHECKPOINT_SCHEMA = 1
POSITION_EMPTY_CONFIRMATION_VERSION = 1
MAX_POSITION_CATEGORY_ERROR_ATTEMPTS = 3
POSITION_CATEGORY_SOURCE = "official_position_tree_query"
DEFAULT_POSITION_CATEGORY_INTERVAL_SECONDS = 12.0
DEFAULT_POSITION_CATEGORY_JITTER_SECONDS = 8.0
MAX_POSITION_CATEGORY_DELAY_SECONDS = 300.0
DEFAULT_POSITION_BLOCK_COOLDOWN_SECONDS = 3600.0
DEFAULT_POSITION_BLOCK_MAX_COOLDOWN_SECONDS = 14400.0
MAX_POSITION_BLOCK_COOLDOWN_SECONDS = 86400.0
POSITION_PAGE_DELAY_RANGE = (4.0, 8.0)
DEFAULT_POSITION_BATCH_SIZE = 0
DEFAULT_POSITION_BATCH_COOLDOWN_SECONDS = 0.0
DEFAULT_POSITION_MAX_CONSECUTIVE_ERRORS = 2
POSITION_NATIVE_RESPONSE_TIMEOUT_SECONDS = 30.0
MAX_CDP_EVENT_QUEUE = 2000
NONZERO_SALARY_DIGIT_RE = re.compile(r"[1-9]")

def get_default_chrome_path():
    system = platform.system()
    if system == "Darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if system == "Windows":
        candidates = []
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(ntpath.join(local_app_data, "Google", "Chrome", "Application", "chrome.exe"))
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env_name)
            if base:
                candidates.append(ntpath.join(base, "Google", "Chrome", "Application", "chrome.exe"))
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return candidates[0] if candidates else "chrome.exe"

    candidates = [
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/snap/bin/chromium",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def get_default_profile_dir():
    system = platform.system()
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = ntpath.join(os.path.expanduser("~"), "AppData", "Local")
        return ntpath.join(base, "Google", "Chrome", "User Data")
    return os.path.expanduser("~/.config/google-chrome")


DEFAULT_CHROME_PATH = get_default_chrome_path()
DEFAULT_PROFILE_DIR = get_default_profile_dir()

DEFAULT_CDP_DATA_DIR = os.path.expanduser("~/.boss-zhipin-scraper/chrome-profile")
DEFAULT_RESULT_DIR = os.path.expanduser("~/.boss-zhipin-scraper/job-result")
DEFAULT_CITY_INPUT = "上海"
LOGIN_PROBE_QUERY = "Java"
LOGIN_PROBE_CITY = "101020100"
LOGIN_PROBE_TARGETS = (
    ("Java", "101020100"),
    ("AI Agent", "101010100"),
    ("产品经理", "101280600"),
)
LOGIN_PROBE_PAGE_SIZE = 10
LOGIN_PROBE_MAX_INTERVAL = 15
LOGIN_PROBE_MAX_TRANSIENT_ERRORS = 2
LOGIN_RESTRICTED_CODES = {31, 32, 37}
LOGIN_RESTRICTED_MESSAGE_KEYWORDS = (
    "环境存在异常",
    "账户存在异常",
    "暂时被禁止使用",
    "访问频繁",
    "操作太频繁",
    "安全校验",
    "滑块",
    "验证",
)
DEFAULT_LOGIN_TIMEOUT = 300

# 全局请求计数器
_request_counter = 0
_live_city_maps_cache = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("boss_cdp")


def default_output_path(kind):
    filename = f"boss_{kind}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    return os.path.join(DEFAULT_RESULT_DIR, filename)


def require_runtime_dependencies(*names):
    global requests, websocket

    missing = []
    if "requests" in names and requests is None:
        try:
            import requests as requests_module
            requests = requests_module
        except ImportError:
            missing.append("requests")
    if "websocket" in names and websocket is None:
        try:
            import websocket as websocket_module
            websocket = websocket_module
        except ImportError:
            missing.append("websocket-client")
    if missing:
        print(f"缺少依赖: {' '.join(missing)}")
        print("请安装（任选其一）:")
        print(f"  uv add {' '.join(missing)}")
        print(f"  pip install {' '.join(missing)}")
        return False
    return True


# ============================================================
# 筛选参数映射
# Source snapshots:
# - 城市: https://www.zhipin.com/wapi/zpgeek/search/job/hot/city.json + cityGroup.json
# - 筛选项: https://www.zhipin.com/wapi/zpgeek/search/job/condition.json
# ============================================================
# 城市码表已外置到 data/city_codes.json（全量城市，覆盖一二三四五线），
# 见 issue #24。resolve_city 查询链：本地静态 → 运行时拉 BOSS 接口 → 原样兜底。
# 仓库内路径（开发态）与打包后路径（pip install）都在 _city_data_path() 里处理。
CITY_DATA_FILENAME = "city_codes.json"

_local_city_map_cache = None


def _city_data_path():
    """返回 data/city_codes.json 的路径，兼容仓库开发态与 pip 打包态。"""
    # 1. 仓库开发态：脚本在 scripts/，数据在 ../data/
    repo_data = os.path.join(os.path.dirname(__file__), "..", "data", CITY_DATA_FILENAME)
    if os.path.isfile(repo_data):
        return os.path.normpath(repo_data)
    # 2. 打包态：wheel force-include 到包根 data/，用 importlib.resources 兜底
    try:
        from importlib.resources import files  # py3.9+
        pkg_data = files(__package__ or "__main__").joinpath("..", "data", CITY_DATA_FILENAME) \
            if __package__ else None
    except Exception:
        pkg_data = None
    if pkg_data is not None and os.path.isfile(str(pkg_data)):
        return str(pkg_data)
    # 3. 找不到则返回开发态路径（让调用方决定降级）
    return os.path.normpath(repo_data)


def load_local_city_map():
    """读取本地 data/city_codes.json 静态全量城市码表。

    返回 (name_to_code, code_to_name) 两个字典；读取失败返回 ({}, {})。
    结果缓存，重复调用零开销。
    """
    global _local_city_map_cache
    if _local_city_map_cache is not None:
        return _local_city_map_cache
    name_to_code = {}
    try:
        path = _city_data_path()
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for name, code in raw.items():
                if name and code is not None:
                    name_to_code[str(name)] = str(code)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        log.debug(f"读取本地城市码表失败: {e}")
    code_to_name = {code: name for name, code in name_to_code.items()}
    _local_city_map_cache = name_to_code, code_to_name
    return _local_city_map_cache

SCALE_MAP = {
    "0-20人": "301", "20-99人": "302", "100-499人": "303",
    "500-999人": "304", "1000-9999人": "305", "10000人以上": "306",
}

STAGE_MAP = {
    "未融资": "801", "天使轮": "802", "A轮": "803", "B轮": "804",
    "C轮": "805", "D轮及以上": "806", "已上市": "807", "不需要融资": "808",
}

SALARY_MAP = {
    "不限": "0", "3K以下": "402", "3-5K": "403", "5-10K": "404",
    "10-20K": "405", "20-50K": "406", "50K以上": "407",
}

EXPERIENCE_MAP = {
    "不限": "0", "在校生": "108", "应届生": "102", "经验不限": "101",
    "1年以内": "103", "1-3年": "104",
    "3-5年": "105", "5-10年": "106", "10年以上": "107",
}

DEGREE_MAP = {
    "不限": "0", "初中及以下": "209", "中专/中技": "208", "高中": "206",
    "大专": "202", "本科": "203", "硕士": "204", "博士": "205",
}

INDUSTRY_MAP = {
    "互联网": "1001", "电子商务": "1002", "金融": "1003", "游戏": "1004",
    "企业服务": "1005", "教育培训": "1006", "社交网络": "1007",
    "医疗健康": "1008", "生活服务": "1009", "广告营销": "1010",
}


# ============================================================
# 全局请求计数器辅助
# ============================================================
def incr_request(limit=MAX_API_REQUESTS):
    """递增全局请求计数，达到上限时抛出异常"""
    global _request_counter
    _request_counter += 1
    if _request_counter > limit:
        raise RuntimeError(f"已达到单次最大请求数 {limit}，停止抓取")
    if _request_counter >= limit * 0.8:
        log.warning(f"⚠️ 请求次数接近上限: {_request_counter}/{limit}")


# ============================================================
# CDP 连接
# ============================================================
class CDPSession:
    def __init__(self, cdp_port=DEFAULT_CDP_PORT):
        if not require_runtime_dependencies("requests", "websocket"):
            raise RuntimeError("缺少 CDP 运行依赖")
        self.cdp_port = cdp_port
        resp = requests.get(f"http://127.0.0.1:{cdp_port}/json/version", timeout=10)
        ws_url = resp.json()["webSocketDebuggerUrl"]
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.mid = 0
        # ``send`` 与 Network 监听共用一条 WebSocket。命令响应到达前收到的
        # 事件不能丢弃，否则快速页面响应会在监听器开始前消失。
        self._event_queue = deque(maxlen=MAX_CDP_EVENT_QUEUE)

    def send(self, method, params=None, sid=None, timeout=30):
        """发送 CDP 命令并等待匹配的响应。

        Args:
            method: CDP 方法名
            params: 参数字典
            sid: Target session ID
            timeout: 等待响应的超时秒数，默认 30s

        Returns:
            CDP 响应字典

        Raises:
            TimeoutError: 超过 max_retries 仍未收到匹配响应
        """
        self.mid += 1
        msg = {"id": self.mid, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        self.ws.send(json.dumps(msg))

        start_time = time.time()
        max_retries = 1000

        for attempt in range(max_retries):
            # 检查超时
            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise TimeoutError(
                    f"CDP send({method}) 超时 ({timeout}s), "
                    f"已跳过 {attempt} 条不匹配消息"
                )

            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                raise TimeoutError(f"CDP WebSocket recv 超时, method={method}")

            try:
                r = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                log.debug(f"跳过非 JSON 消息: {raw[:100]}")
                continue

            if r.get("id") == self.mid:
                return r

            # CDP 命令和事件复用一条 WebSocket。保留有界事件队列，供页面
            # 原生 XHR 监听使用；非事件的陈旧命令响应仍只记录并跳过。
            event_name = r.get("method", "unknown")
            if r.get("method"):
                self._event_queue.append(r)
            log.debug(f"跳过不匹配消息 (id={r.get('id')}, event={event_name})")

        raise TimeoutError(
            f"CDP send({method}) 在 {max_retries} 条消息内未找到匹配响应"
        )

    def eval_js(self, js, sid):
        r = self.send("Runtime.evaluate", {"expression": js, "returnByValue": True}, sid)
        return r.get("result", {}).get("result", {}).get("value", None)

    def clear_events(self, sid=None):
        """Discard queued events for one target session without growing memory."""
        if sid is None:
            self._event_queue.clear()
            return
        self._event_queue = deque(
            (event for event in self._event_queue if event.get("sessionId") != sid),
            maxlen=MAX_CDP_EVENT_QUEUE,
        )

    def recv_event(self, sid=None, timeout=30):
        """Return the next CDP event for ``sid`` within a bounded timeout."""
        deadline = time.time() + timeout
        previous_timeout = None
        can_set_timeout = hasattr(self.ws, "settimeout")
        if can_set_timeout and hasattr(self.ws, "gettimeout"):
            previous_timeout = self.ws.gettimeout()
        try:
            while True:
                for index, event in enumerate(self._event_queue):
                    if sid is None or event.get("sessionId") == sid:
                        del self._event_queue[index]
                        return event

                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError("等待 CDP Network 事件超时")
                if can_set_timeout:
                    self.ws.settimeout(remaining)
                try:
                    raw = self.ws.recv()
                except websocket.WebSocketTimeoutException as exc:
                    raise TimeoutError("等待 CDP Network 事件超时") from exc
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not event.get("method"):
                    log.debug("等待事件时跳过非事件 CDP 消息 id=%s", event.get("id"))
                    continue
                if sid is None or event.get("sessionId") == sid:
                    return event
                self._event_queue.append(event)
        finally:
            if can_set_timeout and previous_timeout is not None:
                self.ws.settimeout(previous_timeout)

    def close(self):
        self.ws.close()


BACKGROUND_VISIBILITY_SCRIPT = (
    "Object.defineProperty(document, 'hidden', {get: () => false});"
    "Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});"
    "Object.defineProperty(document, 'webkitHidden', {get: () => false});"
    "Object.defineProperty(document, 'webkitVisibilityState', {get: () => 'visible'});"
)


def create_page_session(cdp, background=True):
    """Create and attach an about:blank target without stealing focus by default.

    Background pages report themselves as hidden, which prevents BOSS detail
    pages from rendering reliably. Register the existing visibility override
    before callers navigate. Interactive callers such as the login flow must
    opt into a foreground target explicitly.
    """
    target = cdp.send(
        "Target.createTarget",
        {"url": "about:blank", "background": background},
    )
    target_id = target["result"]["targetId"]
    attached = cdp.send(
        "Target.attachToTarget",
        {"targetId": target_id, "flatten": True},
    )
    session_id = attached["result"]["sessionId"]
    if background:
        cdp.send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": BACKGROUND_VISIBILITY_SCRIPT},
            session_id,
        )
    return target_id, session_id


# ============================================================
# 通过页面内 XHR 调 API 获取列表数据（明文薪资）
# ============================================================
FETCH_API_JS_TEMPLATE = """
(function(){
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '__API_URL__', false);
    xhr.send();
    if (xhr.status !== 200) return JSON.stringify([{error: xhr.status}]);
    var data = JSON.parse(xhr.responseText);
    var jobs = (data.zpData || {}).jobList || [];
    var results = jobs.map(function(j) {
        return {
            title: j.jobName || '',
            salary: j.salaryDesc || '',
            salary_source: j.salaryDesc ? 'api' : 'api_empty',
            location: (j.cityName || '') + '\\u00b7' + (j.areaDistrict || '') + '\\u00b7' + (j.businessDistrict || ''),
            tags: [j.jobExperience || '', j.jobDegree || ''].filter(function(t){return t && t !== '\\u4e0d\\u9650';}).join(' | '),
            boss_name: j.brandName || '',
            boss_title: j.bossTitle || '',
            company_scale: j.brandScaleName || '',
            company_stage: j.brandStageName || '',
            company_industry: j.brandIndustry || '',
            job_labels: (j.jobLabels || []).join(' | '),
            skills: (j.skills || []).join(' | '),
            security_id: j.securityId || '',
            lid: j.lid || '',
            encrypt_job_id: j.encryptJobId || '',
            encrypt_boss_id: j.encryptBossId || '',
            encrypt_brand_id: j.encryptBrandId || '',
            job_link: j.encryptJobId ? 'https://www.zhipin.com/job_detail/' + j.encryptJobId + '.html' : '',
            company_link: j.encryptBrandId ? 'https://www.zhipin.com/gongsi/' + j.encryptBrandId + '.html' : '',
            welfare: (j.welfareList || []).join(' | ')
        };
    });
    return JSON.stringify(results);
})()
"""

# 职业批次模式需要区分“自然无岗位”和风控/接口异常，因此保留响应状态。
FETCH_POSITION_API_JS_TEMPLATE = """
(function(){
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '__API_URL__', false);
    xhr.send();
    if (xhr.status !== 200) {
        return JSON.stringify({
            ok: false,
            http_status: xhr.status,
            retry_after: xhr.getResponseHeader('Retry-After') || '',
            code: null,
            message: '',
            jobs: []
        });
    }
    var data = JSON.parse(xhr.responseText);
    var zpData = data.zpData || {};
    var jobs = zpData.jobList || [];
    var results = jobs.map(function(j) {
        return {
            title: j.jobName || '',
            salary: j.salaryDesc || '',
            salary_source: j.salaryDesc ? 'api' : 'api_empty',
            location: (j.cityName || '') + '\\u00b7' + (j.areaDistrict || '') + '\\u00b7' + (j.businessDistrict || ''),
            tags: [j.jobExperience || '', j.jobDegree || ''].filter(function(t){return t && t !== '\\u4e0d\\u9650';}).join(' | '),
            boss_name: j.brandName || '',
            boss_title: j.bossTitle || '',
            company_scale: j.brandScaleName || '',
            company_stage: j.brandStageName || '',
            company_industry: j.brandIndustry || '',
            job_labels: (j.jobLabels || []).join(' | '),
            skills: (j.skills || []).join(' | '),
            security_id: j.securityId || '',
            lid: j.lid || '',
            encrypt_job_id: j.encryptJobId || '',
            encrypt_boss_id: j.encryptBossId || '',
            encrypt_brand_id: j.encryptBrandId || '',
            job_link: j.encryptJobId ? 'https://www.zhipin.com/job_detail/' + j.encryptJobId + '.html' : '',
            company_link: j.encryptBrandId ? 'https://www.zhipin.com/gongsi/' + j.encryptBrandId + '.html' : '',
            welfare: (j.welfareList || []).join(' | ')
        };
    });
    return JSON.stringify({
        ok: data.code === 0,
        http_status: xhr.status,
        retry_after: xhr.getResponseHeader('Retry-After') || '',
        code: data.code,
        message: data.message || '',
        has_more: typeof zpData.hasMore === 'boolean' ? zpData.hasMore : null,
        jobs: results
    });
})()
"""

# ============================================================
# DEPRECATED: DOM 提取作为 fallback（薪资可能是加密字体）
# 此方法已弃用，仅作为 API 方式失败时的最后降级手段。
# 新代码应优先使用 FETCH_API_JS_TEMPLATE 通过 API 获取数据。
# ============================================================
EXTRACT_LIST_JS = """
(function(){
    var results = [];
    var cards = document.querySelectorAll('li.job-card-box');
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var nameEl = card.querySelector('.job-name');
        var salaryEl = card.querySelector('.job-salary');
        var locEl = card.querySelector('.company-location');
        var tagEls = card.querySelectorAll('.tag-list li');
        var bossEl = card.querySelector('.boss-name');
        var bossLink = card.querySelector('.boss-info');
        var tags = [];
        for (var j = 0; j < tagEls.length; j++) tags.push(tagEls[j].innerText.trim());
        var jobLink = nameEl ? (nameEl.getAttribute('href') || '') : '';
        if (jobLink && jobLink.charAt(0) === '/') jobLink = 'https://www.zhipin.com' + jobLink;
        var cLink = bossLink ? (bossLink.getAttribute('href') || '') : '';
        if (cLink && cLink.charAt(0) === '/') cLink = 'https://www.zhipin.com' + cLink;
        var t = nameEl ? nameEl.innerText.trim() : '';
        if (t) results.push({
            title: t,
            salary: salaryEl ? salaryEl.innerText.trim() : '',
            salary_source: 'dom_untrusted',
            location: locEl ? locEl.innerText.trim() : '',
            tags: tags.join(' | '),
            boss_name: bossEl ? bossEl.innerText.trim() : '',
            job_link: jobLink,
            company_link: cLink
        });
    }
    return JSON.stringify(results);
})()
"""

# ============================================================
# 详情页提取与校验
# ============================================================
DETAIL_LOGIN_MARKER = "登录查看完整内容"
DETAIL_DESCRIPTION_MARKER = "职位描述"
DETAIL_COMPETITIVENESS_MARKER = "竞争力分析"
DETAIL_SAFETY_MARKER = "BOSS 安全提示"
MIN_DETAIL_TEXT_LENGTH = 120
DEFAULT_DETAIL_MIN_TEXT_LENGTH = 40
DEFAULT_DETAIL_LOAD_TIMEOUT_SECONDS = 15.0
DEFAULT_DETAIL_POLL_INTERVAL_SECONDS = 0.5
DEFAULT_DETAIL_GAP_RANGE = (2.0, 4.0)
DEFAULT_DETAIL_RETRIES = 1


class DetailExtractionError(ValueError):
    """The rendered page does not contain a usable job description."""


class DetailLoginRequiredError(DetailExtractionError):
    """The detail page is truncated because the BOSS session is not logged in."""


class DetailAccessBlockedError(DetailExtractionError):
    """The detail page was replaced by an access-verification page."""


EXTRACT_DETAIL_JS = """
(function(){
    var pageText = document.body ? document.body.innerText : '';
    var tags = [];
    var benefitWords = ['五险','补充医疗','定期体检','带薪年假','年终奖','零食','餐补',
        '节日福利','加班补助','股票期权','员工旅游','交通补助','通讯补贴','团建',
        '生日福利','免费班车','全勤奖','包吃','弹性工作','下午茶','租房补贴',
        '体检','健身','文化','充电假','司龄假','红包','能量补贴','社团','三薪',
        '绩效','底薪','保底','活动基金','学习基金','节日礼品','无障碍'];
    var noiseWords = ['BOSS直聘','boss','BOSS','来自BOSS直聘','金','金币'];
    function isBenefit(t) {
        if (t === '...' || t.length > 15 || t.length < 2) return true;
        for (var i = 0; i < benefitWords.length; i++) {
            if (t.includes(benefitWords[i])) return true;
        }
        for (var i = 0; i < noiseWords.length; i++) {
            if (t === noiseWords[i] || t.includes(noiseWords[i])) return true;
        }
        return false;
    }
    document.querySelectorAll('.job-tags .tag-all span, .job-keyword-list span').forEach(function(s){
        var t = s.innerText.trim();
        if(t && !isBenefit(t)) tags.push(t);
    });
    var jd = '';
    var sections = document.querySelectorAll('.job-detail-section, .job-sec');
    for (var i = 0; i < sections.length; i++) {
        var text = (sections[i].innerText || '').trim();
        if (text.indexOf('职位描述') !== -1 && text.length > jd.length) {
            jd = text;
        }
    }
    return JSON.stringify({
        jd: jd,
        page_text: pageText.substring(0, 12000),
        tags: tags,
        url: location.href
    });
})()
"""


def _normalize_detail_whitespace(text):
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").splitlines()]
    normalized = "\n".join(lines).strip()
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return re.sub(r"[ \t]{2,}", " ", normalized)


def _looks_like_navigation_page(text):
    return (
        DETAIL_DESCRIPTION_MARKER not in text
        and "无障碍专区" in text
        and "首页" in text
        and "职位" in text
        and "公司" in text
    )


def _recruiter_footer_start(lines):
    stripped_lines = [line.strip() for line in lines]
    end = len(stripped_lines)
    while end and not stripped_lines[end - 1]:
        end -= 1

    def card_start(card_end):
        while card_end and not stripped_lines[card_end - 1]:
            card_end -= 1
        if card_end < 4 or stripped_lines[card_end - 2] != "·":
            return None
        activity_or_name = stripped_lines[card_end - 4]
        has_activity_line = (
            activity_or_name == "在线" or activity_or_name.endswith("活跃")
        )
        start = card_end - 5 if has_activity_line else card_end - 4
        return start if start >= 0 else None

    for marker in (DETAIL_COMPETITIVENESS_MARKER, DETAIL_SAFETY_MARKER):
        try:
            marker_index = stripped_lines.index(marker)
        except ValueError:
            continue
        start = card_start(marker_index)
        if start is not None:
            return start
    return card_start(end)


def extract_job_description(extracted, min_length=MIN_DETAIL_TEXT_LENGTH):
    """Return validated JD text without BOSS page chrome.

    `page_text` is diagnostic input only. It is never persisted unless it has
    an explicit job-description section that passes all checks.
    """
    if not isinstance(extracted, dict):
        raise DetailExtractionError("detail extractor returned non-dict")

    raw_jd = str(extracted.get("jd") or "")
    page_text = str(extracted.get("page_text") or "")
    diagnostic_text = "\n".join((raw_jd, page_text))

    if DETAIL_LOGIN_MARKER in diagnostic_text:
        raise DetailLoginRequiredError(
            "detail page is truncated at the login wall; refresh the BOSS login session"
        )
    if _looks_like_navigation_page(diagnostic_text):
        raise DetailExtractionError("detail page rendered navigation chrome without a JD")

    text = raw_jd
    if not text and DETAIL_DESCRIPTION_MARKER in page_text:
        text = page_text
    if DETAIL_DESCRIPTION_MARKER in text:
        text = text.split(DETAIL_DESCRIPTION_MARKER, 1)[1]

    # Some detail variants prepend skill tags before the actual duty section.
    duty_markers = ("岗位职责", "工作职责", "职位职责", "工作内容", "任职要求", "任职资格")
    marker_positions = [text.find(marker) for marker in duty_markers if text.find(marker) >= 0]
    if marker_positions:
        first_marker = min(marker_positions)
        if first_marker > 0:
            if text[first_marker - 1] in "【[":
                first_marker -= 1
            text = text[first_marker:]

    lines = text.replace("\r\n", "\n").splitlines()
    footer_start = _recruiter_footer_start(lines)
    if footer_start is not None:
        lines = lines[:footer_start]
    else:
        for index, line in enumerate(lines):
            if line.strip() == DETAIL_SAFETY_MARKER:
                lines = lines[:index]
                break

    jd = _normalize_detail_whitespace("\n".join(lines))
    if len(jd) < min_length:
        raise DetailExtractionError(
            f"job description too short after validation: {len(jd)} < {min_length}"
        )
    return jd


def parse_detail_eval_value(value):
    """Parse one rendered detail extraction result without inventing missing text."""
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DetailExtractionError(f"detail extractor returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise DetailExtractionError("detail extractor returned non-dict")
    return parsed


def wait_for_job_description(cdp, session_id,
                             timeout=DEFAULT_DETAIL_LOAD_TIMEOUT_SECONDS,
                             poll_interval=DEFAULT_DETAIL_POLL_INTERVAL_SECONDS,
                             min_length=DEFAULT_DETAIL_MIN_TEXT_LENGTH):
    """Poll the rendered page until a validated JD appears or the wait is bounded out."""
    deadline = time.monotonic() + max(0.1, float(timeout))
    last_error = None
    while True:
        extracted = parse_detail_eval_value(cdp.eval_js(EXTRACT_DETAIL_JS, session_id))
        diagnostic_text = "\n".join((
            str(extracted.get("jd") or ""),
            str(extracted.get("page_text") or ""),
        ))
        blocked_markers = (
            "请完成安全验证", "点击进行验证", "访问过于频繁",
            "当前访问存在异常", "请先完成验证",
        )
        if any(marker in diagnostic_text for marker in blocked_markers):
            raise DetailAccessBlockedError("detail page requires access verification")
        try:
            extracted["jd"] = extract_job_description(extracted, min_length=min_length)
            return extracted
        except DetailLoginRequiredError:
            raise
        except DetailExtractionError as exc:
            last_error = exc

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            detail = f": {last_error}" if last_error else ""
            raise DetailExtractionError(
                f"detail page did not render a usable JD within {timeout:g}s{detail}"
            )
        time.sleep(min(max(0.05, float(poll_interval)), remaining))


# ============================================================
# 解析城市参数（支持中文和代码）
# ============================================================
def fetch_boss_json(url, timeout=10):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_live_city_maps(timeout=10):
    global _live_city_maps_cache
    if _live_city_maps_cache is not None:
        return _live_city_maps_cache

    name_to_code = {}

    try:
        hot_city_data = fetch_boss_json(HOT_CITY_URL, timeout=timeout)
        for item in hot_city_data.get("zpData", {}).get("hotCityList", []):
            name = item.get("name")
            code = item.get("code")
            if name and code is not None:
                name_to_code[name] = str(code)

        city_group_data = fetch_boss_json(CITY_GROUP_URL, timeout=timeout)
        for group in city_group_data.get("zpData", {}).get("cityGroup", []):
            for item in group.get("cityList", []):
                name = item.get("name")
                code = item.get("code")
                if name and code is not None:
                    name_to_code.setdefault(name, str(code))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        log.debug(f"加载 BOSS 城市映射失败，使用内置城市映射: {e}")

    code_to_name = {code: name for name, code in name_to_code.items()}
    _live_city_maps_cache = name_to_code, code_to_name
    return _live_city_maps_cache


def resolve_city(city_input):
    """把「中文城市名 / 城市码」解析为 (name, code)。

    查询链（逐级降级）:
      1. 本地静态码表 data/city_codes.json（全量、离线可用）
      2. 运行时拉 BOSS 接口 hot/city.json + cityGroup.json（自愈）
      3. 都查不到则原样返回（兼容用户直接传裸 city code）
    """
    if not city_input:
        return city_input, city_input

    # 1. 本地静态码表
    local_map, local_reverse = load_local_city_map()
    if city_input in local_map:
        return city_input, local_map[city_input]
    if city_input in local_reverse:
        return local_reverse[city_input], city_input

    # 2. 运行时拉 BOSS 接口
    live_map, live_reverse = load_live_city_maps()
    if city_input in live_map:
        return city_input, live_map[city_input]
    if city_input in live_reverse:
        return live_reverse[city_input], city_input

    # 3. 兜底：原样返回
    return city_input, city_input


def list_cities(keyword=None, use_live=True):
    """打印支持的城市列表。keyword 非空时只打印城市名含该关键词的城市。

    优先用运行时拉取的最新码表（use_live=True），拉取失败回退本地静态码表。
    """
    name_to_code = {}
    if use_live:
        live_map, _ = load_live_city_maps()
        name_to_code.update(live_map)
    if not name_to_code:
        local_map, _ = load_local_city_map()
        name_to_code.update(local_map)
    if not name_to_code:
        print("⚠️ 无法加载城市码表（本地静态文件缺失且网络拉取失败）")
        return

    items = sorted(name_to_code.items(), key=lambda kv: kv[0])
    if keyword:
        keyword = keyword.strip()
        items = [(n, c) for n, c in items if keyword in n]
        if not items:
            print(f"没有匹配「{keyword}」的城市")
            return
    print(f"共 {len(items)} 个城市（支持中文城市名或城市码）：")
    for name, code in items:
        print(f"  {name}\t{code}")


# ============================================================
# BOSS 官方三级职业目录
# ============================================================
class PositionCatalogError(ValueError):
    """官方职业目录缺失、损坏或字段结构发生变化。"""


class PositionFetchError(RuntimeError):
    """按职业代码请求职位列表失败。"""


class PositionNativeResponseTimeoutError(PositionFetchError):
    """当前搜索 tab 未在有界时间内产生预期的原生职位响应。"""


class PositionFetchBlockedError(PositionFetchError):
    """BOSS 返回登录/环境限制，继续请求不会恢复。"""

    def __init__(self, message, *, retry_after_seconds=None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class PositionSessionRequiredError(PositionFetchError):
    """BOSS 职业接口明确返回未登录，重试同一会话没有意义。"""


class PositionFetchCooldownError(PositionFetchError):
    """已有站点限制仍处于冷却期，本次禁止继续联网。"""


def validate_position_category_pacing(interval_seconds, jitter_seconds):
    """Validate and normalize the bounded delay between position categories."""
    try:
        interval = float(interval_seconds)
        jitter = float(jitter_seconds)
    except (TypeError, ValueError) as e:
        raise ValueError("职业分类间隔和抖动必须是数字") from e
    if not math.isfinite(interval) or not math.isfinite(jitter):
        raise ValueError("职业分类间隔和抖动必须是有限数字")
    if interval < 0 or jitter < 0:
        raise ValueError("职业分类间隔和抖动不能小于 0")
    if interval + jitter > MAX_POSITION_CATEGORY_DELAY_SECONDS:
        raise ValueError(
            f"职业分类最大等待不能超过 {MAX_POSITION_CATEGORY_DELAY_SECONDS:.0f} 秒"
        )
    return interval, jitter


def position_category_delay_seconds(interval_seconds, jitter_seconds):
    """Return one bounded category delay using additive non-negative jitter."""
    interval, jitter = validate_position_category_pacing(interval_seconds, jitter_seconds)
    return interval + (random.uniform(0.0, jitter) if jitter else 0.0)


def position_pause_seconds(index, total, interval_seconds, jitter_seconds,
                           batch_size=0, batch_cooldown_seconds=0.0):
    """Return one bounded pause between categories, including batch cooling."""
    if index >= total:
        return 0.0, False
    regular = position_category_delay_seconds(interval_seconds, jitter_seconds)
    is_batch_boundary = batch_size > 0 and index % batch_size == 0
    if is_batch_boundary:
        return max(regular, float(batch_cooldown_seconds)), True
    return regular, False


def effective_position_page_limit(configured_pages, per_position_limit):
    """多页目标允许追到 quota/耗尽/硬上限；单页目标保持原 scope。"""
    required_pages = math.ceil(per_position_limit / POSITION_API_PAGE_SIZE)
    if required_pages > 1:
        return MAX_PAGES
    return min(MAX_PAGES, max(int(configured_pages), required_pages))


def parse_retry_after_seconds(value, *, now=None):
    """Parse Retry-After seconds/date into a bounded non-negative duration."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            target = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        seconds = (target - current.astimezone(timezone.utc)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return min(MAX_POSITION_BLOCK_COOLDOWN_SECONDS, max(0.0, seconds))


def position_block_cooldown_seconds(attempt_count, retry_after_seconds=None,
                                    base_seconds=DEFAULT_POSITION_BLOCK_COOLDOWN_SECONDS):
    """Choose Retry-After when present, otherwise use bounded exponential cooldown."""
    try:
        base = float(base_seconds)
    except (TypeError, ValueError) as e:
        raise ValueError("站点限制冷却时间必须是数字") from e
    if (
        not math.isfinite(base)
        or base < 0
        or base > DEFAULT_POSITION_BLOCK_MAX_COOLDOWN_SECONDS
    ):
        raise ValueError(
            "站点限制冷却时间必须在 "
            f"0-{DEFAULT_POSITION_BLOCK_MAX_COOLDOWN_SECONDS:.0f} 秒之间"
        )
    if retry_after_seconds is not None:
        return min(MAX_POSITION_BLOCK_COOLDOWN_SECONDS, max(0.0, float(retry_after_seconds)))
    exponent = max(0, min(int(attempt_count or 1) - 1, 16))
    return min(
        MAX_POSITION_BLOCK_COOLDOWN_SECONDS,
        DEFAULT_POSITION_BLOCK_MAX_COOLDOWN_SECONDS,
        base * (2 ** exponent),
    )


def active_position_cooldown(state, pending, *, now=None):
    """Return the pending category restriction while its persisted cooldown is active."""
    pending_codes = {str(item.get("position_code") or "") for item in pending}
    restriction = state.get("site_restriction")
    if not isinstance(restriction, dict):
        return None
    restriction_scope = str(restriction.get("scope") or "position")
    if (
        restriction_scope != "login_probe"
        and str(restriction.get("position_code") or "") not in pending_codes
    ):
        return None
    raw_until = str(restriction.get("cooldown_until") or "").strip()
    if not raw_until:
        return None
    try:
        until = datetime.fromisoformat(raw_until.replace("Z", "+00:00"))
    except ValueError:
        return None
    if until.tzinfo is None:
        until = until.astimezone()
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    remaining = (until - current).total_seconds()
    if remaining <= 0:
        return None
    return {**restriction, "remaining_seconds": remaining}


def load_active_position_checkpoint_cooldown(output_path=None, checkpoint_path=None,
                                             *, now=None):
    """Read an existing checkpoint and enforce cooldown before any CDP request."""
    if checkpoint_path:
        path = os.path.abspath(os.path.expanduser(checkpoint_path))
    elif output_path:
        _, path, _ = position_artifact_paths(output_path)
    else:
        return None
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        raise PositionCatalogError(f"断点文件无法读取: {path}: {e}") from e
    if not isinstance(state, dict):
        raise PositionCatalogError(f"断点文件不是对象: {path}")
    completed = set(state.get("completed_position_codes") or [])
    pending = [
        item for item in state.get("catalog") or []
        if isinstance(item, dict) and item.get("position_code") not in completed
    ]
    return active_position_cooldown(state, pending, now=now)


def describe_position_cooldown(cooldown):
    remaining = max(1, math.ceil(float(cooldown["remaining_seconds"])))
    target = (
        "登录探测"
        if cooldown.get("scope") == "login_probe"
        else cooldown.get("position_code") or "unknown"
    )
    return (
        f"BOSS 站点限制仍在冷却期，约 {remaining} 秒后再续跑；"
        f"目标={target}"
    )


def build_position_tree_url(city_code):
    return f"{POSITION_TREE_URL}?{urlencode({'cityCode': city_code})}"


def _position_node_value(node, key, context):
    if not isinstance(node, dict):
        raise PositionCatalogError(f"职业目录节点不是对象: {context}")
    value = node.get(key)
    if value is None or not str(value).strip():
        raise PositionCatalogError(f"职业目录节点缺少 {key}: {context}")
    return str(value).strip()


def parse_position_catalog(data, source_url):
    """把官方三级职业树整理为按三级职业代码去重的目录。

    BOSS 的同一三级职业代码可能挂在多个一级/二级路径下。采集只请求一次该
    代码，同时在 ``category_paths`` / ``category_paths_raw`` 中保留全部路径。
    """
    if not isinstance(data, dict):
        raise PositionCatalogError("职业目录响应不是对象")
    code = data.get("code")
    if code not in (0, "0", None):
        raise PositionCatalogError(
            f"职业目录接口返回 code={code}: {data.get('message') or '未知错误'}"
        )
    top_levels = (data.get("zpData") or {}).get("position")
    if not isinstance(top_levels, list) or not top_levels:
        raise PositionCatalogError("职业目录响应缺少 zpData.position")

    by_code = {}
    path_count = 0
    for level_1 in top_levels:
        level_1_code = _position_node_value(level_1, "code", "一级")
        level_1_name = _position_node_value(level_1, "name", level_1_code)
        level_2_nodes = level_1.get("subLevelModelList") or []
        if not isinstance(level_2_nodes, list):
            raise PositionCatalogError(f"二级职业列表格式错误: {level_1_name}")
        for level_2 in level_2_nodes:
            level_2_code = _position_node_value(level_2, "code", level_1_name)
            level_2_name = _position_node_value(level_2, "name", level_2_code)
            level_3_nodes = level_2.get("subLevelModelList") or []
            if not isinstance(level_3_nodes, list):
                raise PositionCatalogError(f"三级职业列表格式错误: {level_2_name}")
            for level_3 in level_3_nodes:
                position_code = _position_node_value(level_3, "code", level_2_name)
                position_name = _position_node_value(level_3, "name", position_code)
                path = {
                    "category_level_1_code": level_1_code,
                    "category_level_1_raw": level_1_name,
                    "category_level_2_code": level_2_code,
                    "category_level_2_raw": level_2_name,
                    "category_level_3_code": position_code,
                    "category_level_3_raw": position_name,
                    "category_path_raw": " > ".join(
                        (level_1_name, level_2_name, position_name)
                    ),
                }
                path_count += 1
                category = by_code.get(position_code)
                if category is None:
                    category = {
                        **path,
                        "position_code": position_code,
                        "position_name": position_name,
                        "category_source": POSITION_CATEGORY_SOURCE,
                        "category_source_url": source_url,
                        "category_identity_verified": True,
                        "category_paths": [],
                        "category_paths_raw": [],
                    }
                    by_code[position_code] = category
                elif category["position_name"] != position_name:
                    raise PositionCatalogError(
                        f"同一职业代码名称不一致: {position_code} "
                        f"{category['position_name']} != {position_name}"
                    )
                if path["category_path_raw"] not in category["category_paths_raw"]:
                    category["category_paths"].append(path)
                    category["category_paths_raw"].append(path["category_path_raw"])

    catalog = list(by_code.values())
    if not catalog:
        raise PositionCatalogError("职业目录中没有三级职业")
    if len(catalog) > MAX_POSITION_CATEGORIES:
        raise PositionCatalogError(
            f"职业代码数量 {len(catalog)} 超过安全上限 {MAX_POSITION_CATEGORIES}"
        )
    for category in catalog:
        category["category_path_count"] = len(category["category_paths"])
    log.info(
        "已加载 BOSS 职业目录: %s 个唯一职业代码，%s 条三级路径",
        len(catalog), path_count,
    )
    return catalog


def load_position_catalog(city_code, timeout=10, retries=POSITION_CATALOG_RETRIES):
    """有界重试拉取并校验官方职业目录。"""
    if retries < 1:
        raise ValueError("retries 必须大于等于 1")
    source_url = build_position_tree_url(city_code)
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return parse_position_catalog(
                fetch_boss_json(source_url, timeout=timeout),
                source_url,
            )
        except (OSError, json.JSONDecodeError, PositionCatalogError, ValueError) as e:
            last_error = e
            if attempt < retries:
                delay = min(2 ** (attempt - 1), 4)
                log.warning(
                    "职业目录加载失败 (%s/%s)，%.0fs 后重试: %s",
                    attempt, retries, delay, e,
                )
                time.sleep(delay)
    raise PositionCatalogError(f"职业目录加载失败: {last_error}") from last_error


def select_position_categories(catalog, position_codes=None):
    """按传入顺序选择职业代码；未传则返回完整官方目录。"""
    categories = list(catalog or [])
    if not position_codes:
        return categories
    by_code = {item.get("position_code"): item for item in categories}
    selected = []
    missing = []
    seen = set()
    for raw_code in position_codes:
        code = str(raw_code).strip()
        if code in seen:
            continue
        seen.add(code)
        if code not in by_code:
            missing.append(code)
        else:
            selected.append(by_code[code])
    if missing:
        raise PositionCatalogError(f"官方职业目录中不存在代码: {', '.join(missing)}")
    return selected


def list_positions(keyword=None, city_input=DEFAULT_CITY_INPUT):
    city_name, city_code = resolve_city(city_input)
    catalog = load_position_catalog(city_code)
    if keyword:
        needle = str(keyword).strip().lower()
        catalog = [
            item for item in catalog
            if needle in item["position_code"].lower()
            or any(needle in path.lower() for path in item["category_paths_raw"])
        ]
    print(f"{city_name}官方职业目录: {len(catalog)} 个唯一三级职业代码")
    for item in catalog:
        aliases = item["category_path_count"] - 1
        suffix = f" (+{aliases} 条别名路径)" if aliases else ""
        print(f"  {item['position_code']}\t{item['category_path_raw']}{suffix}")
    return catalog


class LoginProbeStatus(Enum):
    """Outcome of one login probe request."""

    AVAILABLE = "available"
    UNAUTHENTICATED = "unauthenticated"
    RESTRICTED = "restricted"
    EMPTY = "empty"
    RESPONSE_ERROR = "response_error"


@dataclass(frozen=True)
class LoginProbeResult:
    """Structured login probe result with the original failure context."""

    status: LoginProbeStatus
    code: int | None = None
    message: str = ""
    retryable: bool = False


def classify_login_probe_response(data, http_status=200):
    """Classify a BOSS search response without collapsing failures to bool."""
    if http_status == 401:
        return LoginProbeResult(
            LoginProbeStatus.UNAUTHENTICATED,
            message="HTTP 401",
        )
    if http_status in (403, 429):
        return LoginProbeResult(
            LoginProbeStatus.RESTRICTED,
            message=f"HTTP {http_status}",
        )
    if http_status != 200:
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR,
            message=f"HTTP {http_status}",
            retryable=http_status == 0 or http_status >= 500,
        )
    if not isinstance(data, dict):
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR,
            message="响应不是 JSON 对象",
            retryable=True,
        )

    raw_code = data.get("code")
    try:
        code = int(raw_code) if raw_code is not None else None
    except (TypeError, ValueError):
        code = None
    message = str(data.get("message") or data.get("msg") or "")

    if code in LOGIN_RESTRICTED_CODES:
        return LoginProbeResult(LoginProbeStatus.RESTRICTED, code=code, message=message)
    if code != 0:
        if any(keyword in message for keyword in LOGIN_RESTRICTED_MESSAGE_KEYWORDS):
            return LoginProbeResult(LoginProbeStatus.RESTRICTED, code=code, message=message)
        return LoginProbeResult(LoginProbeStatus.RESPONSE_ERROR, code=code, message=message)

    zp_data = data.get("zpData")
    if not isinstance(zp_data, dict):
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR,
            code=code,
            message="响应缺少 zpData",
            retryable=True,
        )
    job_list = zp_data.get("jobList")
    if not isinstance(job_list, list):
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR,
            code=code,
            message="响应缺少 jobList",
            retryable=True,
        )
    if not job_list:
        return LoginProbeResult(LoginProbeStatus.EMPTY, code=code)
    if any(
        (job.get("salaryDesc") or "").strip()
        for job in job_list
        if isinstance(job, dict)
    ):
        return LoginProbeResult(LoginProbeStatus.AVAILABLE, code=code)
    return LoginProbeResult(LoginProbeStatus.UNAUTHENTICATED, code=code)


def is_logged_in_search_response(data):
    """Return True only when BOSS returns jobs with plaintext salary."""
    result = classify_login_probe_response(data)
    return result.status is LoginProbeStatus.AVAILABLE


def build_login_probe_url(query, city_code):
    params = {
        "scene": 1,
        "query": query,
        "city": city_code,
        "page": 1,
        "pageSize": LOGIN_PROBE_PAGE_SIZE,
    }
    return f"{API_JOB_LIST_PATH}?{urlencode(params)}"


def probe_login_state(cdp, sid, query=LOGIN_PROBE_QUERY, city_code=LOGIN_PROBE_CITY):
    """Run exactly one budgeted search probe and return its structured state."""
    probe_url = build_login_probe_url(query, city_code)
    js = f"""
    (function(){{
        var xhr = new XMLHttpRequest();
        xhr.open('GET', '{probe_url}', false);
        xhr.send();
        return JSON.stringify({{
            httpStatus: xhr.status,
            body: xhr.responseText
        }});
    }})()
    """
    incr_request()
    val = cdp.eval_js(js, sid)
    if not val:
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR,
            message="探测响应为空",
            retryable=True,
        )
    try:
        envelope = json.loads(val) if isinstance(val, str) else val
    except (json.JSONDecodeError, ValueError):
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR,
            message="探测响应不是有效 JSON",
            retryable=True,
        )
    if not isinstance(envelope, dict):
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR,
            message="探测响应格式异常",
            retryable=True,
        )

    raw_http_status = envelope.get("httpStatus", 200)
    try:
        http_status = int(raw_http_status)
    except (TypeError, ValueError):
        http_status = 0
    body = envelope.get("body", envelope)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return LoginProbeResult(
                LoginProbeStatus.RESPONSE_ERROR,
                message="搜索接口响应不是有效 JSON",
                retryable=True,
            )
    return classify_login_probe_response(body, http_status=http_status)


def probe_login_state_native(cdp, sid, query=LOGIN_PROBE_QUERY,
                             city_code=LOGIN_PROBE_CITY):
    """Verify login from the search page's own request, without injected XHR."""
    navigation_url = build_search_url(query, city_code, 1, {})
    incr_request()
    body, http_status, _retry_after = capture_native_joblist_response(
        cdp,
        sid,
        navigation_url,
        {"query": str(query), "city": str(city_code), "page": "1"},
        f"login query={query}, city={city_code}",
    )
    if http_status != 200:
        return classify_login_probe_response({}, http_status=http_status)
    try:
        payload = json.loads(body) if isinstance(body, str) else body
    except (json.JSONDecodeError, ValueError, TypeError):
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR,
            message="搜索接口响应不是有效 JSON",
            retryable=True,
        )
    return classify_login_probe_response(payload, http_status=http_status)


def describe_login_probe_result(result):
    """Return a concise user-facing explanation for a non-available state."""
    context = []
    if result.code is not None:
        context.append(f"code: {result.code}")
    if result.message:
        context.append(result.message)
    suffix = f"（{'; '.join(context)}）" if context else ""

    if result.status is LoginProbeStatus.UNAUTHENTICATED:
        return f"未检测到可用登录态{suffix}"
    if result.status is LoginProbeStatus.RESTRICTED:
        return f"BOSS 接口返回限制状态{suffix}"
    if result.status is LoginProbeStatus.EMPTY:
        return "探测样本没有职位，暂时无法确认登录态"
    return f"登录探测响应异常{suffix}"


# ============================================================
# 登录状态检测
# ============================================================
def check_login_state(cdp_port=DEFAULT_CDP_PORT):
    """通过 CDP 检测 BOSS直聘登录状态。

    Returns:
        LoginProbeResult: 登录探测的结构化状态
    """
    cdp = None
    tid = None
    try:
        cdp = CDPSession(cdp_port)
        # 原生搜索页必须保持普通前台语义；后台 target 即使覆盖 visibility，
        # document.hasFocus() 仍为 false，会形成不一致的浏览器环境信号。
        tid, sid = create_page_session(cdp, background=False)

        return probe_login_state_native(cdp, sid)
    except (requests.ConnectionError, requests.Timeout, KeyError,
            json.JSONDecodeError, websocket.WebSocketException,
            TimeoutError, RuntimeError) as e:
        log.error(f"登录状态检测失败: {e}")
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR,
            message=str(e),
        )
    finally:
        if cdp is not None:
            if tid is not None:
                try:
                    cdp.send("Target.closeTarget", {"targetId": tid})
                except (KeyError, websocket.WebSocketException, TimeoutError):
                    log.debug("关闭登录探测 target 失败", exc_info=True)
            try:
                cdp.close()
            except websocket.WebSocketException:
                log.debug("关闭登录探测 CDP 连接失败", exc_info=True)


def wait_for_login(cdp_port=DEFAULT_CDP_PORT, timeout=DEFAULT_LOGIN_TIMEOUT, interval=3):
    """Open BOSS login page and wait until plaintext salary is available."""
    cdp = CDPSession(cdp_port)
    tid, sid = create_page_session(cdp, background=False)
    cdp.send(
        "Page.navigate",
        {"url": "https://www.zhipin.com/web/user/"},
        sid,
    )

    deadline = time.time() + timeout
    logged_in = False
    attempt = 0
    transient_errors = 0
    print(f"等待 BOSS 登录完成（最长 {timeout}s）", end="", flush=True)
    try:
        while time.time() <= deadline:
            query, city_code = LOGIN_PROBE_TARGETS[attempt % len(LOGIN_PROBE_TARGETS)]
            try:
                result = probe_login_state(cdp, sid, query=query, city_code=city_code)
            except RuntimeError as e:
                print(f"\n❌ {e}")
                return False

            if result.status is LoginProbeStatus.AVAILABLE:
                logged_in = True
                print("\n✅ 已检测到 BOSS 登录态，且接口返回明文薪资")
                return True
            if result.status is LoginProbeStatus.RESTRICTED:
                print(f"\n❌ {describe_login_probe_result(result)}，已停止登录探测")
                print("   当前问题不是尚未登录；请先在浏览器中完成验证或稍后再试")
                return False
            if result.status is LoginProbeStatus.RESPONSE_ERROR:
                if not result.retryable:
                    print(f"\n❌ {describe_login_probe_result(result)}，已停止登录探测")
                    return False
                transient_errors += 1
                if transient_errors > LOGIN_PROBE_MAX_TRANSIENT_ERRORS:
                    print(f"\n❌ {describe_login_probe_result(result)}，连续异常次数过多")
                    return False
            else:
                transient_errors = 0

            print(".", end="", flush=True)
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            delay = min(interval * (2 ** attempt), LOGIN_PROBE_MAX_INTERVAL, remaining)
            time.sleep(delay)
            attempt += 1
        print("\n❌ 等待登录超时")
        print("   Chrome 会继续保持打开；登录后可重新运行 --check 或抓取命令")
        return False
    finally:
        if logged_in:
            cdp.send("Target.closeTarget", {"targetId": tid})
        cdp.close()


# ============================================================
# CSV 导出
# ============================================================
CSV_COLUMNS = [
    "record_id", "site_name", "content_category", "title_raw", "job_id", "title",
    "salary", "salary_source", "price_evidence", "list_url", "detail_url",
    "location", "tags", "boss_name",
    "company_scale", "company_stage", "company_industry", "skills",
    "job_link", "welfare", "position_code", "position_name",
    "category_level_1_code", "category_level_1_raw",
    "category_level_2_code", "category_level_2_raw",
    "category_level_3_code", "category_level_3_raw", "category_path_raw",
    "category_source", "category_source_url", "category_identity_verified",
    "category_paths_raw",
]

DETAIL_CSV_COLUMNS = [
    "record_id", "site_name", "content_category", "title_raw", "job_id", "title",
    "company", "salary", "salary_source", "price_evidence", "list_url", "detail_url",
    "location", "tags_list", "job_link", "skill_tags", "jd", "position_code", "position_name",
    "category_level_1_raw", "category_level_2_raw", "category_level_3_raw",
    "category_path_raw", "category_source", "category_source_url",
    "category_identity_verified", "category_paths_raw",
]


def write_csv(csv_path, jobs):
    """将 jobs 列表写入 CSV 文件"""
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for j in jobs:
            # 确保每列都有值
            row = {col: j.get(col, "") for col in CSV_COLUMNS}
            if isinstance(row.get("category_paths_raw"), list):
                row["category_paths_raw"] = " | ".join(row["category_paths_raw"])
            if isinstance(row.get("price_evidence"), list):
                row["price_evidence"] = json.dumps(
                    row["price_evidence"], ensure_ascii=False, separators=(",", ":")
                )
            writer.writerow(row)
    print(f"CSV 已保存: {csv_path}")


def write_detail_csv(csv_path, details):
    """将岗位详情列表写入 CSV 文件"""
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for d in details:
            row = {col: d.get(col, "") for col in DETAIL_CSV_COLUMNS}
            if isinstance(row.get("skill_tags"), list):
                row["skill_tags"] = " | ".join(row["skill_tags"])
            if isinstance(row.get("category_paths_raw"), list):
                row["category_paths_raw"] = " | ".join(row["category_paths_raw"])
            if isinstance(row.get("price_evidence"), list):
                row["price_evidence"] = json.dumps(
                    row["price_evidence"], ensure_ascii=False, separators=(",", ":")
                )
            writer.writerow(row)
    print(f"详情 CSV 已保存: {csv_path}")


def has_numeric_salary(text):
    """价格原文至少包含一个非零数字时，视为有明确薪资。"""
    return bool(NONZERO_SALARY_DIGIT_RE.search(str(text or "")))


def filter_numeric_salary_jobs(jobs, numeric_price_only=False):
    """按明确数字薪资筛选，返回 (保留记录, 过滤数量)。"""
    records = list(jobs or [])
    if not numeric_price_only:
        return records, 0
    kept = [job for job in records if isinstance(job, dict) and has_numeric_salary(job.get("salary"))]
    return kept, len(records) - len(kept)


def apply_numeric_salary_filter(list_data, numeric_price_only=False):
    """将明确数字薪资筛选应用到列表数据，并累计过滤计数。"""
    if not numeric_price_only:
        return list_data, 0
    filtered_data = dict(list_data)
    kept, filtered_now = filter_numeric_salary_jobs(
        filtered_data.get("jobs", []),
        numeric_price_only=True,
    )
    try:
        previous_filtered = int(filtered_data.get("filtered_out_count", 0) or 0)
    except (TypeError, ValueError):
        previous_filtered = 0
    filtered_data["jobs"] = kept
    filtered_data["total"] = len(kept)
    filtered_data["filtered_out_count"] = previous_filtered + filtered_now
    return filtered_data, filtered_now


# ============================================================
# 增量写入 JSON
# ============================================================
def append_json(path, new_jobs):
    """追加 jobs 到 JSON 文件，每条按 job_id 去重"""
    existing = []
    seen_ids = set()
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing = data.get("jobs", [])
            seen_ids = {j.get("job_id", "") for j in existing}
        except (json.JSONDecodeError, OSError, ValueError):
            data = {}
    added = 0
    for j in new_jobs:
        if j.get("job_id") not in seen_ids:
            existing.append(j)
            seen_ids.add(j.get("job_id", ""))
            added += 1
    data["jobs"] = existing
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return added


def flush_jobs(path, meta, jobs, numeric_price_only=False,
               count_filtered_during_flush=True):
    """每次有新数据就全量刷写（jobs 去重后），保证异常退出也能保留"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # 合并已有文件
    existing_jobs = []
    seen_ids = set()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
            existing_jobs = old.get("jobs", [])
            seen_ids = {j.get("job_id", "") for j in existing_jobs}
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    filtered_during_flush = 0
    if numeric_price_only:
        existing_jobs, filtered_existing = filter_numeric_salary_jobs(
            existing_jobs,
            numeric_price_only=True,
        )
        jobs, filtered_new = filter_numeric_salary_jobs(jobs, numeric_price_only=True)
        filtered_during_flush = filtered_existing + filtered_new
        seen_ids = {j.get("job_id", "") for j in existing_jobs}
    for j in jobs:
        if j.get("job_id") not in seen_ids:
            existing_jobs.append(j)
            seen_ids.add(j.get("job_id", ""))
    if numeric_price_only:
        try:
            reported_filtered = int(meta.get("filtered_out_count", 0) or 0)
        except (TypeError, ValueError):
            reported_filtered = 0
        counted_during_flush = filtered_during_flush if count_filtered_during_flush else 0
        meta["filtered_out_count"] = reported_filtered + counted_during_flush
    meta["total"] = len(existing_jobs)
    meta["jobs"] = existing_jobs
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return filtered_during_flush if count_filtered_during_flush else 0


# ============================================================
# 合并外部 JSON 文件
# ============================================================
def merge_jobs(external_path, new_jobs):
    """从外部 JSON 加载 jobs，与 new_jobs 按 job_id 合并去重。

    Args:
        external_path: 已有 JSON 文件路径
        new_jobs: 新抓取的 jobs 列表

    Returns:
        合并后的 jobs 列表
    """
    try:
        with open(external_path, "r", encoding="utf-8") as f:
            old_data = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        log.warning(f"无法加载合并文件 {external_path}: {e}")
        return new_jobs

    old_jobs = old_data.get("jobs", [])
    merged = list(old_jobs)
    seen_ids = {j.get("job_id", "") for j in merged}

    added = 0
    for j in new_jobs:
        if j.get("job_id") not in seen_ids:
            merged.append(j)
            seen_ids.add(j.get("job_id", ""))
            added += 1

    print(f"合并: 旧文件 {len(old_jobs)} 条 + 新抓取 {len(new_jobs)} 条 = {len(merged)} 条 (新增 {added})")
    return merged


def merge_details(external_path, new_details):
    """从外部 JSON 加载详情，与 new_details 按 job_id 合并去重。

    详情文件本身可能是列表结构（scrape_details 输出）或带 jobs/details 键的字典，
    这里都做兼容。优先保留 new_details 中的同名记录（更新覆盖旧值）。

    Args:
        external_path: 已有详情 JSON 文件路径
        new_details: 新抓取的详情列表（可为空）

    Returns:
        合并后的详情列表
    """
    if not external_path:
        return new_details
    try:
        with open(external_path, "r", encoding="utf-8") as f:
            old_data = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        log.warning(f"无法加载合并详情文件 {external_path}: {e}")
        return new_details

    if isinstance(old_data, list):
        old_details = old_data
    elif isinstance(old_data, dict):
        old_details = old_data.get("details") or old_data.get("jobs") or []
    else:
        old_details = []

    merged = merge_details_from_lists(old_details, new_details)
    print(f"合并详情: 旧文件 {len(old_details)} 条 + 新抓取 {len(new_details)} 条 = {len(merged)} 条")
    return merged


def merge_details_from_lists(old_details, new_details):
    """把两份详情列表按 job_id 合并去重，new_details 优先（同 id 用新覆盖旧）。"""
    by_id = {}
    for d in old_details:
        jid = d.get("job_id", "") if isinstance(d, dict) else ""
        if jid:
            by_id[jid] = d
    for d in new_details:
        jid = d.get("job_id", "") if isinstance(d, dict) else ""
        if jid:
            by_id[jid] = d
    return list(by_id.values())


# ============================================================
# 构建搜索 URL
# ============================================================
def build_search_url(keyword, city_code, page, filters):
    params = {"query": keyword, "city": city_code, "page": page}
    for key, code in filters.items():
        if code:
            params[key] = code
    return f"https://www.zhipin.com/web/geek/job?{urlencode(params)}"


def should_use_dom_fallback(jobs, allow_dom_fallback=False):
    return allow_dom_fallback and not jobs


def parse_api_jobs_eval_value(value):
    if not value:
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (json.JSONDecodeError, ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []

    jobs = []
    for item in parsed:
        if not isinstance(item, dict) or item.get("error"):
            continue
        if item.get("title") or item.get("job_link"):
            jobs.append(item)
    return jobs


def parse_position_page_eval_value(value):
    """解析职业批次 API 响应，并显式区分空结果与限制状态。"""
    if not value:
        raise PositionFetchError("职业职位 API 返回空响应")
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        raise PositionFetchError(f"职业职位 API JSON 解析失败: {e}") from e
    if isinstance(parsed, list):
        return parse_api_jobs_eval_value(parsed), None
    if not isinstance(parsed, dict):
        raise PositionFetchError("职业职位 API 响应不是对象")

    http_status = parsed.get("http_status")
    retry_after_seconds = parse_retry_after_seconds(parsed.get("retry_after"))
    if http_status not in (None, 200, "200"):
        if http_status in (401, "401"):
            raise PositionSessionRequiredError("BOSS 职业职位 API HTTP 401，需要重新登录")
        if http_status in (403, 429, "403", "429"):
            raise PositionFetchBlockedError(
                f"BOSS 限制 HTTP {http_status}",
                retry_after_seconds=retry_after_seconds,
            )
        raise PositionFetchError(f"职业职位 API HTTP {http_status}")
    code = parsed.get("code")
    message = str(parsed.get("message") or "")
    if code not in (0, "0", None) or parsed.get("ok") is False:
        blocked_markers = ("访问受限", "登录", *LOGIN_RESTRICTED_MESSAGE_KEYWORDS)
        if code in (31, 32, 37, "31", "32", "37") or any(
            marker in message for marker in blocked_markers
        ):
            raise PositionFetchBlockedError(
                f"BOSS 限制 code={code}: {message or '未知限制'}",
                retry_after_seconds=retry_after_seconds,
            )
        raise PositionFetchError(f"职业职位 API code={code}: {message or '未知错误'}")
    jobs = parse_api_jobs_eval_value(parsed.get("jobs", []))
    has_more = parsed.get("has_more")
    return jobs, has_more if isinstance(has_more, bool) else None


def _join_api_values(value):
    if not isinstance(value, list):
        return ""
    return " | ".join(str(item) for item in value if item)


def normalize_position_api_job(item):
    """Map one raw BOSS jobList item to the established output fields."""
    if not isinstance(item, dict):
        return None
    encrypt_job_id = str(item.get("encryptJobId") or "")
    encrypt_brand_id = str(item.get("encryptBrandId") or "")
    experience = str(item.get("jobExperience") or "")
    degree = str(item.get("jobDegree") or "")
    tags = " | ".join(value for value in (experience, degree) if value and value != "不限")
    salary = str(item.get("salaryDesc") or "")
    result = {
        "title": str(item.get("jobName") or ""),
        "salary": salary,
        "salary_source": "api" if salary else "api_empty",
        "location": "·".join(str(item.get(key) or "") for key in (
            "cityName", "areaDistrict", "businessDistrict",
        )),
        "tags": tags,
        "boss_name": str(item.get("brandName") or ""),
        "boss_title": str(item.get("bossTitle") or ""),
        "company_scale": str(item.get("brandScaleName") or ""),
        "company_stage": str(item.get("brandStageName") or ""),
        "company_industry": str(item.get("brandIndustry") or ""),
        "job_labels": _join_api_values(item.get("jobLabels")),
        "skills": _join_api_values(item.get("skills")),
        "security_id": str(item.get("securityId") or ""),
        "lid": str(item.get("lid") or ""),
        "encrypt_job_id": encrypt_job_id,
        "encrypt_boss_id": str(item.get("encryptBossId") or ""),
        "encrypt_brand_id": encrypt_brand_id,
        "job_link": (
            f"https://www.zhipin.com/job_detail/{encrypt_job_id}.html"
            if encrypt_job_id else ""
        ),
        "company_link": (
            f"https://www.zhipin.com/gongsi/{encrypt_brand_id}.html"
            if encrypt_brand_id else ""
        ),
        "welfare": _join_api_values(item.get("welfareList")),
    }
    return result if result["title"] or result["job_link"] else None


def parse_native_position_response_body(body, http_status=200, retry_after=""):
    """Parse the page's own joblist response captured through CDP Network."""
    if http_status not in (200, "200"):
        return parse_position_page_eval_value({
            "ok": False,
            "http_status": http_status,
            "retry_after": retry_after,
            "code": None,
            "message": "",
            "jobs": [],
        })
    try:
        payload = json.loads(body) if isinstance(body, str) else body
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise PositionFetchError(f"BOSS 页面原生职位响应 JSON 解析失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise PositionFetchError("BOSS 页面原生职位响应不是对象")
    zp_data = payload.get("zpData")
    raw_jobs = zp_data.get("jobList", []) if isinstance(zp_data, dict) else []
    jobs = []
    if isinstance(raw_jobs, list):
        for item in raw_jobs:
            normalized = normalize_position_api_job(item)
            if normalized is not None:
                jobs.append(normalized)
    envelope = {
        "ok": payload.get("code") in (0, "0"),
        "http_status": http_status,
        "retry_after": retry_after,
        "code": payload.get("code"),
        "message": payload.get("message") or payload.get("msg") or "",
        "has_more": zp_data.get("hasMore") if isinstance(zp_data, dict) else None,
        "jobs": jobs,
    }
    return parse_position_page_eval_value(envelope)


def is_expected_joblist_url(url, expected_params):
    """Match the page-owned joblist response and validate parameters when exposed."""
    try:
        parsed = urlparse(str(url or ""))
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    except (TypeError, ValueError):
        return False
    if parsed.path != API_JOB_LIST_PATH:
        return False
    return all(
        key not in params
        or str(params.get(key) or ("1" if key == "page" else "")) == str(value)
        for key, value in expected_params.items()
    )


def atomic_write_json(path, data):
    """写临时文件并原子替换，避免中断留下半个 JSON。"""
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp_path = f"{path}.{os.getpid()}.{time.time_ns()}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, path)


def load_jsonl_records(path, tolerate_truncated_tail=False):
    """Load JSON objects from JSONL and recover one interrupted final append."""
    records = []
    truncate_at = None
    with open(path, "rb") as f:
        line_number = 0
        while True:
            line_start = f.tell()
            raw_line = f.readline()
            if not raw_line:
                break
            line_number += 1
            if not raw_line.strip():
                continue
            try:
                encoding = "utf-8-sig" if line_number == 1 else "utf-8"
                value = json.loads(raw_line.decode(encoding))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                has_more_content = bool(f.read().strip())
                if tolerate_truncated_tail and not has_more_content:
                    truncate_at = line_start
                    log.warning("修复 JSONL 未完整写入的末行: %s:%s", path, line_number)
                    break
                raise ValueError(f"JSONL 解析失败 {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL 记录不是对象 {path}:{line_number}")
            records.append(value)
    if truncate_at is not None:
        with open(path, "r+b") as f:
            f.truncate(truncate_at)
            f.flush()
            os.fsync(f.fileno())
    return records


def load_list_data_file(path):
    """Load a list-data JSON object or a raw one-record-per-line JSONL file."""
    expanded_path = os.path.abspath(os.path.expanduser(path))
    if expanded_path.lower().endswith(".jsonl"):
        jobs = load_jsonl_records(expanded_path)
        return {
            "source_file": expanded_path,
            "total": len(jobs),
            "jobs": jobs,
        }
    with open(expanded_path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"source_file": expanded_path, "total": len(data), "jobs": data}
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("输入 JSON 必须是职位数组或包含 jobs 数组的对象")
    return data


def detail_record_key(record):
    """Return a stable identity for resume and duplicate protection."""
    if not isinstance(record, dict):
        return ""
    for field in ("job_id", "record_id", "encrypt_job_id", "job_link", "detail_url"):
        value = str(record.get(field) or "").strip()
        if value:
            return f"{field}:{value}"
    return ""


def detail_checkpoint_path(output_path, checkpoint_path=None):
    if checkpoint_path:
        return os.path.abspath(os.path.expanduser(checkpoint_path))
    expanded = os.path.abspath(os.path.expanduser(output_path))
    stem, _ = os.path.splitext(expanded)
    return f"{stem}_checkpoint.jsonl"


def load_existing_detail_records(output_path):
    """Load prior successful detail records so the output file is the resume source."""
    if not output_path or not os.path.exists(output_path):
        return []
    if output_path.lower().endswith(".jsonl"):
        return load_jsonl_records(output_path, tolerate_truncated_tail=True)
    with open(output_path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("详情输出 JSON 必须是数组")
    return [record for record in data if isinstance(record, dict)]


def load_terminal_detail_failure_keys(checkpoint_path, max_failed_events=2):
    """Load details that failed their bounded retry budget in two runs."""
    if not checkpoint_path or not os.path.exists(checkpoint_path):
        return set()
    failures = {}
    for event in load_jsonl_records(checkpoint_path, tolerate_truncated_tail=True):
        if event.get("status") != "failed":
            continue
        key = str(event.get("key") or detail_record_key(event) or "").strip()
        if key:
            failures[key] = failures.get(key, 0) + 1
    return {
        key for key, count in failures.items()
        if count >= max_failed_events
    }


def append_jsonl_record(path, record):
    """Append and fsync one recoverable JSONL event."""
    expanded_path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(expanded_path) or ".", exist_ok=True)
    with open(expanded_path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def persist_detail_record(output_path, record, all_records):
    if output_path.lower().endswith(".jsonl"):
        append_jsonl_record(output_path, record)
    else:
        atomic_write_json(output_path, all_records)


def detail_transport_error_types():
    """Build concrete external-transport exception types after lazy imports load."""
    error_types = [OSError, TimeoutError, KeyError]
    request_exceptions = getattr(requests, "exceptions", None) if requests is not None else None
    if request_exceptions is not None and hasattr(request_exceptions, "RequestException"):
        error_types.append(request_exceptions.RequestException)
    if websocket is not None and hasattr(websocket, "WebSocketException"):
        error_types.append(websocket.WebSocketException)
    return tuple(error_types)


def position_artifact_paths(output_path, checkpoint_path=None, coverage_output=None):
    output_path = os.path.abspath(os.path.expanduser(output_path))
    stem, extension = os.path.splitext(output_path)
    if extension.lower() != ".json":
        stem = output_path
    return (
        output_path,
        os.path.abspath(os.path.expanduser(checkpoint_path or f"{stem}_checkpoint.json")),
        os.path.abspath(os.path.expanduser(coverage_output or f"{stem}_coverage.json")),
    )


def position_category_match(category):
    keys = (
        "position_code", "position_name",
        "category_level_1_code", "category_level_1_raw",
        "category_level_2_code", "category_level_2_raw",
        "category_level_3_code", "category_level_3_raw",
        "category_path_raw", "category_paths_raw", "category_paths",
    )
    return {key: category.get(key) for key in keys}


def decorate_job_with_position(job, category, list_url=""):
    """把查询所用官方职业代码和完整三级路径写入职位记录。"""
    decorated = dict(job)
    for key in (
        "record_id", "site_name", "content_category", "title_raw",
        "price_evidence", "list_url", "detail_url",
        "position_code", "position_name",
        "category_level_1_code", "category_level_1_raw",
        "category_level_2_code", "category_level_2_raw",
        "category_level_3_code", "category_level_3_raw",
        "category_path_raw", "category_paths_raw",
        "category_source", "category_source_url", "category_identity_verified",
    ):
        decorated[key] = category.get(key)
    title = str(decorated.get("title") or "")
    salary = str(decorated.get("salary") or "")
    detail_url = str(decorated.get("job_link") or "")
    source_key = str(
        decorated.get("encrypt_job_id")
        or detail_url
        or f"{title}|{decorated.get('boss_name') or ''}|{category.get('position_code') or ''}"
    )
    decorated.update({
        "site_name": "boss",
        "content_category": "full_time_job",
        "title_raw": title,
        "list_url": list_url,
        "detail_url": detail_url or None,
        "record_id": f"boss-{hashlib.sha256(f'boss|{source_key}'.encode('utf-8')).hexdigest()[:20]}",
        "price_evidence": ([{
            "raw_text": salary,
            "price_type": "salary_range",
            "price_source": "api_response",
            "evidence_url": list_url,
            "evidence_location": "job list response salaryDesc",
            "package_name": None,
            "currency_text": None,
            "period_text": None,
            "negotiable": ("面议" in salary or "报价" in salary),
            "estimated_by_platform": False,
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }] if salary else []),
    })
    decorated["category_matches"] = [position_category_match(category)]
    return decorated


def merge_position_jobs(existing_jobs, new_jobs):
    """按 job_id 幂等合并；同一职位命中多个职业时保留全部匹配。"""
    merged = [dict(job) for job in existing_jobs if isinstance(job, dict)]
    by_id = {job.get("job_id"): job for job in merged if job.get("job_id")}
    added = 0
    for new_job in new_jobs:
        job_id = new_job.get("job_id")
        if not job_id or job_id not in by_id:
            copied = dict(new_job)
            merged.append(copied)
            if job_id:
                by_id[job_id] = copied
            added += 1
            continue
        current = by_id[job_id]
        matches = list(current.get("category_matches") or [])
        match_keys = {
            (str(item.get("position_code") or ""), str(item.get("category_path_raw") or ""))
            for item in matches if isinstance(item, dict)
        }
        for match in new_job.get("category_matches") or []:
            key = (str(match.get("position_code") or ""), str(match.get("category_path_raw") or ""))
            if key not in match_keys:
                matches.append(match)
                match_keys.add(key)
        current["category_matches"] = matches
        paths = list(current.get("category_paths_raw") or [])
        for path in new_job.get("category_paths_raw") or []:
            if path not in paths:
                paths.append(path)
        current["category_paths_raw"] = paths
    return merged, added


def build_position_run_config(keyword, city_code, max_pages, per_position_limit,
                              filters, numeric_price_only, position_codes):
    return {
        "keyword": keyword,
        "city_code": city_code,
        "max_pages_per_position": max_pages,
        "per_position_limit": per_position_limit,
        "filters": dict(sorted((filters or {}).items())),
        "numeric_price_only": bool(numeric_price_only),
        "position_codes": list(position_codes) if position_codes else None,
    }


def position_catalog_signature(catalog):
    payload = [
        {
            "position_code": item.get("position_code"),
            "position_name": item.get("position_name"),
            "category_paths_raw": item.get("category_paths_raw", []),
        }
        for item in catalog
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_position_checkpoint(path, run_config):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        raise PositionCatalogError(f"断点文件无法读取: {path}: {e}") from e
    if not isinstance(state, dict) or state.get("schema_version") != POSITION_CHECKPOINT_SCHEMA:
        raise PositionCatalogError(f"断点文件版本不兼容: {path}")
    if state.get("run_config") != run_config:
        raise PositionCatalogError(
            f"断点参数与本次运行不一致: {path}；请使用新的 --output/--checkpoint"
        )
    if state.get("checkpoint_status") == "awaiting_catalog":
        return normalize_position_checkpoint_state(state)
    if not isinstance(state.get("catalog"), list) or not state["catalog"]:
        raise PositionCatalogError(f"断点文件缺少职业目录: {path}")
    return normalize_position_checkpoint_state(state)


def persist_position_login_probe_restriction(
        output_path, checkpoint_path, coverage_output, run_config, city_name,
        login_result, *, block_cooldown_seconds=DEFAULT_POSITION_BLOCK_COOLDOWN_SECONDS,
        now=None):
    """在职业目录请求前持久化登录探测限制，不伪造空目录或完成状态。"""
    output_path, checkpoint_path, _ = position_artifact_paths(
        output_path, checkpoint_path, coverage_output,
    )
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()

    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            raise PositionCatalogError(
                f"断点文件无法读取: {checkpoint_path}: {e}"
            ) from e
        if not isinstance(state, dict) or state.get("schema_version") != POSITION_CHECKPOINT_SCHEMA:
            raise PositionCatalogError(f"断点文件版本不兼容: {checkpoint_path}")
        if state.get("run_config") != run_config:
            raise PositionCatalogError(
                f"断点参数与本次运行不一致: {checkpoint_path}；"
                "请使用新的 --output/--checkpoint"
            )
        if (
            state.get("checkpoint_status") != "awaiting_catalog"
            and (not isinstance(state.get("catalog"), list) or not state["catalog"])
        ):
            raise PositionCatalogError(f"断点文件缺少职业目录: {checkpoint_path}")
    else:
        state = {
            "schema_version": POSITION_CHECKPOINT_SCHEMA,
            "checkpoint_status": "awaiting_catalog",
            "run_config": run_config,
            "city_name": city_name,
            "completed_position_codes": [],
            "failed_positions": {},
            "deferred_positions": {},
            "position_stats": {},
            "created_at": current.isoformat(),
        }

    previous = state.get("site_restriction") or {}
    try:
        previous_attempts = int(previous.get("attempt_count", 0) or 0)
    except (TypeError, ValueError):
        previous_attempts = 0
    attempt_count = previous_attempts + 1
    cooldown_seconds = position_block_cooldown_seconds(
        attempt_count,
        base_seconds=block_cooldown_seconds,
    )
    state["site_restriction"] = {
        "scope": "login_probe",
        "position_code": None,
        "blocked_at": current.isoformat(),
        "cooldown_until": (current + timedelta(seconds=cooldown_seconds)).isoformat(),
        "cooldown_seconds": cooldown_seconds,
        "attempt_count": attempt_count,
        "failure_type": "LoginProbeRestricted",
        "code": login_result.code,
        "message": login_result.message,
    }
    state["updated_at"] = current.isoformat()
    atomic_write_json(checkpoint_path, state)
    return checkpoint_path, state["site_restriction"]


def normalize_position_checkpoint_state(state):
    """补齐分类失败状态，并兼容仅含 ``failed_positions`` 的旧断点。"""
    try:
        empty_confirmation_version = int(
            state.get("empty_confirmation_version", 0) or 0
        )
    except (TypeError, ValueError):
        empty_confirmation_version = 0
    if empty_confirmation_version < POSITION_EMPTY_CONFIRMATION_VERSION:
        stats = state.get("position_stats")
        stats = stats if isinstance(stats, dict) else {}
        legacy_empty_codes = set()
        for code, detail in stats.items():
            if not isinstance(detail, dict) or detail.get("stop_reason") != "empty":
                continue
            try:
                pages_fetched = int(detail.get("pages_fetched", 0) or 0)
            except (TypeError, ValueError):
                pages_fetched = 0
            if pages_fetched < 2:
                legacy_empty_codes.add(str(code))
        state["completed_position_codes"] = [
            str(code)
            for code in state.get("completed_position_codes") or []
            if str(code) not in legacy_empty_codes
        ]
        for code in legacy_empty_codes:
            stats.pop(code, None)
        state["position_stats"] = stats
        state["empty_confirmation_version"] = POSITION_EMPTY_CONFIRMATION_VERSION

    completed = {
        str(code) for code in state.get("completed_position_codes") or [] if str(code)
    }
    terminal = {}
    deferred = {}

    def normalize_failure(code, value):
        failure = dict(value) if isinstance(value, dict) else {"error": str(value or "")}
        try:
            failure["attempt_count"] = max(1, int(failure.get("attempt_count", 1) or 1))
        except (TypeError, ValueError):
            failure["attempt_count"] = 1
        return str(code), failure

    for code, value in (state.get("deferred_positions") or {}).items():
        code, failure = normalize_failure(code, value)
        if code and code not in completed:
            failure["terminal"] = False
            deferred[code] = failure

    # schema v1 早期版本把临时限制和普通错误都写入 failed_positions。
    # 只有显式终态，或已耗尽普通分类错误预算的记录，才保留为 failed。
    for code, value in (state.get("failed_positions") or {}).items():
        code, failure = normalize_failure(code, value)
        if not code or code in completed:
            continue
        stop_reason = str(failure.get("stop_reason") or "")
        is_terminal = failure.get("terminal") is True or (
            stop_reason == "category_error"
            and failure["attempt_count"] >= MAX_POSITION_CATEGORY_ERROR_ATTEMPTS
        )
        if is_terminal:
            failure["terminal"] = True
            terminal[code] = failure
            deferred.pop(code, None)
        elif code not in terminal:
            failure["terminal"] = False
            deferred[code] = failure

    state["failed_positions"] = terminal
    state["deferred_positions"] = deferred
    return state


def build_position_work_queue(catalog, state):
    """未尝试职业优先，非终态失败职业统一排到本次队尾。"""
    completed = {str(code) for code in state.get("completed_position_codes") or []}
    failed = {str(code) for code in (state.get("failed_positions") or {})}
    deferred = {str(code) for code in (state.get("deferred_positions") or {})}
    unattempted = []
    retry_later = []
    for category in catalog:
        code = str(category.get("position_code") or "")
        if code in completed or code in failed:
            continue
        if code in deferred:
            retry_later.append(category)
        else:
            unattempted.append(category)
    return unattempted + retry_later


def load_position_output_jobs(path, run_config):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        raise PositionCatalogError(f"职业采样输出无法读取: {path}: {e}") from e
    if data.get("mode") != "position_sampling" or data.get("run_config") != run_config:
        raise PositionCatalogError(
            f"输出文件不是同一职业采样任务: {path}；请更换 --output"
        )
    jobs = data.get("jobs", [])
    if not isinstance(jobs, list):
        raise PositionCatalogError(f"职业采样输出 jobs 不是列表: {path}")
    return jobs


def build_position_output(state, jobs):
    stats = state.get("position_stats", {})
    return {
        "mode": "position_sampling",
        "keyword": state["run_config"]["keyword"],
        "city": state.get("city_name", ""),
        "city_code": state["run_config"]["city_code"],
        "run_config": state["run_config"],
        "catalog_source_url": state.get("catalog_source_url", ""),
        "catalog_signature": state.get("catalog_signature", ""),
        "position_codes_total": len(state.get("catalog", [])),
        "position_paths_total": sum(
            len(item.get("category_paths", [])) for item in state.get("catalog", [])
        ),
        "positions_completed": len(state.get("completed_position_codes", [])),
        "filtered_out_count": sum(
            int(item.get("filtered_out_count", 0) or 0) for item in stats.values()
        ),
        "total": len(jobs),
        "scraped_at": datetime.now().isoformat(),
        "jobs": jobs,
    }


def build_position_coverage_report(state, jobs):
    catalog = state.get("catalog", [])
    catalog_codes = {item["position_code"] for item in catalog}
    completed = set(state.get("completed_position_codes", [])) & catalog_codes
    failures = {
        code: failure for code, failure in (state.get("failed_positions") or {}).items()
        if code in catalog_codes and code not in completed
    }
    deferred = {
        code: failure for code, failure in (state.get("deferred_positions") or {}).items()
        if code in catalog_codes and code not in completed and code not in failures
    }
    pending_codes = catalog_codes - completed - set(failures) - set(deferred)
    quota_target = int(state.get("run_config", {}).get("per_position_limit", 0) or 0)
    quota_summary = {
        "quota_target": quota_target,
        "quota_met": 0,
        "exhausted_below_quota": 0,
        "empty": 0,
        "page_limit_below_quota": 0,
        "failed": len(failures),
    }
    stats = state.get("position_stats", {})
    positions = []
    for category in catalog:
        code = category["position_code"]
        status = (
            "completed" if code in completed
            else "failed" if code in failures
            else "deferred" if code in deferred
            else "pending"
        )
        stat = stats.get(code, {})
        records_fetched = int(stat.get("records_fetched", 0) or 0)
        stop_reason = (
            stat.get("stop_reason", "")
            or (failures.get(code) or deferred.get(code) or {}).get("stop_reason", "")
        )
        if status == "failed":
            quota_status = "failed"
        elif status != "completed":
            quota_status = status
        elif quota_target and records_fetched >= quota_target:
            quota_status = "quota_met"
            quota_summary["quota_met"] += 1
        elif stop_reason == "empty":
            quota_status = "empty"
            quota_summary["empty"] += 1
        elif stop_reason == "exhausted":
            quota_status = "exhausted_below_quota"
            quota_summary["exhausted_below_quota"] += 1
        else:
            quota_status = "page_limit_below_quota"
            quota_summary["page_limit_below_quota"] += 1
        positions.append({
            "position_code": code,
            "position_name": category["position_name"],
            "category_path_raw": category["category_path_raw"],
            "category_paths_raw": category["category_paths_raw"],
            "status": status,
            "quota_target": quota_target,
            "quota_status": quota_status,
            "records_fetched": records_fetched,
            "unique_jobs_added": int(stat.get("unique_jobs_added", 0) or 0),
            "filtered_out_count": int(stat.get("filtered_out_count", 0) or 0),
            "pages_fetched": int(stat.get("pages_fetched", 0) or 0),
            "stop_reason": stop_reason,
            "error": (failures.get(code) or deferred.get(code) or {}).get("error", ""),
            "attempt_count": int(
                (failures.get(code) or deferred.get(code) or {}).get("attempt_count", 0) or 0
            ),
        })

    level_1 = {}
    for category in catalog:
        code = category["position_code"]
        for path in category.get("category_paths", []):
            level_code = path["category_level_1_code"]
            bucket = level_1.setdefault(level_code, {
                "category_level_1_code": level_code,
                "category_level_1_raw": path["category_level_1_raw"],
                "position_codes": set(),
                "completed_position_codes": set(),
                "pending_position_codes": set(),
                "deferred_position_codes": set(),
                "failed_position_codes": set(),
                "positions_with_jobs": set(),
            })
            bucket["position_codes"].add(code)
            if code in completed:
                bucket["completed_position_codes"].add(code)
            elif code in failures:
                bucket["failed_position_codes"].add(code)
            elif code in deferred:
                bucket["deferred_position_codes"].add(code)
            else:
                bucket["pending_position_codes"].add(code)
            if int((stats.get(code) or {}).get("records_fetched", 0) or 0) > 0:
                bucket["positions_with_jobs"].add(code)
    level_1_rows = []
    for bucket in level_1.values():
        total = len(bucket["position_codes"])
        completed_count = len(bucket["completed_position_codes"])
        level_1_rows.append({
            "category_level_1_code": bucket["category_level_1_code"],
            "category_level_1_raw": bucket["category_level_1_raw"],
            "position_codes_total": total,
            "position_codes_completed": completed_count,
            "position_codes_pending": len(bucket["pending_position_codes"]),
            "position_codes_deferred": len(bucket["deferred_position_codes"]),
            "position_codes_failed": len(bucket["failed_position_codes"]),
            "positions_with_jobs": len(bucket["positions_with_jobs"]),
            "coverage_rate": round(completed_count / total, 6) if total else 0.0,
        })
    level_1_rows.sort(key=lambda row: row["category_level_1_code"])

    total_codes = len(catalog)
    total_paths = sum(len(item.get("category_paths", [])) for item in catalog)
    completed_paths = sum(
        len(item.get("category_paths", []))
        for item in catalog if item["position_code"] in completed
    )
    with_jobs = sum(
        1 for code in completed
        if int((stats.get(code) or {}).get("records_fetched", 0) or 0) > 0
    )
    return {
        "mode": "position_sampling_coverage",
        "generated_at": datetime.now().isoformat(),
        "run_config": state["run_config"],
        "catalog_source_url": state.get("catalog_source_url", ""),
        "catalog_signature": state.get("catalog_signature", ""),
        "position_codes_total": total_codes,
        "position_codes_completed": len(completed),
        "position_codes_pending": len(pending_codes),
        "position_codes_deferred": len(deferred),
        "position_codes_failed": len(failures),
        "position_codes_remaining": len(pending_codes) + len(deferred),
        "quota_summary": quota_summary,
        "positions_with_jobs": with_jobs,
        "positions_empty": max(0, len(completed) - with_jobs),
        "position_code_coverage_rate": round(len(completed) / total_codes, 6) if total_codes else 0.0,
        "position_paths_total": total_paths,
        "position_paths_completed": completed_paths,
        "position_paths_pending": sum(
            len(item.get("category_paths", []))
            for item in catalog if item["position_code"] in pending_codes
        ),
        "position_paths_deferred": sum(
            len(item.get("category_paths", []))
            for item in catalog if item["position_code"] in deferred
        ),
        "position_paths_failed": sum(
            len(item.get("category_paths", []))
            for item in catalog if item["position_code"] in failures
        ),
        "position_path_coverage_rate": round(completed_paths / total_paths, 6) if total_paths else 0.0,
        "records_fetched": sum(
            int(item.get("records_fetched", 0) or 0) for item in stats.values()
        ),
        "unique_jobs": len(jobs),
        "filtered_out_count": sum(
            int(item.get("filtered_out_count", 0) or 0) for item in stats.values()
        ),
        "site_restriction": state.get("site_restriction"),
        "by_level_1": level_1_rows,
        "positions": positions,
    }


def _response_header(headers, name):
    if not isinstance(headers, dict):
        return ""
    needle = str(name).lower()
    for key, value in headers.items():
        if str(key).lower() == needle:
            return str(value or "")
    return ""


def capture_native_joblist_response(cdp, sid, navigation_url, expected_params,
                                    description,
                                    timeout=POSITION_NATIVE_RESPONSE_TIMEOUT_SECONDS):
    """Navigate through the official UI and capture its own joblist response."""
    cdp.send(
        "Network.enable",
        {
            "maxTotalBufferSize": 10 * 1024 * 1024,
            "maxResourceBufferSize": 2 * 1024 * 1024,
        },
        sid,
    )
    cdp.clear_events(sid)
    navigation = cdp.send(
        "Page.navigate",
        {"url": navigation_url, "transitionType": "link"},
        sid,
        timeout=timeout,
    )
    if navigation.get("error"):
        raise PositionFetchError(
            f"BOSS 页面导航失败: {navigation['error'].get('message', '未知错误')}"
        )
    error_text = (navigation.get("result") or {}).get("errorText")
    if error_text:
        raise PositionFetchError(f"BOSS 页面导航失败: {error_text}")

    deadline = time.time() + timeout
    responses = {}
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise PositionNativeResponseTimeoutError(
                f"未捕获 BOSS 页面原生职位响应: {description}"
            )
        try:
            event = cdp.recv_event(sid=sid, timeout=remaining)
        except TimeoutError as exc:
            raise PositionNativeResponseTimeoutError(
                f"未捕获 BOSS 页面原生职位响应: {description}"
            ) from exc
        method = event.get("method")
        params = event.get("params") or {}
        request_id = str(params.get("requestId") or "")

        if method == "Network.responseReceived":
            response = params.get("response") or {}
            response_url = response.get("url") or ""
            if not is_expected_joblist_url(response_url, expected_params):
                continue
            http_status = int(response.get("status") or 0)
            retry_after = _response_header(response.get("headers"), "Retry-After")
            if http_status != 200:
                return None, http_status, retry_after
            responses[request_id] = {
                "http_status": http_status,
                "retry_after": retry_after,
            }
            continue

        if method == "Network.loadingFailed" and request_id in responses:
            raise PositionFetchError(
                "BOSS 页面原生职位响应加载失败: "
                f"{params.get('errorText') or '未知网络错误'}"
            )

        if method != "Network.loadingFinished" or request_id not in responses:
            continue
        body_response = cdp.send(
            "Network.getResponseBody",
            {"requestId": request_id},
            sid,
            timeout=min(10, max(1, remaining)),
        )
        if body_response.get("error"):
            raise PositionFetchError(
                "读取 BOSS 页面原生职位响应失败: "
                f"{body_response['error'].get('message', '未知错误')}"
            )
        body_result = body_response.get("result") or {}
        body = body_result.get("body")
        if body_result.get("base64Encoded") and isinstance(body, str):
            try:
                body = base64.b64decode(body).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise PositionFetchError("BOSS 页面原生职位响应 Base64 解码失败") from exc
        metadata = responses[request_id]
        return body, metadata["http_status"], metadata["retry_after"]


def fetch_native_position_page(cdp, sid, navigation_url, position_code, page,
                               timeout=POSITION_NATIVE_RESPONSE_TIMEOUT_SECONDS):
    """Navigate like the official UI and consume that page's own joblist XHR."""
    body, http_status, retry_after = capture_native_joblist_response(
        cdp,
        sid,
        navigation_url,
        {"position": str(position_code), "page": str(page)},
        f"position={position_code}, page={page}",
        timeout=timeout,
    )
    return parse_native_position_response_body(
        body,
        http_status=http_status,
        retry_after=retry_after,
    )


def fetch_position_page(cdp, sid, api_url, request_limit,
                        retries=POSITION_PAGE_RETRIES, navigation_url=None):
    """经官方页面原生请求获取职业页；旧注入路径仅保留给非导航调用。"""
    last_error = None
    attempts = retries + 1
    api_params = dict(parse_qsl(urlparse(api_url).query, keep_blank_values=True))
    position_code = api_params.get("position", "")
    page = api_params.get("page", "1")
    for attempt in range(1, attempts + 1):
        incr_request(request_limit)
        try:
            if navigation_url:
                return fetch_native_position_page(
                    cdp, sid, navigation_url, position_code, page,
                )
            js = FETCH_POSITION_API_JS_TEMPLATE.replace("__API_URL__", api_url)
            return parse_position_page_eval_value(cdp.eval_js(js, sid))
        except (PositionFetchBlockedError, PositionSessionRequiredError):
            raise
        except PositionNativeResponseTimeoutError:
            # Repeating a native-response wait on the same target cannot repair
            # a stalled search tab. Let the category runner replace that tab.
            raise
        except (PositionFetchError, TimeoutError, OSError, RuntimeError) as e:
            last_error = e
            if attempt < attempts:
                delay = min(2 ** (attempt - 1), 4)
                log.warning("职业职位页请求失败 (%s/%s)，%.0fs 后重试: %s", attempt, attempts, delay, e)
                time.sleep(delay)
    raise PositionFetchError(f"职业职位页请求失败: {last_error}") from last_error


def is_recoverable_position_tab_error(error):
    """只把原生职位响应等待超时视为可通过换 tab 恢复。"""
    current = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(
            current,
            (PositionFetchBlockedError, PositionSessionRequiredError, PositionFetchCooldownError),
        ):
            return False
        if isinstance(current, PositionNativeResponseTimeoutError):
            return True
        if (
            isinstance(current, PositionFetchError)
            and "未捕获 BOSS 页面原生职位响应" in str(current)
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def replace_position_page_session(cdp, target_id, session_id):
    """在同一 CDP 浏览器 profile 内创建新搜索 tab，再回收旧 tab。"""
    new_target_id, new_session_id = create_page_session(cdp, background=False)
    if target_id:
        try:
            cdp.send("Target.closeTarget", {"targetId": target_id})
        except (TimeoutError, OSError, RuntimeError):
            log.warning("恢复职业采样时关闭失效搜索 tab 失败")
    if session_id:
        cdp.clear_events(session_id)
    return new_target_id, new_session_id


def collect_position_jobs(cdp, sid, category, keyword, city_code, max_pages,
                          per_position_limit, filters, numeric_price_only,
                          request_limit, page_delay_range=POSITION_PAGE_DELAY_RANGE):
    jobs_out = []
    seen = set()
    filtered_out = 0
    pages_fetched = 0
    stop_reason = "page_limit"
    for page in range(1, max_pages + 1):
        params = {
            "scene": "1",
            "query": keyword,
            "city": city_code,
            "position": category["position_code"],
            "page": page,
            "pageSize": POSITION_API_PAGE_SIZE,
        }
        for key, value in (filters or {}).items():
            if value:
                params[key] = value
        api_url = f"{API_JOB_LIST_PATH}?{urlencode(params)}"
        list_filters = dict(filters or {})
        list_filters["position"] = category["position_code"]
        list_url = build_search_url(keyword, city_code, page, list_filters)
        # First page follows the official UI and establishes the page-owned
        # session. Later pages use that same browser context with the exact API
        # page parameter; navigating the SPA repeatedly can replay page 1.
        navigation_url = list_url if page == 1 else None
        page_jobs, has_more = fetch_position_page(
            cdp, sid, api_url, request_limit, navigation_url=navigation_url,
        )
        pages_fetched += 1
        if not page_jobs and page == 1:
            time.sleep(random.uniform(*page_delay_range))
            page_jobs, has_more = fetch_position_page(
                cdp, sid, api_url, request_limit, navigation_url=list_url,
            )
            pages_fetched += 1
        if not page_jobs:
            stop_reason = "empty" if page == 1 else "exhausted"
            break
        for job in page_jobs:
            key = (
                job.get("encrypt_job_id")
                or job.get("job_link")
                or "|".join((
                    str(job.get("title") or ""),
                    str(job.get("boss_name") or ""),
                    str(job.get("location") or ""),
                ))
            )
            if not key or key in seen:
                continue
            seen.add(key)
            if numeric_price_only and not has_numeric_salary(job.get("salary")):
                filtered_out += 1
                continue
            job["job_id"] = hashlib.md5(key.encode()).hexdigest()[:16]
            jobs_out.append(decorate_job_with_position(job, category, list_url=list_url))
            if len(jobs_out) >= per_position_limit:
                stop_reason = "quota_reached"
                break
        if len(jobs_out) >= per_position_limit:
            break
        if (
            has_more is False
            or (has_more is None and len(page_jobs) < POSITION_API_PAGE_SIZE)
        ):
            stop_reason = "exhausted"
            break
        if page < max_pages:
            time.sleep(random.uniform(*page_delay_range))
    return {
        "jobs": jobs_out,
        "records_fetched": len(jobs_out),
        "filtered_out_count": filtered_out,
        "pages_fetched": pages_fetched,
        "stop_reason": stop_reason,
    }


def scrape_position_categories(keyword, city_input, max_pages, filters, output_path,
                               cdp_port=DEFAULT_CDP_PORT, fmt="json",
                               per_position_limit=DEFAULT_PER_POSITION_LIMIT,
                               checkpoint_path=None, coverage_output=None,
                               position_codes=None, numeric_price_only=False,
                               catalog=None, max_positions=None,
                               category_interval_seconds=DEFAULT_POSITION_CATEGORY_INTERVAL_SECONDS,
                               category_jitter_seconds=DEFAULT_POSITION_CATEGORY_JITTER_SECONDS,
                               block_cooldown_seconds=DEFAULT_POSITION_BLOCK_COOLDOWN_SECONDS,
                               page_delay_min_seconds=POSITION_PAGE_DELAY_RANGE[0],
                               page_delay_max_seconds=POSITION_PAGE_DELAY_RANGE[1],
                               batch_size=DEFAULT_POSITION_BATCH_SIZE,
                               batch_cooldown_seconds=DEFAULT_POSITION_BATCH_COOLDOWN_SECONDS,
                               max_consecutive_errors=DEFAULT_POSITION_MAX_CONSECUTIVE_ERRORS):
    """按官方三级职业代码依次采样，自动断点续跑并生成覆盖报告。"""
    global _request_counter
    if not 1 <= per_position_limit <= MAX_PER_POSITION_LIMIT:
        raise ValueError(
            f"per_position_limit 必须在 1-{MAX_PER_POSITION_LIMIT} 之间"
        )
    if not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"max_pages 必须在 1-{MAX_PAGES} 之间")
    max_pages = effective_position_page_limit(max_pages, per_position_limit)
    if max_positions is not None and max_positions < 1:
        raise ValueError("max_positions 必须是正整数")
    category_interval_seconds, category_jitter_seconds = validate_position_category_pacing(
        category_interval_seconds, category_jitter_seconds,
    )
    block_cooldown_seconds = position_block_cooldown_seconds(
        1, base_seconds=block_cooldown_seconds,
    )
    page_delay_min_seconds = float(page_delay_min_seconds)
    page_delay_max_seconds = float(page_delay_max_seconds)
    if not 0 <= page_delay_min_seconds <= page_delay_max_seconds <= 60:
        raise ValueError("职业分页等待必须满足 0 <= min <= max <= 60 秒")
    batch_size = int(batch_size)
    if not 0 <= batch_size <= MAX_POSITION_CATEGORIES:
        raise ValueError(f"batch_size 必须在 0-{MAX_POSITION_CATEGORIES} 之间")
    batch_cooldown_seconds = float(batch_cooldown_seconds)
    if not 0 <= batch_cooldown_seconds <= 14_400:
        raise ValueError("batch_cooldown_seconds 必须在 0-14400 秒之间")
    max_consecutive_errors = int(max_consecutive_errors)
    if not 1 <= max_consecutive_errors <= 20:
        raise ValueError("max_consecutive_errors 必须在 1-20 之间")
    page_delay_range = (page_delay_min_seconds, page_delay_max_seconds)

    city_name, city_code = resolve_city(city_input)
    output_path = output_path or default_output_path("jobs")
    output_path, checkpoint_path, coverage_output = position_artifact_paths(
        output_path, checkpoint_path, coverage_output,
    )
    requested_codes = [str(code).strip() for code in position_codes or []]
    run_config = build_position_run_config(
        keyword, city_code, max_pages, per_position_limit,
        filters, numeric_price_only, requested_codes,
    )
    state = load_position_checkpoint(checkpoint_path, run_config)
    if state is not None and state.get("checkpoint_status") == "awaiting_catalog":
        cooldown = active_position_cooldown(state, [])
        if cooldown:
            raise PositionFetchCooldownError(describe_position_cooldown(cooldown))
    if state is None or state.get("checkpoint_status") == "awaiting_catalog":
        full_catalog = list(catalog) if catalog is not None else load_position_catalog(city_code)
        selected_catalog = select_position_categories(full_catalog, requested_codes)
        if not selected_catalog:
            raise PositionCatalogError("没有可采集的职业代码")
        created_at = (state or {}).get("created_at") or datetime.now().isoformat()
        state = {
            "schema_version": POSITION_CHECKPOINT_SCHEMA,
            "run_config": run_config,
            "city_name": city_name,
            "catalog_source_url": selected_catalog[0].get("category_source_url", ""),
            "catalog_signature": position_catalog_signature(selected_catalog),
            "catalog": selected_catalog,
            "completed_position_codes": [],
            "failed_positions": {},
            "deferred_positions": {},
            "position_stats": {},
            "created_at": created_at,
            "updated_at": datetime.now().isoformat(),
        }
    state = normalize_position_checkpoint_state(state)
    selected_catalog = state["catalog"]
    jobs = load_position_output_jobs(output_path, run_config)
    completed = set(state.get("completed_position_codes", []))
    all_pending = build_position_work_queue(selected_catalog, state)
    pending = all_pending[:max_positions] if max_positions is not None else all_pending
    request_limit = min(
        MAX_POSITION_RUN_REQUESTS,
        max(
            1,
            len(selected_catalog)
            * max_pages
            * (POSITION_PAGE_RETRIES + 1)
            * (POSITION_TAB_RECOVERY_RETRIES + 1),
        ),
    )
    _request_counter = 0

    atomic_write_json(checkpoint_path, state)
    atomic_write_json(output_path, build_position_output(state, jobs))
    atomic_write_json(coverage_output, build_position_coverage_report(state, jobs))
    if not pending:
        failed_count = len(state.get("failed_positions") or {})
        if failed_count:
            print(
                f"职业采样无可运行类别：{failed_count} 个职业已达错误预算并标记 failed。"
                f"覆盖报告: {coverage_output}"
            )
        else:
            print(f"职业采样已完成，无需重复抓取。覆盖报告: {coverage_output}")
        if fmt == "csv":
            write_csv(os.path.splitext(output_path)[0] + ".csv", jobs)
        return build_position_output(state, jobs)

    cooldown = active_position_cooldown(state, all_pending)
    if cooldown:
        raise PositionFetchCooldownError(describe_position_cooldown(cooldown))

    print("=== BOSS 官方职业分类采样 ===")
    print(
        f"城市: {city_name} | 职业代码: {len(selected_catalog)} | "
        f"总待采集: {len(all_pending)} | 本批次: {len(pending)} | "
        f"每类最多: {per_position_limit} | 每类最多页数: {max_pages}"
    )
    print(
        f"类别间隔: {category_interval_seconds:.1f}s + "
        f"0-{category_jitter_seconds:.1f}s 抖动"
    )
    print(
        f"分页间隔: {page_delay_min_seconds:.1f}-{page_delay_max_seconds:.1f}s | "
        f"批次: {batch_size or '关闭'} | 批次冷却: {batch_cooldown_seconds:.1f}s | "
        f"连续原生响应错误熔断: {max_consecutive_errors} 类"
    )
    print(f"输出: {output_path}")
    print(f"断点: {checkpoint_path}")
    print(f"覆盖报告: {coverage_output}\n")

    cdp = CDPSession(cdp_port)
    target_id = None
    session_id = None
    interrupted = False
    consecutive_native_errors = 0
    try:
        # 整个职业批次只激活这一个 tab，后续分类都复用它；避免每类抢焦点，
        # 同时不伪造 background tab 的 visibility/hasFocus 组合。
        target_id, session_id = create_page_session(cdp, background=False)

        for index, category in enumerate(pending, start=1):
            code = category["position_code"]
            print(
                f"--- [{index}/{len(pending)}] {code} "
                f"{category['category_path_raw']} ---"
            )
            try:
                tab_recovery_attempts = 0
                while True:
                    try:
                        result = collect_position_jobs(
                            cdp, session_id, category, keyword, city_code,
                            max_pages, per_position_limit, filters,
                            numeric_price_only, request_limit,
                            page_delay_range=page_delay_range,
                        )
                        break
                    except (PositionFetchError, TimeoutError, OSError, RuntimeError) as e:
                        if (
                            tab_recovery_attempts >= POSITION_TAB_RECOVERY_RETRIES
                            or not is_recoverable_position_tab_error(e)
                        ):
                            raise
                        tab_recovery_attempts += 1
                        print(
                            "  当前搜索 tab 未捕获原生响应；"
                            f"重建 tab 后重试本职业 "
                            f"({tab_recovery_attempts}/{POSITION_TAB_RECOVERY_RETRIES})..."
                        )
                        target_id, session_id = replace_position_page_session(
                            cdp, target_id, session_id,
                        )
            except KeyboardInterrupt:
                interrupted = True
                print("\n已中断；已完成职业保留在断点中，下次同命令自动续跑。")
                break
            except (PositionFetchError, TimeoutError, OSError, RuntimeError) as e:
                blocked = isinstance(e, PositionFetchBlockedError)
                session_required = isinstance(e, PositionSessionRequiredError)
                failures = state.setdefault("failed_positions", {})
                deferred = state.setdefault("deferred_positions", {})
                previous = deferred.get(code) if isinstance(deferred.get(code), dict) else {}
                attempt_count = int(previous.get("attempt_count", 0) or 0) + 1
                failed_at = datetime.now().astimezone()
                failure = {
                    "error": str(e),
                    "failure_type": type(e).__name__,
                    "stop_reason": (
                        "site_restriction" if blocked
                        else "session_required" if session_required
                        else "category_error"
                    ),
                    "attempt_count": attempt_count,
                    "failed_at": failed_at.isoformat(),
                    "terminal": False,
                }
                if blocked:
                    retry_after = getattr(e, "retry_after_seconds", None)
                    cooldown_seconds = position_block_cooldown_seconds(
                        attempt_count,
                        retry_after_seconds=retry_after,
                        base_seconds=block_cooldown_seconds,
                    )
                    failure.update({
                        "retry_after_seconds": retry_after,
                        "cooldown_seconds": cooldown_seconds,
                        "cooldown_until": (failed_at + timedelta(seconds=cooldown_seconds)).isoformat(),
                    })
                    state["site_restriction"] = {
                        "position_code": code,
                        "blocked_at": failure["failed_at"],
                        "cooldown_until": failure["cooldown_until"],
                        "cooldown_seconds": cooldown_seconds,
                        "attempt_count": attempt_count,
                        "failure_type": failure["failure_type"],
                    }
                terminal = (
                    not blocked
                    and not session_required
                    and attempt_count >= MAX_POSITION_CATEGORY_ERROR_ATTEMPTS
                )
                failure["terminal"] = terminal
                if terminal:
                    failures[code] = failure
                    deferred.pop(code, None)
                else:
                    deferred[code] = failure
                    failures.pop(code, None)
                state["updated_at"] = datetime.now().isoformat()
                atomic_write_json(checkpoint_path, state)
                atomic_write_json(coverage_output, build_position_coverage_report(state, jobs))
                if blocked:
                    print("  检测到站点限制；失败断点已保存，本次立即停止且不重试。")
                    raise
                if session_required:
                    print("  登录会话失效；当前职业已延后，下次续跑排在未尝试职业之后。")
                    raise
                # Any consecutive category-level transport/response failures
                # indicate that the shared browser session may be gone. Stop
                # instead of walking the remaining catalog with zero output.
                consecutive_native_errors += 1
                if consecutive_native_errors >= max_consecutive_errors:
                    print(
                        f"  连续 {consecutive_native_errors} 个职业请求失败；"
                        "已保存断点并触发全局熔断。"
                    )
                    raise PositionFetchError(
                        f"连续 {consecutive_native_errors} 个职业请求失败，停止本次运行"
                    ) from e
                if terminal:
                    print(
                        f"  普通错误已达 {MAX_POSITION_CATEGORY_ERROR_ATTEMPTS} 次；"
                        "标记终态 failed，继续其余职业。"
                    )
                else:
                    print(
                        f"  普通错误第 {attempt_count}/{MAX_POSITION_CATEGORY_ERROR_ATTEMPTS} 次；"
                        "已延后，继续其余职业。"
                    )
                if index < len(pending):
                    delay, batch_boundary = position_pause_seconds(
                        index, len(pending), category_interval_seconds,
                        category_jitter_seconds, batch_size, batch_cooldown_seconds,
                    )
                    label = "批次冷却" if batch_boundary else "节流等待"
                    print(f"  {label} {delay:.1f}s 后处理下一个职业...")
                    time.sleep(delay)
                continue

            jobs, added = merge_position_jobs(jobs, result["jobs"])
            consecutive_native_errors = 0
            # 先写数据，再标记职业完成；两步之间中断时重跑仍可幂等合并。
            atomic_write_json(output_path, build_position_output(state, jobs))
            completed.add(code)
            state["completed_position_codes"] = [
                item["position_code"] for item in selected_catalog
                if item["position_code"] in completed
            ]
            state.setdefault("failed_positions", {}).pop(code, None)
            state.setdefault("deferred_positions", {}).pop(code, None)
            if (state.get("site_restriction") or {}).get("position_code") == code:
                state.pop("site_restriction", None)
            state.setdefault("position_stats", {})[code] = {
                **{key: value for key, value in result.items() if key != "jobs"},
                "unique_jobs_added": added,
                "completed_at": datetime.now().isoformat(),
            }
            state["updated_at"] = datetime.now().isoformat()
            atomic_write_json(checkpoint_path, state)
            atomic_write_json(output_path, build_position_output(state, jobs))
            atomic_write_json(coverage_output, build_position_coverage_report(state, jobs))
            print(
                f"  完成: {result['records_fetched']} 条，新增唯一职位 {added}，"
                f"原因={result['stop_reason']}，总唯一职位 {len(jobs)}"
            )
            if index < len(pending):
                delay, batch_boundary = position_pause_seconds(
                    index, len(pending), category_interval_seconds,
                    category_jitter_seconds, batch_size, batch_cooldown_seconds,
                )
                label = "批次冷却" if batch_boundary else "节流等待"
                print(f"  {label} {delay:.1f}s 后处理下一个职业...")
                time.sleep(delay)
    finally:
        if target_id:
            try:
                cdp.send("Target.closeTarget", {"targetId": target_id})
            except (TimeoutError, OSError, RuntimeError):
                log.warning("职业采样结束时关闭临时页面失败")
        cdp.close()

    final_output = build_position_output(state, jobs)
    atomic_write_json(output_path, final_output)
    atomic_write_json(coverage_output, build_position_coverage_report(state, jobs))
    if fmt == "csv":
        write_csv(os.path.splitext(output_path)[0] + ".csv", jobs)
    remaining = len(build_position_work_queue(selected_catalog, state))
    failed_count = len(state.get("failed_positions") or {})
    if not interrupted and remaining:
        print(f"\n本批次完成: {len(jobs)} 个唯一职位，仍有 {remaining} 个职业待续跑")
    elif not interrupted and failed_count:
        print(
            f"\n可运行职业已处理完：{len(jobs)} 个唯一职位，"
            f"{failed_count} 个职业为终态 failed"
        )
    elif not interrupted:
        print(f"\n职业采样完成: {len(jobs)} 个唯一职位")
    print(f"覆盖报告: {coverage_output}")
    return final_output


def build_detail_url(job):
    """Build the URL used for detail navigation without mutating job_link."""
    link = job.get("job_link", "")
    if not link:
        return ""

    parsed = urlparse(link)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    existing_keys = {key for key, _ in params}
    for query_key, job_key in (("lid", "lid"), ("securityId", "security_id")):
        value = job.get(job_key) or job.get(query_key) or ""
        if value and query_key not in existing_keys:
            params.append((query_key, value))
            existing_keys.add(query_key)

    return urlunparse(parsed._replace(query=urlencode(params)))


def find_latest_detail_file(result_dir=DEFAULT_RESULT_DIR):
    pattern = os.path.join(result_dir, "boss_details_*.json")
    files = [path for path in glob.glob(pattern) if os.path.isfile(path)]
    if not files:
        return None
    return max(files, key=lambda path: (os.path.getmtime(path), path))


def detail_candidate_paths(input_path=None, detail_output=None, result_dir=DEFAULT_RESULT_DIR):
    candidates = []
    if detail_output:
        candidates.append(detail_output)
    if input_path:
        directory = os.path.dirname(input_path) or "."
        basename = os.path.basename(input_path)
        if basename.startswith("boss_jobs_"):
            candidates.append(os.path.join(directory, basename.replace("boss_jobs_", "boss_details_", 1)))
    latest = find_latest_detail_file(result_dir)
    if latest:
        candidates.append(latest)

    deduped = []
    seen = set()
    for path in candidates:
        normalized = os.path.abspath(os.path.expanduser(path))
        if normalized not in seen:
            deduped.append(path)
            seen.add(normalized)
    return deduped


def load_existing_details(input_path=None, detail_output=None, result_dir=DEFAULT_RESULT_DIR):
    for path in detail_candidate_paths(input_path, detail_output, result_dir):
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                details = json.load(f)
            if isinstance(details, list):
                print(f"加载详情文件: {path}")
                return details
        except (json.JSONDecodeError, OSError, ValueError) as e:
            log.warning(f"无法加载详情文件 {path}: {e}")
    return None


# ============================================================
# 抓取列表
# ============================================================
def scrape_list(keyword, city_input, max_pages, filters, output_path,
                cdp_port=DEFAULT_CDP_PORT, fmt="json", allow_dom_fallback=False,
                numeric_price_only=False):
    city_name, city_code = resolve_city(city_input)
    cdp = CDPSession(cdp_port)
    all_jobs = []
    seen = set()
    filtered_out_count = 0
    if not output_path:
        output_path = default_output_path("jobs")

    # 显示筛选条件
    filter_desc = []
    if filters.get("scale"):
        for k, v in SCALE_MAP.items():
            if v == filters["scale"]:
                filter_desc.append(f"规模={k}")
    if filters.get("stage"):
        for k, v in STAGE_MAP.items():
            if v == filters["stage"]:
                filter_desc.append(f"融资={k}")
    if filters.get("salary"):
        for k, v in SALARY_MAP.items():
            if v == filters["salary"]:
                filter_desc.append(f"薪资={k}")
    if filters.get("experience"):
        for k, v in EXPERIENCE_MAP.items():
            if v == filters["experience"]:
                filter_desc.append(f"经验={k}")
    if filters.get("degree"):
        for k, v in DEGREE_MAP.items():
            if v == filters["degree"]:
                filter_desc.append(f"学历={k}")
    if filters.get("industry"):
        for k, v in INDUSTRY_MAP.items():
            if v == filters["industry"]:
                filter_desc.append(f"行业={k}")

    print(f"=== BOSS直聘抓取 ===")
    print(f"关键词: {keyword} | 城市: {city_name} | 页数: {max_pages}")
    if filter_desc:
        print(f"筛选: {' | '.join(filter_desc)}")
    if numeric_price_only:
        print("薪资限制: 仅保留价格原文含非零数字 [1-9] 的职位；不会扩大抓取页数")
    print()

    tid, sid = create_page_session(cdp)

    def human_scroll(cdp, sid):
        """模拟人类滚动: 随机次数、随机距离、随机停顿，偶尔回滚一点"""
        total_scrolls = random.randint(3, 6)
        for i in range(total_scrolls):
            # 大部分往下滚，偶尔往上回滚一点（模拟阅读回看）
            if random.random() < 0.15:
                delta = -random.randint(50, 150)
            else:
                delta = random.randint(150, 500)
            cdp.eval_js(f"window.scrollBy(0,{delta})", sid)
            # 滚动间隔随机：有时快速连续滚，有时停下来"看"
            if random.random() < 0.3:
                time.sleep(random.uniform(2.0, 4.0))
            else:
                time.sleep(random.uniform(0.5, 1.5))

    def human_mouse_jitter(cdp, sid):
        """偶尔移动鼠标位置，模拟人在页面上活动"""
        if random.random() < 0.4:
            x = random.randint(100, 800)
            y = random.randint(100, 600)
            cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": x, "y": y
            }, sid)

    try:
        for pg in range(1, max_pages + 1):
            print(f"--- [{pg}/{max_pages} 页, {len(all_jobs)} 条已抓] ---")
            incr_request()

            # 第一页：导航到搜索页建立 cookie/session
            if pg == 1:
                url = build_search_url(keyword, city_code, pg, filters)
                cdp.send("Page.navigate", {"url": url}, sid)
                time.sleep(random.uniform(6, 10))
                human_scroll(cdp, sid)
                human_mouse_jitter(cdp, sid)

            # 优先用 API 获取明文数据
            api_params = {
                "scene": "1",
                "query": keyword,
                "city": city_code,
                "page": pg,
                "pageSize": 30,
            }
            for k, v in filters.items():
                if v:
                    api_params[k] = v
            api_url = f"{API_JOB_LIST_PATH}?{urlencode(api_params)}"
            api_js = FETCH_API_JS_TEMPLATE.replace("__API_URL__", api_url)
            val = cdp.eval_js(api_js, sid)

            jobs = parse_api_jobs_eval_value(val)

            # DOM 提取的薪资可能是加密字体，默认禁用；只有显式允许时才降级。
            if should_use_dom_fallback(jobs, allow_dom_fallback):
                log.warning("⚠️ API 获取失败，回退到 DOM 提取（此方式已弃用，数据可能不完整）")
                if pg > 1:
                    url = build_search_url(keyword, city_code, pg, filters)
                    cdp.send("Page.navigate", {"url": url}, sid)
                    time.sleep(random.uniform(4, 8))
                    human_scroll(cdp, sid)
                val = cdp.eval_js(EXTRACT_LIST_JS, sid)
                if val:
                    try:
                        jobs = json.loads(val) if isinstance(val, str) else val
                    except (json.JSONDecodeError, ValueError):
                        print(f"  ⚠️ JSON 解析失败")
                        jobs = []
            elif not jobs:
                log.warning("⚠️ API 未返回职位数据，已跳过 DOM fallback；如需强制降级可加 --allow-dom-fallback")

            if not jobs:
                print("  ⚠️ 无数据")
                continue

            new = 0
            page_filtered = 0
            for j in jobs:
                key = j.get('job_link') or j['title']
                j['job_id'] = hashlib.md5(key.encode()).hexdigest()[:16]
                if key in seen:
                    continue
                seen.add(key)
                if numeric_price_only and not has_numeric_salary(j.get("salary")):
                    filtered_out_count += 1
                    page_filtered += 1
                    continue
                all_jobs.append(j)
                new += 1
                salary = j.get('salary','?')
                scale = j.get('company_scale', '')
                extra = f" | {scale}" if scale else ""
                print(f"  ✓ {j['title']} | {salary} | {j.get('location','')} | {j.get('boss_name','')}{extra}")

            filtered_desc = f", 过滤 {page_filtered}" if numeric_price_only else ""
            print(f"  本页 {len(jobs)} 条, 新增 {new}{filtered_desc}, 累计 {len(all_jobs)}")

            # 每页抓完就写入文件，异常退出也能保留
            if output_path:
                extra_filtered = flush_jobs(output_path, {
                    "keyword": keyword,
                    "city": city_name,
                    "filters": filters,
                    "filter_desc": filter_desc,
                    "numeric_price_only": numeric_price_only,
                    "filtered_out_count": filtered_out_count,
                    "scraped_at": datetime.now().isoformat(),
                }, all_jobs, numeric_price_only=numeric_price_only)
                filtered_out_count += extra_filtered

            if pg < max_pages:
                d = random.uniform(12, 22)
                print(f"  翻页等待 {d:.0f}s...\n")
                time.sleep(d)

    except KeyboardInterrupt:
        print("\n中断")
    except RuntimeError as e:
        print(f"\n⚠️ {e}")
    finally:
        cdp.send("Target.closeTarget", {"targetId": tid})
        cdp.close()

    print(f"\n{'='*60}")
    print(f"完成: {len(all_jobs)} 条")

    if all_jobs or numeric_price_only:
        # 最终写入（含时间戳更新）
        extra_filtered = flush_jobs(output_path, {
            "keyword": keyword,
            "city": city_name,
            "filters": filters,
            "filter_desc": filter_desc,
            "numeric_price_only": numeric_price_only,
            "filtered_out_count": filtered_out_count,
            "scraped_at": datetime.now().isoformat(),
        }, all_jobs, numeric_price_only=numeric_price_only)
        filtered_out_count += extra_filtered
        print(f"已保存: {output_path}")

        # CSV 导出
        if all_jobs and fmt == "csv":
            csv_path = output_path.rsplit(".", 1)[0] + ".csv"
            write_csv(csv_path, all_jobs)
    else:
        print("无数据")

    if numeric_price_only:
        print(f"明确数字薪资过滤: 保留 {len(all_jobs)} 条，过滤 {filtered_out_count} 条")

    return {
        "keyword": keyword,
        "city": city_name,
        "total": len(all_jobs),
        "filtered_out_count": filtered_out_count,
        "jobs": all_jobs,
    }


# ============================================================
# 抓取详情
# ============================================================
def build_detail_record(job, extracted):
    link = job.get("job_link") or job.get("detail_url", "")
    record = {
        "job_id": job.get("job_id", ""),
        "title": job.get("title", ""),
        "company": job.get("boss_name", ""),
        "salary": job.get("salary", ""),
        "salary_source": job.get("salary_source", ""),
        "location": job.get("location", ""),
        "tags_list": job.get("tags", ""),
        "job_link": link,
        "link": link,
        "skill_tags": extracted.get("tags", []),
        "jd": extracted.get("jd", ""),
    }
    for key in (
        "record_id", "site_name", "content_category", "title_raw",
        "price_evidence", "list_url", "detail_url",
        "position_code", "position_name",
        "category_level_1_code", "category_level_1_raw",
        "category_level_2_code", "category_level_2_raw",
        "category_level_3_code", "category_level_3_raw",
        "category_path_raw", "category_paths_raw", "category_matches",
        "category_source", "category_source_url", "category_identity_verified",
    ):
        if key in job:
            record[key] = job[key]
    return record


def scrape_details(list_data, max_details=None, output_path=None,
                   cdp_port=DEFAULT_CDP_PORT, fmt="json", checkpoint_path=None,
                   load_timeout=DEFAULT_DETAIL_LOAD_TIMEOUT_SECONDS,
                   min_text_length=DEFAULT_DETAIL_MIN_TEXT_LENGTH,
                   retries=DEFAULT_DETAIL_RETRIES,
                   gap_min=DEFAULT_DETAIL_GAP_RANGE[0],
                   gap_max=DEFAULT_DETAIL_GAP_RANGE[1], resume=True):
    """Supplement validated BOSS JDs with bounded waits and resumable output.

    JSONL is recommended for large runs: each successful record is appended and
    fsynced before its success event is written to the separate checkpoint log.
    Existing successful identities are skipped on restart.
    """
    jobs = list(list_data.get("jobs", []))
    if not output_path:
        output_path = default_output_path("details")
    output_path = os.path.abspath(os.path.expanduser(output_path))
    checkpoint_path = detail_checkpoint_path(output_path, checkpoint_path)

    if max_details is not None and max_details < 1:
        raise ValueError("max_details 必须是正整数")
    if retries < 0:
        raise ValueError("detail retries 不能小于 0")
    if load_timeout <= 0 or min_text_length < 1:
        raise ValueError("detail load timeout 和 min text length 必须为正数")
    if gap_min < 0 or gap_max < gap_min:
        raise ValueError("detail gap 必须满足 0 <= min <= max")

    existing = load_existing_detail_records(output_path) if resume else []
    results = []
    completed_keys = set()
    for record in existing:
        key = detail_record_key(record)
        if key and key in completed_keys:
            continue
        results.append(record)
        if key:
            completed_keys.add(key)
    terminal_failure_keys = (
        load_terminal_detail_failure_keys(checkpoint_path) if resume else set()
    )

    available_request_budget = max(0, MAX_API_REQUESTS - _request_counter)
    requested_limit = max_details if max_details is not None else available_request_budget
    run_limit = min(requested_limit, available_request_budget)
    print(
        f"\n=== 补采岗位详情 (输入 {len(jobs)} 个，已完成 {len(completed_keys)} 个，"
        f"终止失败 {len(terminal_failure_keys)} 个，"
        f"本次最多 {run_limit} 个) ===\n"
    )

    attempted = 0
    succeeded = 0
    failed = 0
    duplicate_or_completed = 0
    missing_link = 0
    seen_input_keys = set()
    cdp = None
    target_id = None
    session_id = None

    def close_current_session():
        nonlocal cdp, target_id, session_id
        if cdp is not None and target_id:
            try:
                cdp.send("Target.closeTarget", {"targetId": target_id})
            except detail_transport_error_types() as exc:
                log.debug("关闭详情 target 失败: %s", exc)
        if cdp is not None:
            try:
                cdp.close()
            except detail_transport_error_types() as exc:
                log.debug("关闭详情 CDP 连接失败: %s", exc)
        cdp = None
        target_id = None
        session_id = None

    def ensure_current_session():
        nonlocal cdp, target_id, session_id
        if cdp is None:
            cdp = CDPSession(cdp_port)
            target_id, session_id = create_page_session(cdp)
        return cdp, target_id, session_id

    # Fail before consuming the per-job failure budget when the logged-in CDP
    # browser is not running. Repeating the same connection error for every
    # input record cannot recover and only produces misleading failure events.
    if run_limit and jobs:
        try:
            ensure_current_session()
        except detail_transport_error_types() as exc:
            close_current_session()
            raise RuntimeError(
                f"BOSS CDP endpoint unavailable on port {cdp_port}; start the logged-in browser first"
            ) from exc

    try:
        for index, job in enumerate(jobs):
            if not isinstance(job, dict):
                continue
            link = str(job.get("job_link") or job.get("detail_url") or "").strip()
            if not link:
                missing_link += 1
                continue
            key = detail_record_key(job) or f"job_link:{link}"
            if key in completed_keys or key in terminal_failure_keys or key in seen_input_keys:
                duplicate_or_completed += 1
                continue
            seen_input_keys.add(key)
            if attempted >= run_limit:
                break

            attempted += 1
            incr_request()
            title = str(job.get("title") or job.get("title_raw") or "")
            company = str(job.get("boss_name") or "")
            print(f"[{attempted}/{run_limit}] {company} - {title}")
            started_at = time.monotonic()
            detail_url = build_detail_url({**job, "job_link": link})
            extracted = None
            last_error = None

            for attempt_number in range(1, retries + 2):
                try:
                    current_cdp, _, current_session_id = ensure_current_session()
                    navigation = current_cdp.send(
                        "Page.navigate", {"url": detail_url}, current_session_id
                    )
                    navigation_error = str(
                        navigation.get("result", {}).get("errorText") or ""
                    )
                    if navigation_error:
                        raise DetailExtractionError(
                            f"detail navigation failed: {navigation_error}"
                        )
                    extracted = wait_for_job_description(
                        current_cdp,
                        current_session_id,
                        timeout=load_timeout,
                        min_length=min_text_length,
                    )
                    break
                except DetailLoginRequiredError as exc:
                    append_jsonl_record(checkpoint_path, {
                        "timestamp": datetime.now().isoformat(),
                        "key": key,
                        "record_id": job.get("record_id"),
                        "job_id": job.get("job_id"),
                        "detail_url": detail_url,
                        "status": "login_required",
                        "attempts": attempt_number,
                        "error": str(exc),
                    })
                    raise RuntimeError(
                        "BOSS detail login expired; stopped before writing truncated JD data"
                    ) from exc
                except DetailAccessBlockedError as exc:
                    append_jsonl_record(checkpoint_path, {
                        "timestamp": datetime.now().isoformat(),
                        "key": key,
                        "record_id": job.get("record_id"),
                        "job_id": job.get("job_id"),
                        "detail_url": detail_url,
                        "status": "verification_required",
                        "attempts": attempt_number,
                        "error": str(exc),
                    })
                    raise RuntimeError(
                        "BOSS detail access verification required; checkpoint saved"
                    ) from exc
                except DetailExtractionError as exc:
                    last_error = exc
                except detail_transport_error_types() as exc:
                    last_error = exc
                    close_current_session()

                if attempt_number <= retries:
                    retry_delay = min(2.0, 0.5 * attempt_number)
                    print(f"  第 {attempt_number} 次失败，{retry_delay:g}s 后重试: {last_error}")
                    time.sleep(retry_delay)

            if extracted is None:
                failed += 1
                append_jsonl_record(checkpoint_path, {
                    "timestamp": datetime.now().isoformat(),
                    "key": key,
                    "record_id": job.get("record_id"),
                    "job_id": job.get("job_id"),
                    "detail_url": detail_url,
                    "status": "failed",
                    "attempts": retries + 1,
                    "error": str(last_error or "unknown detail extraction error"),
                })
                print(f"  跳过无效详情页: {last_error}")
            else:
                detail = build_detail_record(job, extracted)
                results.append(detail)
                persist_detail_record(output_path, detail, results)
                completed_keys.add(key)
                succeeded += 1
                append_jsonl_record(checkpoint_path, {
                    "timestamp": datetime.now().isoformat(),
                    "key": key,
                    "record_id": detail.get("record_id"),
                    "job_id": detail.get("job_id"),
                    "detail_url": detail_url,
                    "status": "success",
                    "attempts": attempt_number,
                    "jd_length": len(detail.get("jd") or ""),
                })
                print(
                    f"  JD: {len(detail.get('jd') or '')} 字 "
                    f"({time.monotonic() - started_at:.1f}s)"
                )

            if attempted < run_limit and gap_max:
                time.sleep(random.uniform(gap_min, gap_max))
    finally:
        close_current_session()

    if not os.path.exists(output_path) and not output_path.lower().endswith(".jsonl"):
        atomic_write_json(output_path, results)
    print(
        f"\n详情补采完成: 本次尝试 {attempted}，成功 {succeeded}，失败 {failed}，"
        f"已完成/重复 {duplicate_or_completed}，缺链接 {missing_link}"
    )
    print(f"详情数据: {output_path}")
    print(f"断点日志: {checkpoint_path}")

    if fmt == "csv":
        csv_path = output_path.rsplit(".", 1)[0] + ".csv"
        write_detail_csv(csv_path, results)
    return results


# ============================================================
# 动态技术术语提取
# ============================================================
def extract_tech_terms_from_jds(details, search_keyword=""):
    """从 JD 文本中动态提取高频技术术语。

    策略：
    1. 保留一个小的基础术语列表用于匹配
    2. 对 JD 正文做分词频率分析，提取高频词
    3. 将搜索关键词拆分后加入

    Args:
        details: 详情列表，每个含 "jd" 字段
        search_keyword: 搜索关键词

    Returns:
        去重后的术语列表
    """
    # 基础技术术语（小列表，用于精确匹配）
    base_tech_terms = [
        "Java", "Spring", "Redis", "MySQL", "Kafka", "Flink", "Spark",
        "Go", "Python", "微服务", "分布式", "高并发",
        "AI", "LLM", "RAG", "Agent", "SQL", "Linux",
    ]

    # 从搜索关键词中提取词
    keyword_terms = []
    for word in re.split(r'[\s,，、]+', search_keyword):
        word = word.strip()
        if len(word) >= 2:
            keyword_terms.append(word)

    # 从 JD 文本中提取高频词
    word_freq = Counter()
    for d in details:
        jd_text = d.get("jd", "")
        if not jd_text:
            continue
        # 提取英文技术词（连续 2+ 字母的词）
        en_words = re.findall(r'\b[A-Za-z][A-Za-z0-9._-]+\b', jd_text)
        for w in en_words:
            if len(w) >= 2 and len(w) <= 30:
                word_freq[w] += 1
        # 提取中文技术词（简单：连续中文字符 2-6 个）
        cn_words = re.findall(r'[\u4e00-\u9fff]{2,6}', jd_text)
        # 过滤常见非技术中文词
        stop_words = {
            "任职", "要求", "岗位", "职责", "描述", "优先", "具有",
            "负责", "相关", "经验", "能力", "以上", "及其", "工作",
            "开发", "团队", "项目", "公司", "业务", "熟悉", "熟练",
            "了解", "掌握", "参与", "完成", "进行", "能够", "学历",
            "专业", "提供", "福利", "加入", "我们", "我们只", "是通过",
            "就是", "已经", "可以", "这个", "那个", "什么", "怎么",
            "欢迎", "期待", "为你", "为你提供",
        }
        for w in cn_words:
            if w not in stop_words:
                word_freq[w] += 1

    # 取频率最高的动态词（至少出现 2 次，取 top 60）
    dynamic_terms = [
        word for word, count in word_freq.most_common(60)
        if count >= 2
    ]

    # 合并去重：基础 + 关键词 + 动态提取
    all_terms = list(dict.fromkeys(
        base_tech_terms + keyword_terms + dynamic_terms
    ))
    return all_terms


# ============================================================
# 分析报告
# ============================================================
def analyze(list_data, details=None, search_keyword=""):
    jobs = list_data.get("jobs", [])
    print(f"\n{'='*60}")
    print(f"  分析报告: {list_data.get('keyword','')} @ {list_data.get('city','')}")
    print(f"  共 {len(jobs)} 条职位")
    print(f"{'='*60}")

    # 1. 薪资分析
    print(f"\n--- 薪资分布 ---")
    salary_ranges = Counter()
    for j in jobs:
        s = j.get("salary", "")
        if "K" in s:
            salary_ranges[s] += 1
        elif "元/天" in s:
            salary_ranges[s] += 1
        else:
            salary_ranges["未标注"] += 1
    for s, c in salary_ranges.most_common(15):
        bar = "█" * c
        print(f"  {s:<20} {c:>3}  {bar}")

    # 2. 经验要求
    print(f"\n--- 经验要求 ---")
    exp_count = Counter()
    for j in jobs:
        tags = j.get("tags", "")
        for t in tags.split(" | "):
            if "年" in t or "应届" in t or "在校" in t or "经验不限" in t:
                exp_count[t] += 1
    for e, c in exp_count.most_common():
        print(f"  {e:<15} {c}")

    # 3. 学历要求
    print(f"\n--- 学历要求 ---")
    edu_count = Counter()
    for j in jobs:
        tags = j.get("tags", "")
        for t in tags.split(" | "):
            if t in ["大专", "本科", "硕士", "博士", "学历不限"]:
                edu_count[t] += 1
    for e, c in edu_count.most_common():
        print(f"  {e:<10} {c}")

    # 4. 地区分布
    print(f"\n--- 地区分布 ---")
    loc_count = Counter()
    for j in jobs:
        loc = j.get("location", "")
        # Extract district
        parts = loc.split("·")
        if len(parts) >= 2:
            loc_count[parts[1]] += 1
        elif loc:
            loc_count[loc] += 1
    for l, c in loc_count.most_common(10):
        print(f"  {l:<15} {c}")

    # 5. 公司分布
    print(f"\n--- 高频公司 ---")
    company_count = Counter()
    for j in jobs:
        c = j.get("boss_name", "")
        if c:
            company_count[c] += 1
    for c, n in company_count.most_common(10):
        print(f"  {c:<25} {n} 个岗位")

    # 6. 详情页的技能标签（如有）
    body_freq = Counter()
    if details:
        print(f"\n--- 技能要求频次（来自 JD 标签）---")
        skill_freq = Counter()
        for d in details:
            for tag in d.get("skill_tags", []):
                skill_freq[tag] += 1
        for s, c in skill_freq.most_common(25):
            bar = "█" * c
            print(f"  {s:<20} {c:>3}/{len(details)}  {bar}")

        # 7. JD 正文关键词（动态提取）
        print(f"\n--- JD 正文高频技术词 ---")
        tech_terms = extract_tech_terms_from_jds(details, search_keyword)
        for d in details:
            jd_lower = d.get("jd", "").lower()
            for term in tech_terms:
                if term.lower() in jd_lower:
                    body_freq[term] += 1
        for t, c in body_freq.most_common(25):
            pct = c / len(details) * 100
            bar = "█" * c
            print(f"  {t:<20} {c:>3}/{len(details)} ({pct:.0f}%)  {bar}")

    # 8. 简历建议
    print(f"\n--- 简历建议 ---")
    if details and body_freq:
        noise_list = {'BOSS直聘', 'boss', 'BOSS', '来自BOSS直聘', '金', '金币'}
        top_skills = [s for s, _ in Counter(
            tag for d in details for tag in d.get("skill_tags", [])
        ).most_common(10)]
        # 如果有效标签太少或都是噪音，用 JD 正文关键词代替
        valid_skills = [s for s in top_skills if len(s) >= 2 and s not in noise_list]
        if len(valid_skills) < 3:
            top_skills = [t for t, _ in body_freq.most_common(10)]
        top_body = [t for t, _ in body_freq.most_common(8)] if body_freq else []
        print(f"  技能关键词: {', '.join(top_skills)}")
        print(f"  正文高频词: {', '.join(top_body)}")
        # Experience requirement
        if exp_count:
            top_exp = exp_count.most_common(1)[0][0]
            print(f"  经验要求主流: {top_exp}")
        if edu_count:
            top_edu = edu_count.most_common(1)[0][0]
            print(f"  学历要求主流: {top_edu}")
    else:
        print("  提示: 用 --detail 抓取 JD 详情后可获得更精准的简历建议")


def parse_jobs_eval_value(value):
    if not value:
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (json.JSONDecodeError, ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def has_usable_smoke_jobs(jobs):
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if (
            job.get("title")
            and job.get("salary")
            and job.get("salary_source") == "api"
            and job.get("job_link")
        ):
            return True
    return False


def run_smoke_test(cdp_port=DEFAULT_CDP_PORT):
    """Run a real browser/API smoke test without writing result files."""
    if not require_runtime_dependencies("requests", "websocket"):
        return 1

    try:
        cdp = CDPSession(cdp_port)
        city_name, city_code = resolve_city(DEFAULT_CITY_INPUT)
        search_url = build_search_url(LOGIN_PROBE_QUERY, city_code, 1, {})
        tid, sid = create_page_session(cdp)

        print(f"打开 BOSS 搜索页: {LOGIN_PROBE_QUERY} @ {city_name}")
        cdp.send("Page.navigate", {"url": search_url}, sid)
        time.sleep(4)
        api_url = f"{API_JOB_LIST_PATH}?{urlencode({'scene': '1', 'query': LOGIN_PROBE_QUERY, 'city': city_code, 'page': 1, 'pageSize': 5})}"
        api_js = FETCH_API_JS_TEMPLATE.replace("__API_URL__", api_url)
        jobs = parse_jobs_eval_value(cdp.eval_js(api_js, sid))
        cdp.send("Target.closeTarget", {"targetId": tid})
        cdp.close()

        if has_usable_smoke_jobs(jobs):
            sample = next(job for job in jobs if job.get("salary") and job.get("job_link"))
            print(f"✅ Smoke test 通过: {sample.get('title')} | {sample.get('salary')}")
            return 0
        print("❌ Smoke test 未拿到可用职位；请检查登录态或 BOSS API 返回")
        return 1
    except (requests.ConnectionError, requests.Timeout, KeyError,
            json.JSONDecodeError, websocket.WebSocketException, TimeoutError) as e:
        print(f"❌ Smoke test 失败: {e}")
        return 1


# ============================================================
# --check 环境检查
# ============================================================
def run_check(cdp_port=DEFAULT_CDP_PORT):
    """运行环境诊断检查"""
    print("=" * 50)
    print("  BOSS直聘 CDP 环境检查")
    print("=" * 50)
    print()

    all_pass = True

    # 检查 1: Python 依赖
    print("[1/3] Python 依赖...")
    deps_ok = require_runtime_dependencies("websocket", "requests")
    if requests is not None:
        print(f"  ✅ requests 可导入")
    if websocket is not None:
        print(f"  ✅ websocket 可导入")
    if deps_ok:
        print(f"  ✅ 依赖完整")
    else:
        all_pass = False

    # 检查 2: CDP 端口连通性
    print("[2/3] CDP 端口连通性...")
    if requests is None:
        print(f"  ❌ 跳过 — 缺少 requests")
        all_pass = False
    else:
        try:
            resp = requests.get(f"http://127.0.0.1:{cdp_port}/json/version", timeout=5)
            data = resp.json()
            browser = data.get("Browser", "未知")
            print(f"  ✅ 通过 — Chrome {browser}")
        except (requests.ConnectionError, requests.Timeout):
            print(f"  ❌ 失败 — 无法连接 127.0.0.1:{cdp_port}")
            print(f"     请先启动 Chrome CDP: python3 {__file__} --setup-chrome")
            all_pass = False
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  ❌ 失败 — CDP 响应异常: {e}")
            all_pass = False

    # 检查 3: BOSS直聘登录状态
    print("[3/3] BOSS直聘登录状态...")
    if not deps_ok:
        print(f"  ❌ 跳过 — 缺少运行依赖")
        all_pass = False
    else:
        try:
            login_result = check_login_state(cdp_port)
            if login_result.status is LoginProbeStatus.AVAILABLE:
                print(f"  ✅ 已登录")
            elif login_result.status is LoginProbeStatus.EMPTY:
                print(f"  ⚠️  {describe_login_probe_result(login_result)}")
                all_pass = False
            else:
                print(f"  ❌ {describe_login_probe_result(login_result)}")
                all_pass = False
        except Exception as e:
            print(f"  ❌ 检测失败: {e}")
            all_pass = False

    print()
    if all_pass:
        print("✅ 所有检查通过，可以开始抓取")
    else:
        print("❌ 部分检查未通过，请修复后重试")
    print()

    return 0 if all_pass else 1


# ============================================================
# --setup-chrome 自动启动
# ============================================================
def prepare_cdp_profile(copy_login_state=False, reset=False, cdp_data_dir=None):
    """Prepare an isolated persistent Chrome profile for CDP."""
    cdp_data_dir = os.path.abspath(os.path.expanduser(cdp_data_dir or DEFAULT_CDP_DATA_DIR))
    cdp_default = os.path.join(cdp_data_dir, "Default")

    if reset and os.path.exists(cdp_data_dir):
        shutil.rmtree(cdp_data_dir)

    os.makedirs(cdp_default, exist_ok=True)

    copied = 0
    if copy_login_state:
        default_profile = DEFAULT_PROFILE_DIR
        default_default = os.path.join(default_profile, "Default")
        cookie_files = []
        for rel_dir in ("", "Network"):
            for name in ("Cookies", "Cookies-journal", "Cookies-wal", "Cookies-shm"):
                rel_path = os.path.join(rel_dir, name) if rel_dir else name
                cookie_files.append((os.path.join(default_default, rel_path), os.path.join(cdp_default, rel_path)))

        copy_files = [(os.path.join(default_profile, "Local State"), os.path.join(cdp_data_dir, "Local State"))]
        copy_files.extend(cookie_files)
        for src, dst in copy_files:
            if os.path.exists(src):
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    copied += 1
                except Exception as e:
                    print(f"  ⚠️  复制 {os.path.basename(src)} 失败: {e}")

    return {
        "path": cdp_data_dir,
        "copied": copied,
        "reset": reset,
        "copy_login_state": copy_login_state,
    }


def is_cdp_ready(cdp_port):
    try:
        resp = requests.get(f"http://127.0.0.1:{cdp_port}/json/version", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def is_chrome_command(command):
    lower = (command or "").lower()
    return any(token in lower for token in (
        "google chrome",
        "google-chrome",
        "chromium",
        "chrome.exe",
    ))


def normalize_profile_path(path):
    clean = (path or "").strip("\"'")
    if platform.system() == "Windows":
        return ntpath.normcase(ntpath.normpath(clean))
    return os.path.realpath(os.path.expanduser(clean))


def extract_user_data_dir(command):
    match = re.search(
        r"--user-data-dir=(?:\"([^\"]+)\"|'([^']+)'|(.+?))(?=\s+--|$)",
        command or "",
    )
    if not match:
        return None
    return next(group for group in match.groups() if group is not None).strip()


def iter_chrome_process_commands():
    """Return (pid, command line) tuples for Chrome-like browser processes."""
    if platform.system() == "Windows":
        ps_script = (
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
            "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
            )
        except Exception:
            return []
        if not (r.stdout or "").strip():
            return []
        try:
            data = json.loads(r.stdout)
        except (json.JSONDecodeError, ValueError):
            return []
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []
        processes = []
        for item in data:
            command = item.get("CommandLine") or ""
            if not is_chrome_command(command):
                continue
            try:
                processes.append((int(item.get("ProcessId")), command))
            except (TypeError, ValueError):
                continue
        return processes

    try:
        r = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5)
    except Exception:
        return []

    processes = []
    for line in r.stdout.splitlines():
        if not is_chrome_command(line):
            continue
        try:
            pid_text, command = line.strip().split(None, 1)
            pid = int(pid_text)
        except ValueError:
            continue
        processes.append((pid, command))
    return processes


def chrome_pids_for_user_data_dir(user_data_dir):
    """Return Chrome PIDs using the given user-data-dir."""
    pids = []
    real_dir = normalize_profile_path(user_data_dir)
    for pid, command in iter_chrome_process_commands():
        if "--user-data-dir=" not in command:
            continue
        path = extract_user_data_dir(command)
        if path and normalize_profile_path(path) == real_dir:
            pids.append(pid)
    return pids


def chrome_user_data_dirs_for_cdp_port(cdp_port):
    """Return user-data-dir paths for Chrome processes using the given CDP port."""
    dirs = []
    port_arg = f"--remote-debugging-port={cdp_port}"
    for _pid, command in iter_chrome_process_commands():
        if port_arg not in command:
            continue
        path = extract_user_data_dir(command)
        if path:
            dirs.append(path)
    return dirs


def cdp_port_uses_profile(cdp_port, cdp_data_dir):
    expected = normalize_profile_path(cdp_data_dir)
    return any(normalize_profile_path(path) == expected for path in chrome_user_data_dirs_for_cdp_port(cdp_port))


def terminate_process(pid, force=False):
    if platform.system() == "Windows":
        cmd = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            cmd.append("/F")
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def stop_cdp_chrome(cdp_data_dir):
    """Stop only Chrome processes that use the scraper's isolated profile."""
    pids = chrome_pids_for_user_data_dir(cdp_data_dir)
    if not pids:
        return 0

    for pid in pids:
        try:
            terminate_process(pid, force=False)
        except ProcessLookupError:
            pass
    for _ in range(10):
        time.sleep(0.5)
        if not chrome_pids_for_user_data_dir(cdp_data_dir):
            return len(pids)

    for pid in chrome_pids_for_user_data_dir(cdp_data_dir):
        try:
            terminate_process(pid, force=True)
        except ProcessLookupError:
            pass
    time.sleep(0.5)
    return len(pids)


def wait_for_cdp(cdp_port, timeout=30):
    print("等待 CDP 可用", end="")
    for _ in range(timeout):
        time.sleep(1)
        print(".", end="", flush=True)
        if is_cdp_ready(cdp_port):
            print(f"\n✅ CDP 已就绪 (端口 {cdp_port})")
            return True
    print(f"\n❌ 等待超时 ({timeout}s)，CDP 未就绪")
    print(f"   请手动检查 Chrome 是否启动，端口 {cdp_port} 是否开放")
    return False


def launch_chrome(cmd):
    kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if platform.system() == "Windows":
        creationflags = 0
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        if creationflags:
            kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def run_setup_chrome(cdp_port=DEFAULT_CDP_PORT, copy_login_state=False,
                     reset_profile=False, wait_login=True,
                     login_timeout=DEFAULT_LOGIN_TIMEOUT, cdp_data_dir=None):
    """自动配置并启动 Chrome CDP 模式"""
    if not require_runtime_dependencies("requests"):
        return 1

    print("=" * 50)
    print("  设置 Chrome CDP 调试模式")
    print("=" * 50)
    print()

    if cdp_data_dir is None:
        profile = prepare_cdp_profile(copy_login_state=copy_login_state, reset=reset_profile)
    else:
        profile = prepare_cdp_profile(
            copy_login_state=copy_login_state,
            reset=reset_profile,
            cdp_data_dir=cdp_data_dir,
        )
    cdp_data_dir = profile["path"]
    print(f"✅ 使用独立 Chrome profile: {cdp_data_dir}")
    if reset_profile:
        print("   已按 --reset-chrome-profile 重建 profile")
    if copy_login_state:
        print(f"   已复制 {profile['copied']} 个登录态文件（Local State + Cookie 相关文件）")
    else:
        print("   默认、首次启动、重复启动都不复制主 Chrome Cookie；首次使用请在此专用 Chrome 中登录 zhipin.com")

    if is_cdp_ready(cdp_port):
        if cdp_port_uses_profile(cdp_port, cdp_data_dir):
            print(f"\n✅ CDP 已就绪 (端口 {cdp_port})")
            if wait_login:
                return 0 if wait_for_login(cdp_port, timeout=login_timeout) else 1
            return 0
        print(f"\n❌ 端口 {cdp_port} 已被其他 Chrome CDP profile 占用")
        print(f"   请关闭旧 CDP Chrome，或改用 --cdp-port 指定其他端口")
        return 1

    stopped = stop_cdp_chrome(cdp_data_dir)
    if stopped:
        print(f"\n已关闭 {stopped} 个旧的 BOSS CDP Chrome 进程")

    print(f"\n启动 Chrome (CDP 端口: {cdp_port})...")
    cmd = [
        DEFAULT_CHROME_PATH,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={cdp_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
    ]
    launch_chrome(cmd)

    if not wait_for_cdp(cdp_port):
        return 1

    print()
    print("Chrome 已启动。请在这个专用浏览器中登录 zhipin.com。")
    if wait_login:
        print()
        if not wait_for_login(cdp_port, timeout=login_timeout):
            return 1
    print()
    print(f"示例:")
    print(f"  uv run python3 scripts/boss_cdp_raw.py --keyword \"AI Agent\" --city 上海 --pages 3")
    print(f"  uv run python3 scripts/boss_cdp_raw.py --check")
    print(f"  uv run python3 scripts/boss_cdp_raw.py --stop-chrome   # 抓完关闭专用 Chrome")
    print()
    return 0


def run_stop_chrome(cdp_data_dir=None):
    """关闭 BOSS 专用 CDP Chrome（按隔离 user-data-dir 精准匹配，不碰主 Chrome）。"""
    if not require_runtime_dependencies("requests"):
        return 1

    print("=" * 50)
    print("  关闭 BOSS 专用 CDP Chrome")
    print("=" * 50)
    print()

    # 只定位 scraper 专用 profile 目录，不复制、不重置
    if cdp_data_dir is None:
        profile = prepare_cdp_profile(copy_login_state=False, reset=False)
    else:
        profile = prepare_cdp_profile(
            copy_login_state=False, reset=False, cdp_data_dir=cdp_data_dir,
        )
    cdp_data_dir = profile["path"]

    stopped = stop_cdp_chrome(cdp_data_dir)
    if stopped:
        print(f"\n✅ 已关闭 {stopped} 个 BOSS 专用 Chrome 进程 (profile: {cdp_data_dir})")
    else:
        print(f"\nℹ️  没有找到运行中的 BOSS 专用 Chrome 进程 (profile: {cdp_data_dir})")
    print()
    print("提示：仅关闭 scraper 隔离 profile 的 Chrome，不影响你的主 Chrome。")
    print()
    return 0


# ============================================================
# main
# ============================================================
def main():
    p = argparse.ArgumentParser(
        description=f"BOSS直聘抓取 + 分析 (CDP Raw) v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
筛选参数示例:
  --scale 305          公司规模 (301=0-20人 302=20-99 303=100-499 304=500-999 305=1000-9999 306=10000+)
  --stage 807          融资阶段 (801=未融资 ... 807=已上市 808=不需要融资)
  --salary 406         薪资范围 (402=3K以下 403=3-5K 404=5-10K 405=10-20K 406=20-50K 407=50K+)
  --experience 105     经验要求 (108=在校生 102=应届生 101=经验不限 103=1年以内 104=1-3年 105=3-5年 106=5-10年 107=10年+)
  --degree 203         学历要求 (209=初中及以下 208=中专/中技 206=高中 202=大专 203=本科 204=硕士 205=博士)
  --industry 1001      行业 (1001=互联网 1002=电商 1003=金融 ...)

城市支持中文: --city 上海  或代码: --city 101020100

示例:
  # 基础搜索
  %(prog)s --keyword "Java 风控" --city 上海 --pages 5

  # 筛选大公司 + 高薪
  %(prog)s --keyword "Java 风控" --scale 305 --salary 406

  # 抓列表 + 详情 + 分析报告
  %(prog)s --keyword "Java 风控" --pages 3 --detail --analysis

  # 只分析已有数据
  %(prog)s --input ~/.boss-zhipin-scraper/job-result/boss_jobs_20260609_1200.json --analysis --no-detail

  # 导出 CSV
  %(prog)s --keyword "Java 风控" --pages 3 --format csv

  # 合并旧数据
  %(prog)s --keyword "Java 风控" --pages 3 --merge old_data.json

  # 覆盖官方全部三级职业，每个职业最多保留 5 条（默认关闭详情）
  %(prog)s --all-positions --city 全国 --pages 3 --per-position-limit 5 --output boss_jobs.json

  # 只跑待采队列前 3 个职业做 smoke；重复运行会继续后续职业
  %(prog)s --all-positions --max-positions 3 --pages 1 --per-position-limit 1 --output boss_smoke.json

  # 只采样指定职业代码（可重复传 --position）
  %(prog)s --position 100101 --position 100109 --city 上海 --per-position-limit 5

  # 环境检查
  %(prog)s --check

  # 浏览器/API smoke test
  %(prog)s --smoke-test

  # 启动 Chrome CDP
  %(prog)s --setup-chrome
        """)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--keyword", default=None,
                   help="搜索关键词（普通模式默认 AI Agent；职业采样模式默认不叠加关键词）")
    p.add_argument("--city", default=DEFAULT_CITY_INPUT, help=f"城市 (中文名或代码，默认 {DEFAULT_CITY_INPUT})")
    p.add_argument("--pages", type=int, default=3, help=f"抓取页数 (最大 {MAX_PAGES})")
    p.add_argument("--output", default=None, help="列表数据输出路径")
    p.add_argument("--detail-output", default=None, help="详情数据输出路径")
    p.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT,
                   help=f"CDP 调试端口 (默认 {DEFAULT_CDP_PORT})")
    p.add_argument("--cdp-data-dir", default=None,
                   help="专用 Chrome user-data-dir；默认使用 ~/.boss-zhipin-scraper/chrome-profile")
    p.add_argument("--format", default="json", choices=["json", "csv"],
                   help="输出格式 (默认 json)")
    p.add_argument("--merge", default=None,
                   help="合并已有 JSON 文件 (按 job_id 去重)")

    # 官方职业分类采样
    p.add_argument("--all-positions", action="store_true",
                   help="按 BOSS 官方全部三级职业代码依次采样")
    p.add_argument("--position", action="append", default=[], metavar="CODE",
                   help="只采样指定三级职业代码；可重复传入")
    p.add_argument("--per-position-limit", type=int, default=DEFAULT_PER_POSITION_LIMIT,
                   help=f"每个职业最多保留职位数 (默认 {DEFAULT_PER_POSITION_LIMIT}，最大 {MAX_PER_POSITION_LIMIT})")
    p.add_argument("--max-positions", type=int, default=None,
                   help="只限制本次运行的待采职业数（正整数）；默认不限制，后续同命令可续跑")
    p.add_argument("--category-interval-seconds", type=float,
                   default=DEFAULT_POSITION_CATEGORY_INTERVAL_SECONDS,
                   help=("职业分类之间的固定等待秒数 "
                         f"(默认 {DEFAULT_POSITION_CATEGORY_INTERVAL_SECONDS:g})"))
    p.add_argument("--category-jitter-seconds", type=float,
                   default=DEFAULT_POSITION_CATEGORY_JITTER_SECONDS,
                   help=("职业分类固定等待之外的随机抖动上限秒数 "
                         f"(默认 {DEFAULT_POSITION_CATEGORY_JITTER_SECONDS:g})"))
    p.add_argument("--page-delay-min-seconds", type=float,
                   default=POSITION_PAGE_DELAY_RANGE[0],
                   help="同一职业分页之间的最小等待秒数")
    p.add_argument("--page-delay-max-seconds", type=float,
                   default=POSITION_PAGE_DELAY_RANGE[1],
                   help="同一职业分页之间的最大等待秒数")
    p.add_argument("--batch-size", type=int, default=DEFAULT_POSITION_BATCH_SIZE,
                   help="每处理多少个职业执行一次批次冷却；0 表示关闭")
    p.add_argument("--batch-cooldown-seconds", type=float,
                   default=DEFAULT_POSITION_BATCH_COOLDOWN_SECONDS,
                   help="批次边界等待秒数")
    p.add_argument("--max-consecutive-errors", type=int,
                   default=DEFAULT_POSITION_MAX_CONSECUTIVE_ERRORS,
                   help="连续多少个职业原生响应错误后保存断点并停止")
    p.add_argument("--block-cooldown-seconds", type=float,
                   default=DEFAULT_POSITION_BLOCK_COOLDOWN_SECONDS,
                   help=("遇到站点限制后的首次冷却秒数，重复限制指数增加 "
                         f"(默认 {DEFAULT_POSITION_BLOCK_COOLDOWN_SECONDS:g})"))
    p.add_argument("--checkpoint", default=None,
                   help="职业采样断点路径（默认由 --output 派生）")
    p.add_argument("--coverage-output", default=None,
                   help="职业覆盖报告路径（默认由 --output 派生）")

    # 筛选参数
    p.add_argument("--scale", default=None, help="公司规模代码")
    p.add_argument("--stage", default=None, help="融资阶段代码")
    p.add_argument("--salary", default=None, help="薪资范围代码")
    p.add_argument("--experience", default=None, help="经验要求代码")
    p.add_argument("--degree", default=None, help="学历要求代码")
    p.add_argument("--industry", default=None, help="行业代码")

    # 功能开关
    p.add_argument("--detail", action="store_true", default=None,
                   help="抓取详情页 JD（普通模式默认开启，职业采样默认关闭）")
    p.add_argument("--no-detail", dest="detail", action="store_false", default=None,
                   help="不抓取详情页")
    p.add_argument("--max-details", type=int, default=None,
                   help=f"本次最多补采几个未完成详情（默认按单次请求上限 {MAX_API_REQUESTS}）")
    p.add_argument("--detail-checkpoint", default=None,
                   help="详情补采状态 JSONL；默认由 --detail-output 派生")
    p.add_argument("--detail-load-timeout", type=float,
                   default=DEFAULT_DETAIL_LOAD_TIMEOUT_SECONDS,
                   help=f"单个详情渲染等待秒数（默认 {DEFAULT_DETAIL_LOAD_TIMEOUT_SECONDS:g}）")
    p.add_argument("--detail-retries", type=int, default=DEFAULT_DETAIL_RETRIES,
                   help=f"单个详情失败后的重试次数（默认 {DEFAULT_DETAIL_RETRIES}）")
    p.add_argument("--detail-min-chars", type=int,
                   default=DEFAULT_DETAIL_MIN_TEXT_LENGTH,
                   help=f"有效职位描述最少字符数（默认 {DEFAULT_DETAIL_MIN_TEXT_LENGTH}）")
    p.add_argument("--detail-gap-min", type=float, default=DEFAULT_DETAIL_GAP_RANGE[0],
                   help=f"详情之间最小等待秒数（默认 {DEFAULT_DETAIL_GAP_RANGE[0]:g}）")
    p.add_argument("--detail-gap-max", type=float, default=DEFAULT_DETAIL_GAP_RANGE[1],
                   help=f"详情之间最大等待秒数（默认 {DEFAULT_DETAIL_GAP_RANGE[1]:g}）")
    p.add_argument("--analysis", action="store_true", help="输出分析报告")
    p.add_argument("--input", default=None,
                   help="从已有 JSON/JSONL 文件读取（跳过列表抓取）")
    p.add_argument("--allow-dom-fallback", action="store_true",
                   help="API 无数据时允许降级 DOM 提取（薪资可能受字体反爬影响，默认关闭）")
    p.add_argument("--numeric-price-only", action="store_true",
                   help="只保留薪资原文含非零数字 [1-9] 的职位；过滤项不扩展 --pages")

    # 工具命令
    p.add_argument("--check", action="store_true", help="运行环境诊断检查")
    p.add_argument("--smoke-test", action="store_true",
                   help="用真实 Chrome/CDP 跑一次 BOSS 搜索 API smoke test（不写结果文件）")
    p.add_argument("--list-cities", nargs="?", const="", default=None,
                   metavar="关键词",
                   help="打印支持的城市列表（可选关键词过滤，如 --list-cities 江）；"
                        "支持全国城市，码表见 data/city_codes.json，运行时自动从 BOSS 同步")
    p.add_argument("--list-positions", nargs="?", const="", default=None,
                   metavar="关键词",
                   help="打印 BOSS 官方三级职业代码，可按代码或分类路径关键词过滤")
    p.add_argument("--setup-chrome", action="store_true",
                   help="自动启动 Chrome CDP 调试模式")
    p.add_argument("--copy-login-state", action="store_true",
                   help="手动从主 Chrome 导入 Local State + Cookie 相关文件到独立 profile（默认、首次启动、重复启动都不复制）")
    p.add_argument("--reset-chrome-profile", action="store_true",
                   help="重建 BOSS 专用 Chrome profile，会清除此专用浏览器内的登录态")
    p.add_argument("--no-wait-login", action="store_true",
                   help="--setup-chrome 启动后不等待 BOSS 登录完成")
    p.add_argument("--login-timeout", type=int, default=DEFAULT_LOGIN_TIMEOUT,
                   help=f"--setup-chrome 等待登录完成的秒数 (默认 {DEFAULT_LOGIN_TIMEOUT})")
    p.add_argument("--stop-chrome", action="store_true",
                   help="关闭 BOSS 专用 CDP Chrome（按隔离 profile 精准匹配，不影响主 Chrome）")
    p.add_argument("--close-chrome", action="store_true",
                   help="抓取正常结束后自动关闭专用 Chrome（默认不关；异常退出不触发，保留登录态）")

    args = p.parse_args()

    position_mode = args.all_positions or bool(args.position)
    if args.all_positions and args.position:
        p.error("--all-positions 与 --position 不能同时使用")
    if position_mode and args.input:
        p.error("职业采样模式不能与 --input 同时使用")
    if position_mode and args.merge:
        p.error("职业采样已内置断点和幂等合并，不能与 --merge 同时使用")
    if position_mode and args.allow_dom_fallback:
        p.error("职业采样要求 API 明文薪资，不支持 --allow-dom-fallback")
    if not position_mode and args.max_positions is not None:
        p.error("--max-positions 只能与 --all-positions 或 --position 一起使用")
    if args.max_positions is not None and args.max_positions < 1:
        p.error("--max-positions 必须是正整数")
    if args.max_details is not None and args.max_details < 1:
        p.error("--max-details 必须是正整数")
    if args.detail_retries < 0:
        p.error("--detail-retries 不能小于 0")
    if args.detail_load_timeout <= 0 or args.detail_min_chars < 1:
        p.error("--detail-load-timeout 和 --detail-min-chars 必须为正数")
    if args.detail_gap_min < 0 or args.detail_gap_max < args.detail_gap_min:
        p.error("详情间隔必须满足 0 <= --detail-gap-min <= --detail-gap-max")
    if args.input and args.detail_output and (
        os.path.abspath(os.path.expanduser(args.input))
        == os.path.abspath(os.path.expanduser(args.detail_output))
    ):
        p.error("--detail-output 不能覆盖 --input 原始数据文件")
    if args.keyword is None:
        args.keyword = "" if position_mode else "AI Agent"
    if args.detail is None:
        args.detail = not position_mode
    if position_mode and args.output is None:
        args.output = os.path.join(DEFAULT_RESULT_DIR, DEFAULT_POSITION_OUTPUT_FILENAME)

    # --check 模式
    if args.check:
        sys.exit(run_check(args.cdp_port))

    if args.smoke_test:
        sys.exit(run_smoke_test(args.cdp_port))

    # --list-cities 模式（无需 Chrome/网络依赖，本地静态码表兜底）
    if args.list_cities is not None:
        list_cities(keyword=args.list_cities or None)
        sys.exit(0)

    if args.list_positions is not None:
        try:
            list_positions(
                keyword=args.list_positions or None,
                city_input=args.city,
            )
        except PositionCatalogError as e:
            print(f"❌ {e}")
            sys.exit(1)
        sys.exit(0)

    # --setup-chrome 模式
    if args.setup_chrome:
        sys.exit(run_setup_chrome(
            args.cdp_port,
            copy_login_state=args.copy_login_state,
            reset_profile=args.reset_chrome_profile,
            wait_login=not args.no_wait_login,
            login_timeout=args.login_timeout,
            cdp_data_dir=args.cdp_data_dir,
        ))

    # --stop-chrome 模式（关闭 BOSS 专用 CDP Chrome，独立命令）
    if args.stop_chrome:
        sys.exit(run_stop_chrome(args.cdp_data_dir))

    if not require_runtime_dependencies("requests", "websocket"):
        sys.exit(1)

    # 页数限制
    if args.pages > MAX_PAGES:
        print(f"⚠️ 页数 {args.pages} 超过上限 {MAX_PAGES}，已自动调整为 {MAX_PAGES}")
        args.pages = MAX_PAGES

    # 收集筛选条件
    filters = {}
    for key in ["scale", "stage", "salary", "experience", "degree", "industry"]:
        val = getattr(args, key)
        if val:
            filters[key] = val

    # 加载或抓取列表
    if args.input:
        try:
            list_data = load_list_data_file(args.input)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            p.error(f"输入文件加载失败: {exc}")
        print(f"从文件加载 {len(list_data.get('jobs', []))} 条: {args.input}")
        list_data, _ = apply_numeric_salary_filter(
            list_data,
            numeric_price_only=args.numeric_price_only,
        )
    else:
        if position_mode:
            try:
                cooldown = load_active_position_checkpoint_cooldown(
                    args.output,
                    args.checkpoint,
                )
            except PositionCatalogError as e:
                print(f"❌ 职业采样失败: {e}")
                sys.exit(1)
            if cooldown:
                print(f"❌ {describe_position_cooldown(cooldown)}")
                print("   冷却期内未打开 CDP，也未发送登录探测或职位请求。")
                sys.exit(1)

        # 登录状态检测
        print("检测登录状态...")
        login_result = check_login_state(args.cdp_port)
        if login_result.status is LoginProbeStatus.UNAUTHENTICATED:
            print("❌ 未检测到 BOSS直聘登录状态。请先在 Chrome 中登录 zhipin.com。")
            print(f"   可运行 --check 检查环境，或 --setup-chrome 启动 Chrome。")
            sys.exit(1)
        if login_result.status is LoginProbeStatus.RESTRICTED:
            print(f"❌ {describe_login_probe_result(login_result)}，已停止抓取。")
            if position_mode:
                try:
                    city_name, city_code = resolve_city(args.city)
                    requested_codes = [str(code).strip() for code in args.position or []]
                    effective_pages = effective_position_page_limit(
                        args.pages, args.per_position_limit,
                    )
                    run_config = build_position_run_config(
                        args.keyword, city_code, effective_pages, args.per_position_limit,
                        filters, args.numeric_price_only, requested_codes,
                    )
                    checkpoint_path, restriction = persist_position_login_probe_restriction(
                        args.output,
                        args.checkpoint,
                        args.coverage_output,
                        run_config,
                        city_name,
                        login_result,
                        block_cooldown_seconds=args.block_cooldown_seconds,
                    )
                except (PositionCatalogError, ValueError) as e:
                    print(f"   职业采样限制断点保存失败: {e}")
                    sys.exit(1)
                print(
                    f"   限制冷却已保存到 {checkpoint_path}；"
                    f"约 {math.ceil(restriction['cooldown_seconds'])} 秒内续跑不会再次探测。"
                )
            else:
                print("   请先在浏览器中完成验证或稍后再试，不要重复运行登录探测。")
            sys.exit(1)
        if login_result.status is LoginProbeStatus.RESPONSE_ERROR:
            print(f"❌ {describe_login_probe_result(login_result)}，已停止抓取。")
            sys.exit(1)
        if login_result.status is LoginProbeStatus.EMPTY:
            print(f"⚠️  {describe_login_probe_result(login_result)}；继续执行实际职位搜索。\n")
        else:
            print("✅ 已登录\n")

        if position_mode:
            try:
                list_data = scrape_position_categories(
                    args.keyword, args.city, args.pages, filters, args.output,
                    cdp_port=args.cdp_port, fmt=args.format,
                    per_position_limit=args.per_position_limit,
                    checkpoint_path=args.checkpoint,
                    coverage_output=args.coverage_output,
                    position_codes=None if args.all_positions else args.position,
                    numeric_price_only=args.numeric_price_only,
                    max_positions=args.max_positions,
                    category_interval_seconds=args.category_interval_seconds,
                    category_jitter_seconds=args.category_jitter_seconds,
                    block_cooldown_seconds=args.block_cooldown_seconds,
                    page_delay_min_seconds=args.page_delay_min_seconds,
                    page_delay_max_seconds=args.page_delay_max_seconds,
                    batch_size=args.batch_size,
                    batch_cooldown_seconds=args.batch_cooldown_seconds,
                    max_consecutive_errors=args.max_consecutive_errors,
                )
            except (PositionCatalogError, PositionFetchError, ValueError) as e:
                print(f"❌ 职业采样失败: {e}")
                sys.exit(1)
        else:
            list_data = scrape_list(
                args.keyword, args.city, args.pages, filters, args.output,
                cdp_port=args.cdp_port, fmt=args.format,
                allow_dom_fallback=args.allow_dom_fallback,
                numeric_price_only=args.numeric_price_only,
            )

    # 合并外部文件
    merged_details = None
    if args.merge:
        merged_jobs = merge_jobs(args.merge, list_data.get("jobs", []))
        list_data["jobs"] = merged_jobs
        list_data["total"] = len(merged_jobs)
        list_data, _ = apply_numeric_salary_filter(
            list_data,
            numeric_price_only=args.numeric_price_only,
        )
        # 重新保存合并结果
        if args.output:
            extra_filtered = flush_jobs(args.output, {
                "keyword": list_data.get("keyword", ""),
                "city": list_data.get("city", ""),
                "filters": list_data.get("filters", {}),
                "filter_desc": list_data.get("filter_desc", []),
                "numeric_price_only": args.numeric_price_only,
                "filtered_out_count": list_data.get("filtered_out_count", 0),
                "scraped_at": datetime.now().isoformat(),
                "merged_from": args.merge,
            }, list_data.get("jobs", []),
                numeric_price_only=args.numeric_price_only,
                count_filtered_during_flush=False)
            if extra_filtered:
                list_data["filtered_out_count"] = (
                    list_data.get("filtered_out_count", 0) + extra_filtered
                )
            print(f"合并结果已保存: {args.output}")
            if args.format == "csv":
                csv_path = args.output.rsplit(".", 1)[0] + ".csv"
                write_csv(csv_path, list_data.get("jobs", []))
        # 同时加载旧详情，供后续详情抓取/分析合并（按 job_id 去重）
        merged_details = merge_details(args.merge, [])
        if args.numeric_price_only:
            merged_details, _ = filter_numeric_salary_jobs(
                merged_details,
                numeric_price_only=True,
            )

    # 抓详情
    details = None
    if args.detail and list_data.get("jobs"):
        details = scrape_details(
            list_data, args.max_details, args.detail_output,
            cdp_port=args.cdp_port, fmt=args.format,
            checkpoint_path=args.detail_checkpoint,
            load_timeout=args.detail_load_timeout,
            min_text_length=args.detail_min_chars,
            retries=args.detail_retries,
            gap_min=args.detail_gap_min,
            gap_max=args.detail_gap_max,
        )
        # 若处于合并流程，把旧详情并入本次抓取结果并重新落盘，保证 --merge 后详情不丢失
        if merged_details and args.detail_output:
            details = merge_details_from_lists(merged_details, details)
            if args.numeric_price_only:
                details, _ = filter_numeric_salary_jobs(details, numeric_price_only=True)
            os.makedirs(os.path.dirname(args.detail_output) or ".", exist_ok=True)
            with open(args.detail_output, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)
            print(f"合并详情已保存: {args.detail_output}")
            if args.format == "csv":
                detail_csv = args.detail_output.rsplit(".", 1)[0] + ".csv"
                write_detail_csv(detail_csv, details)

    # 分析
    if args.analysis:
        # 如果有详情文件也加载
        if not details:
            details = load_existing_details(args.input, args.detail_output)
        if args.numeric_price_only and details:
            details, _ = filter_numeric_salary_jobs(details, numeric_price_only=True)
        analyze(list_data, details, search_keyword=args.keyword)

    if args.numeric_price_only:
        print(
            f"明确数字薪资过滤汇总: 保留 {len(list_data.get('jobs', []))} 条，"
            f"过滤 {list_data.get('filtered_out_count', 0)} 条；抓取页数未扩展"
        )

    # 抓取正常结束后按需收尾（仅成功路径；异常/登录失败走 sys.exit，不会触发，保留登录态）
    if args.close_chrome:
        profile = prepare_cdp_profile(
            copy_login_state=False,
            reset=False,
            cdp_data_dir=args.cdp_data_dir,
        )
        stopped = stop_cdp_chrome(profile["path"])
        if stopped:
            print(f"\n🧹 已按 --close-chrome 关闭 BOSS 专用 Chrome 进程：{stopped} 个")
        else:
            print(f"\nℹ️  --close-chrome 未发现运行中的 BOSS 专用 Chrome 进程")


if __name__ == "__main__":
    main()
