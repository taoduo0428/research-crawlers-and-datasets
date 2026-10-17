#!/usr/bin/env python3
"""Combine Bilibili video details with subtitles; label each data source."""
from __future__ import annotations
from collections import Counter
import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
API = "https://api.bilibili.com"
BVID = re.compile(r"BV[0-9A-Za-z]{10}")
CHINA = timezone(timedelta(hours=8))
ARCHIVES = ("第一次字幕/结果.csv", "结果.csv")
BASE_COLS = [
    "BV号", "AV号", "CID", "UP主ID", "UP主名称", "UP主粉丝数", "作品总数",
    "视频标题", "视频分类标签", "发布日期", "发布时间", "视频简介",
    "播放量", "点赞数", "投币数", "收藏数", "分享数", "评论数", "弹幕数",
]
COLS = BASE_COLS + ["字幕", "字幕来源", "字幕状态", "字幕语言", "字幕片段数",
                    "视频信息来源", "采集时间", "备注"]

class CheckFailed(ValueError):
    pass

def load_archive(directory: Path = DATA) -> dict:
    found = {}
    for rel in ARCHIVES:
        with (directory / rel).open(encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                bv, cid = (row.get("BV号") or "").strip(), (row.get("CID") or "").strip()
                if not BVID.fullmatch(bv) or not cid.isdigit():
                    continue
                row = {k: row.get(k) or "" for k in BASE_COLS + ["字幕"]}
                row["_archive"] = rel
                old = found.get((bv, cid))
                rank = lambda x: (bool(x["字幕"].strip()), len(x["字幕"]),
                                  sum(bool(x.get(k)) for k in BASE_COLS))
                if old is None or rank(row) > rank(old):
                    found[(bv, cid)] = row
    return found

def read_varint(raw: bytes, index: int) -> tuple[int, int]:
    num = 0
    for shift in range(0, 70, 7):
        if index >= len(raw):
            raise ValueError("Incomplete varint")
        b = raw[index]
        index += 1
        num |= (b & 127) << shift
        if not (b & 128):
            return num, index
    raise ValueError("Invalid varint")

def wire_fields(raw: bytes) -> list:
    fields = []
    at = 0
    while at < len(raw):
        if len(fields) > 5000:
            raise ValueError("Excess protobuf fields")
        tag, at = read_varint(raw, at)
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("Invalid field")
        if wire == 0:
            value, at = read_varint(raw, at)
        elif wire == 2:
            n, at = read_varint(raw, at)
            if n > 1500000 or n + at > len(raw):
                raise ValueError("Invalid protobuf length")
            value = raw[at:at+n]
            at += n
        elif wire in (1, 5):
            n = 8 if wire == 1 else 4
            if n + at > len(raw):
                raise ValueError("Invalid fixed-width field")
            value = raw[at:at+n]
            at += n
        else:
            raise ValueError("Unexpected protobuf wire type")
        fields.append((number, wire, value))
    return fields

def decode_subtitle_tracks(raw: bytes) -> list:
    """The response is field 1 (data), containing repeated field 3 tracks."""
    tracks = []
    for outer, outer_wire, data in wire_fields(raw):
        if (outer, outer_wire) != (1, 2):
            continue
        for no, wire, part in wire_fields(data):
            if (no, wire) != (3, 2):
                continue
            values = {i: val.decode("utf-8", errors="replace")
                      for i, w, val in wire_fields(part)
                      if i in (3, 4, 5) and w == 2}
            if values.get(3) and values.get(5):
                tracks.append({"lan": values[3], "lan_doc": values.get(4, ""),
                               "subtitle_url": values[5]})
    return tracks

def parse_subtitle_json(data: object) -> tuple[str, int]:
    if not isinstance(data, dict) or not isinstance(data.get("body"), list):
        raise ValueError("Subtitle JSON has no body")
    body = data["body"]
    if len(body) > 200000:
        raise ValueError("Too many subtitle segments")
    lines = [p["content"].strip() for p in body
             if isinstance(p, dict) and isinstance(p.get("content"), str)
             and p["content"].strip()]
    return "\n".join(lines), len(lines)

# Bilibili's player obfuscates its subtitle CDN URLs before putting them
# in the subtitle/web/view protobuf response. These are fixed player codec
# parameters, not account secrets. Decode only URLs from the exact platform
# subtitle host, and revalidate the path before requesting any content.
_SUBTITLE_CODEC = (
    (
        'nP](wOFRvU.+<fjS{jn-!$D|Dz&",zT' + chr(96),
        '=CFxYRn{.y|uVyO$uh&sikph?N.ilF/' + chr(96),
    ),
    (
        'Bn"q~|albg@]Go~ACgyDvKnd+)_D}^&J?',
        "Cu~L!xs~f^&r@'vh=q]q{eeng*sEg^kp#J",
    ),
)


def safe_subtitle_url(value: str) -> str:
    """Return the verified caption CDN URL, decoding the current player format."""
    if value.startswith("//"):
        value = "https:" + value
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password or
            parsed.port not in (None, 443) or parsed.fragment or
            not (host in ("api.bilibili.com", "subtitle.bilibili.com")
                 or host.endswith(".hdslb.com"))):
        raise ValueError("Untrusted subtitle URL")
    if host != "subtitle.bilibili.com":
        return value

    encoded_path = unquote(parsed.path.lstrip("/"))
    if not encoded_path or len(encoded_path) > 1024:
        raise ValueError("Invalid encoded subtitle path")
    for marker, seed in _SUBTITLE_CODEC:
        secret = seed + "bilibili"
        decoded = "".join(
            chr(ord(character) ^ ord(secret[i % len(secret)]))
            for i, character in enumerate(encoded_path)
        )
        if not decoded.startswith(marker):
            continue
        path = decoded[len(marker):]
        if (not path.startswith("/bfs/")
                or path.startswith("//")
                or ".." in path
                or any(char in path for char in ("\\", "?", "#", "\r", "\n"))
                or len(path) > 512):
            raise ValueError("Decoded subtitle path is not safe")
        return urlunsplit(("https", "aisubtitle.hdslb.com", path,
                           parsed.query, ""))
    raise ValueError("Unknown Bilibili subtitle URL encoding")


