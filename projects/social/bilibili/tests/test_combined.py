"""Tests combine the actual archived Bilibili data with deterministic API examples."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "tools"))
import collect_combined as c  # noqa: E402


def enc(n: int) -> bytes:
    out = bytearray()
    while n > 127:
        out.append((n & 127) | 128)
        n >>= 7
    out.append(n)
    return bytes(out)


def blob(key: int, value: bytes) -> bytes:
    return enc((key << 3) | 2) + enc(len(value)) + value


class BilibiliCombinedTests(unittest.TestCase):
    def test_archive_uses_real_rows_and_real_captions(self):
        items = c.load_archive(BASE / "data")
        self.assertEqual(len(items), 4904)
        present = [row for row in items.values() if row["字幕"]]
        self.assertEqual(len(present), 3667)
        self.assertTrue(all("视频标题" in row for row in present))

    def test_subtitle_body_list(self):
        text, n = c.parse_subtitle_json({
            "body": [{"from": 0.1, "to": 1.0, "content": "你好"},
                     {"from": 1.0, "to": 2.0, "content": "B站字幕"}],
        })
        self.assertEqual(text, "你好\nB站字幕")
        self.assertEqual(n, 2)
        with self.assertRaises(ValueError):
            c.parse_subtitle_json({"body": "invalid"})

    def test_new_binary_metadata_format(self):
        inner = blob(3, b"ai-zh") + blob(4, "中文AI".encode()) + blob(
            5, b"https://aisubtitle.hdslb.com/bfs/a/test.json"
        )
        tracks = c.decode_subtitle_tracks(blob(3, inner))
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0]["lan"], "ai-zh")
        self.assertTrue(tracks[0]["subtitle_url"].startswith("https://"))
        self.assertEqual(c.decode_subtitle_tracks(b"\x0a\x00"), [])
        with self.assertRaises(ValueError):
            c.decode_subtitle_tracks(b"\x1a\x07\x1a")

    def test_subtitle_redirect_is_host_restricted(self):
        self.assertTrue(c.safe_subtitle_url("//aisubtitle.hdslb.com/bfs/a"))
        for bad in ("file:///etc/passwd", "http://aisubtitle.hdslb.com/",
                    "https://not-hdslb.com/", "https://127.0.0.1/",
                    "https://aisubtitle.hdslb.com.evil.test/a"):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                c.safe_subtitle_url(bad)

    def test_wbi_sign_is_stable(self):
        args = c.wbi_query({"aid": 100, "cid": 200}, "a" * 32, "b" * 32, 123)
        self.assertEqual(args["wts"], "123")
        self.assertRegex(args["w_rid"], r"^[a-f0-9]{32}$")
        self.assertEqual(args, c.wbi_query({"cid": 200, "aid": 100},
                                            "a" * 32, "b" * 32, 123))

    def test_current_metadata_and_archive_subtitle_are_distinct(self):
        old = next(v for v in c.load_archive(BASE / "data").values()
                   if v.get("字幕"))
        bv, cid = old["BV号"], old["CID"]
        current = {
            "bvid": bv, "aid": 123456, "cid": int(cid),
            "pages": [{"cid": int(cid)}], "title": "当前的公开标题",
            "tname": "知识", "desc": "公开简介",
            "owner": {"mid": 123, "name": "公开UP"},
            "stat": {"view": 1234, "like": 15},
            "pubdate": 1700000000,
        }
        class FakeClient:
            def get_video(self, bvid):
                self_bv = bvid
                assert self_bv == bv
                return current

            def get_subtitle_tracks(self, data, request_cid):
                assert data is current and request_cid == cid
                return [], "login_required"

        item = c.merge_video(bv, cid, old, FakeClient())
        self.assertEqual(item["视频标题"], "当前的公开标题")
        self.assertEqual(item["播放量"], "1234")
        self.assertIn("历史CSV", item["字幕来源"])
        self.assertIn("login_required", item["字幕状态"])
        self.assertEqual(item["字幕"], old["字幕"])

    def test_can_write_and_read_combined_real_rows(self):
        data = c.load_archive(BASE / "data")
        real = [c.merge_video(bv, cid, row, None)
                for (bv, cid), row in data.items() if row.get("字幕")][:3]
        with tempfile.TemporaryDirectory(prefix="bili-joined-") as temp:
            result = Path(temp) / "combined.csv"
            c.export(real, result)
            with result.open(encoding="utf-8-sig", newline="") as f:
                got = list(csv.DictReader(f))
            self.assertEqual(len(got), 3)
            self.assertTrue(all(row["字幕"] for row in got))
            self.assertEqual(got[0]["字幕"], real[0]["字幕"])
            self.assertEqual(got[0]["字幕状态"], "历史可用，离线")


if __name__ == "__main__":
    unittest.main()
