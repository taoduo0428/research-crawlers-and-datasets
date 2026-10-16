"""Offline regression tests for Keep's minimal public-facing research projection."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import keep_release  # noqa: E402
import normalize_keep  # noqa: E402


class KeepPublicationTests(unittest.TestCase):
    def test_profile_allowlist_omits_sensitive_unknown_values(self):
        source = {
            "data": {"headInfo": {"userBasicInfo": {
                "id": "private-profile",
                "gender": "F",
                "keepValue": 20,
                "nickname": "PRIVATE_NAME",
                "avatar": "https://example.invalid/avatar",
                "memberStatus": "active",
                "membershipSchema": "https://example.invalid/profile/private",
            }}}
        }
        cleaned = keep_release.project_profile(source, lambda tagged: "ANONID123")
        dump = json.dumps(cleaned)
        self.assertNotIn("PRIVATE_NAME", dump)
        self.assertNotIn("example.invalid", dump)
        self.assertNotIn("private-profile", dump)
        self.assertEqual(
            cleaned["data"]["headInfo"]["userBasicInfo"]["id"], "ANONID123"
        )

    def test_activity_allowlist_omits_unknown_user_and_media_fields(self):
        source = {
            "data": [{"info": {"entries": [{
                "id": "original-post",
                "author": {
                    "_id": "original-user",
                    "nickname": "PRIVATE_NAME",
                },
                "type": "activity",
                "state": "published",
                "internalShareCount": 3,
                "images": ["https://example.invalid/private.jpg"],
                "desc": "PERSONAL_TEXT",
                "sampleComments": [{
                    "id": "original-comment",
                    "text": "PRIVATE_COMMENT",
                }],
            }]}}]
        }
        cleaned = keep_release.project_activities(source, lambda _: "ANONID123")
        dump = json.dumps(cleaned)
        for leak in ("original-post", "original-user", "PRIVATE_NAME",
                     "example.invalid", "PERSONAL_TEXT", "PRIVATE_COMMENT"):
            self.assertNotIn(leak, dump)
        entry = cleaned["data"][0]["info"]["entries"][0]
        self.assertEqual(entry["id"], "ANONID123")
        self.assertEqual(entry["author"]["_id"], "ANONID123")

    def test_published_stage1_files_have_no_unexpected_keys_or_links(self):
        root = ROOT / "projects/social/keep/data/phase1_anonymized"
        allowed = {
            "data", "headInfo", "userBasicInfo", "id", "gender", "keepValue",
            "kgLevel", "memberStatus", "info", "entries", "author", "_id",
            "type", "state", "internalShareCount", "sampleComments",
        }
        files = sorted(root.rglob("*.json"))
        self.assertEqual(len(files), 206)
        for filename in files:
            obj = json.loads(filename.read_text(encoding="utf-8"))
            def check(value):
                if isinstance(value, dict):
                    self.assertTrue(set(value).issubset(allowed),
                                    msg=filename.name)
                    for child in value.values():
                        check(child)
                elif isinstance(value, list):
                    for child in value:
                        check(child)
                elif isinstance(value, str):
                    self.assertNotRegex(value, r"(?i)https?://|www[.]")
                    self.assertNotRegex(value, r"(?<![0-9])1[3-9][0-9]{9}(?![0-9])")
                    self.assertLessEqual(len(value), 32)
            check(obj)

    def test_second_stage_rebuild_is_byte_identical(self):
        src = ROOT / "projects/social/keep/data/phase1_anonymized"
        expected = ROOT / "projects/social/keep/data/phase2"
        with tempfile.TemporaryDirectory(prefix="keep-test-") as temp:
            target = pathlib.Path(temp)
            stats = normalize_keep.collect(src, target)
            self.assertEqual(
                stats, {"profiles": 100, "posts": 1050, "comments": 1692}
            )
            for name in ("profiles.csv", "posts.csv", "comments.csv"):
                self.assertEqual(
                    (target / name).read_bytes(),
                    (expected / name).read_bytes(),
                    msg=name,
                )


if __name__ == "__main__":
    unittest.main()