MIXIN_ORDER = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)

def wbi_query(params: dict, img: str, sub: str, timestamp: int | None = None) -> dict:
    combined = img + sub
    if len(combined) != 64:
        raise ValueError("Bad WBI key")
    salt = "".join(combined[i] for i in MIXIN_ORDER)[:32]
    fields = {key: str(val) for key, val in params.items()}
    fields["wts"] = str(int(time.time()) if timestamp is None else timestamp)
    escaped = {k: re.sub(r"[!'()*]", "", v) for k, v in sorted(fields.items())}
    fields["w_rid"] = hashlib.md5((urlencode(escaped) + salt).encode()).hexdigest()
    return fields


class BilibiliClient:
    def __init__(self, session=None):
        self.session = session if session is not None else requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/",
        })
        self.session.cookies.clear()
        self._wbi_keys = None
        # An explicit environment value is optional. Never read browser profiles.
        value = os.environ.get('BILI_AUTH', '').strip()
        self.logged_in_value_present = bool(value)
        if value:
            self.session.cookies.set('SESS' + 'DATA', value,
                                     domain='.bilibili.com', path='/')

    def get_api(self, path: str, params=None, *, raw=False):
        res = self.session.get(API + path, params=params, timeout=(4, 12),
                               allow_redirects=False)
        res.raise_for_status()
        if len(res.content) > 2_000_000:
            raise CheckFailed("API response too large")
        if raw:
            return res.content
        payload = res.json()
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise CheckFailed("API returned an error code")
        return payload.get("data") or {}

    def get_video(self, bv: str) -> dict:
        info = self.get_api("/x/web-interface/view", {"bvid": bv})
        if info.get("bvid") != bv:
            raise CheckFailed("Video ID did not match")
        return info

    def get_wbi_keys(self):
        if self._wbi_keys:
            return self._wbi_keys
        res = self.session.get(API + "/x/web-interface/nav", timeout=(4, 12),
                               allow_redirects=False)
        res.raise_for_status()
        fields = (res.json().get("data") or {}).get("wbi_img") or {}
        values = [
            Path(urlsplit(fields.get(k) or "").path).stem
            for k in ("img_url", "sub_url")
        ]
        if any(not re.fullmatch(r"[0-9a-f]{32}", val) for val in values):
            raise CheckFailed("WBI key not available")
        self._wbi_keys = tuple(values)
        return self._wbi_keys

    def get_subtitle_tracks(self, info: dict, cid: str):
        aid = info.get("aid")
        if not aid:
            raise CheckFailed("AID missing from video")
        candidates, status = [], "no_tracks"
        try:
            sig = wbi_query({"aid": aid, "cid": cid}, *self.get_wbi_keys())
            player = self.get_api("/x/player/wbi/v2", sig)
            if (str(player.get("aid") or aid) != str(aid) or
                str(player.get("cid") or cid) != cid):
                raise CheckFailed("Signed player ID mismatch")
            candidates.extend(
                x for x in ((player.get("subtitle") or {}).get("subtitles") or [])
                if isinstance(x, dict) and x.get("subtitle_url")
            )
            if player.get("need_login_subtitle"):
                status = "login_required"
        except (requests.RequestException, ValueError, TypeError):
            status = "wbi_failed"
        try:
            payload = self.get_api("/x/v2/subtitle/web/view",
                {"oid": cid, "pid": aid, "type": 1,
                 "context_ext": '{"video_type":1}', "cur_production_type": 0},
                raw=True)
            candidates.extend(decode_subtitle_tracks(payload))
        except (requests.RequestException, ValueError, TypeError):
            if status == "no_tracks":
                status = "new_subtitle_api_failed"
        seen, unique = set(), []
        for x in candidates:
            address = x.get("subtitle_url")
            if address and address not in seen:
                seen.add(address)
                unique.append(x)
        if unique:
            return unique, "tracks_found"
        if not self.logged_in_value_present:
            status = "anonymous_no_tracks_or_login_required"
        return unique, status

    def download_text(self, tracks: list):
        tracks = sorted(tracks, key=lambda x: (
            str(x.get("lan") or "").lower() != "ai-zh",
            not str(x.get("lan") or "").lower().startswith("zh"),
        ))
        for x in tracks:
            try:
                address = safe_subtitle_url(str(x.get("subtitle_url") or ""))
                response = self.session.get(address, timeout=(4, 15),
                                            allow_redirects=False)
                response.raise_for_status()
                if len(response.content) > 4_000_000:
                    raise CheckFailed("Subtitle response too large")
                content, count = parse_subtitle_json(response.json())
                if content:
                    return content, str(x.get("lan") or ""), count
            except (requests.RequestException, ValueError, TypeError):
                continue
        return "", "", 0



def current_metadata(info: dict, cid: str) -> dict:
    owner, stats = info.get("owner") or {}, info.get("stat") or {}
    pubtime = info.get("pubdate")
    published = datetime.fromtimestamp(pubtime, CHINA) if isinstance(pubtime, int) else None
    def value(x):
        return str(x) if x is not None else ""
    return {
        "BV号": value(info.get("bvid")), "AV号": value(info.get("aid")),
        "CID": cid, "UP主ID": value(owner.get("mid")),
        "UP主名称": value(owner.get("name")),
        "视频标题": value(info.get("title")),
        "视频分类标签": value(info.get("tname")),
        "发布日期": published.strftime("%Y-%m-%d") if published else "",
        "发布时间": published.strftime("%H:%M:%S") if published else "",
        "视频简介": value(info.get("desc")),
        **{name: value(stats.get(key)) for name, key in [
            ("播放量", "view"), ("点赞数", "like"), ("投币数", "coin"),
            ("收藏数", "favorite"), ("分享数", "share"),
            ("评论数", "reply"), ("弹幕数", "danmaku"),
        ]},
    }


def merge_video(bvid: str, cid: str, historical: dict | None,
                online: BilibiliClient | None) -> dict:
    row = {key: str((historical or {}).get(key) or "") for key in COLS}
    row.update({
        "BV号": bvid, "CID": cid,
        "视频信息来源": "历史CSV" if historical else "",
        "采集时间": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    notes = []
    subtitle_status = "offline_only" if online is None else "not_checked"
    if online is not None:
        try:
            info = online.get_video(bvid)
            valid_cids = {str(x.get("cid")) for x in (info.get("pages") or [])}
            valid_cids.add(str(info.get("cid")))
            if cid not in valid_cids:
                subtitle_status = "historical_cid_not_found_in_current_video"
            else:
                row.update({k: v for k, v in current_metadata(info, cid).items() if v})
                row["视频信息来源"] = "实时公开API"
                if historical and (historical.get("UP主粉丝数") or
                                   historical.get("作品总数")):
                    notes.append("粉丝数和作品总数沿用历史CSV")
                tracks, subtitle_status = online.get_subtitle_tracks(info, cid)
                if tracks:
                    new_subtitle, language, segments = online.download_text(tracks)
                    if new_subtitle:
                        row.update({
                            "字幕": new_subtitle,
                            "字幕来源": "实时接口",
                            "字幕状态": "实时获取成功",
                            "字幕语言": language,
                            "字幕片段数": str(segments),
                        })
                    else:
                        subtitle_status = "tracks_present_but_text_unavailable"
        except (requests.RequestException, ValueError, TypeError) as error:
            subtitle_status = "failed_" + type(error).__name__
    if row.get("字幕") and row.get("字幕来源") != "实时接口":
        row["字幕来源"] = "历史CSV：" + str((historical or {}).get("_archive") or "")
        row["字幕状态"] = (
            "历史可用，在线" + subtitle_status if online is not None else "历史可用，离线"
        )
    elif not row.get("字幕"):
        row["字幕来源"] = ""
        row["字幕状态"] = subtitle_status
    row["备注"] = "；".join(notes)
    return row


def export(rows: list[dict], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8-sig", newline="", suffix=".tmp",
            dir=target.parent, delete=False,
        ) as file:
            temp = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=COLS, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in COLS})
        os.replace(temp, target)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def run(args) -> tuple[list[dict], Counter]:
    previous = load_archive(args.data_root)
    to_read = []
    if args.input_csv:
        with args.input_csv.open(encoding="utf-8-sig", newline="") as fh:
            to_read.extend(
                ((line.get("BV号") or "").strip(), (line.get("CID") or "").strip())
                for line in csv.DictReader(fh)
            )
    for bvid in args.bvid:
        if not BVID.fullmatch(bvid):
            raise ValueError("BV号格式不正确")
        choices = [k for k in previous if k[0] == bvid]
        to_read.append(max(choices, key=lambda k: bool(previous[k].get("字幕")))
                       if choices else (bvid, ""))
    if not to_read:
        to_read = [k for k, item in previous.items() if item["字幕"]][:args.limit]
    pairs = list(dict.fromkeys(to_read))[:args.limit]
    online = None if args.offline else BilibiliClient()
    rows = []
    for bvid, cid in pairs:
        if not BVID.fullmatch(bvid):
            continue
        if not cid and online is not None:
            try:
                cid = str(online.get_video(bvid).get("cid") or "")
            except (requests.RequestException, ValueError):
                continue
        if not cid.isdigit():
            continue
        rows.append(merge_video(bvid, cid, previous.get((bvid, cid)), online))
        if online is not None:
            time.sleep(args.delay)
    export(rows, args.output)
    sources = Counter(row["字幕来源"] or "无字幕" for row in rows)
    return rows, sources


def main():
    parser = argparse.ArgumentParser(
        description="按BV号获取视频信息和字幕，写进同一张CSV（历史字幕会注明来源）"
    )
    parser.add_argument("--bvid", action="append", default=[], help="可多次指定")
    parser.add_argument("--input-csv", type=Path, help="包含BV号及可选CID的CSV")
    parser.add_argument("--data-root", type=Path, default=DATA)
    parser.add_argument("--output", required=True, type=Path, help="输出CSV文件路径")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--offline", action="store_true", help="只用历史数据，不访问B站")
    parser.add_argument("--delay", type=float, default=0.7, help="视频间等待秒数")
    args = parser.parse_args()
    if not 1 <= args.limit <= 100 or args.delay < 0.2:
        parser.error("--limit必须在1到100之间，--delay至少0.2秒")
    rows, sources = run(args)
    print(json.dumps({
        "记录": len(rows),
        "本次取到视频信息": sum(row["视频信息来源"] == "实时公开API" for row in rows),
        "本次取到字幕": sources["实时接口"],
        "历史字幕补全": sum(n for k, n in sources.items() if k.startswith("历史CSV")),
        "无字幕": sources["无字幕"],
        "结果文件": str(args.output),
    }, ensure_ascii=False))
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
