import csv
import html
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from facebook_target_readiness import evaluate_target_ui, _load_capture


TARGET_ID = "1476320540987565"
TARGET_URL = f"https://www.facebook.com/page/posts/{TARGET_ID}/"
TARGET_AUTHOR = "张三"
TARGET_MESSAGE = (
    "这是一段足够长而且具有辨识度的正文，应该能够确认当前页面就是目标帖子，"
    "同时保留中文与数字 2026。"
)
TARGET_AUTHOR_ID = "100058288982749"


def _attr(value):
    return html.escape(str(value), quote=True)


def ui_xml(
    *,
    author=TARGET_AUTHOR,
    message=TARGET_MESSAGE,
    package="com.facebook.katana",
    author_visible=True,
    message_visible=True,
    extra="",
    detail=False,
    progress=False,
):
    author_node = ""
    if author is not None:
        author_node = (
            f'<node class="android.widget.Button" package="{_attr(package)}" '
            f'enabled="true" visible-to-user="{str(author_visible).lower()}" '
            f'text="{_attr(author)}" bounds="[0,100][500,150]" />'
        )
    message_node = ""
    if message is not None:
        message_node = (
            f'<node class="android.view.View" package="{_attr(package)}" '
            f'enabled="true" visible-to-user="{str(message_visible).lower()}" '
            f'content-desc="{_attr(message)}" bounds="[0,160][1000,500]" />'
        )
    detail_nodes = ""
    if detail:
        detail_nodes = (
            '<node class="android.widget.AutoCompleteTextView" package="com.facebook.katana" '
            'enabled="true" visible-to-user="true" clickable="true" text="写评论..." '
            'bounds="[0,1700][1000,1850]" />'
            '<node class="android.widget.Button" package="com.facebook.katana" '
            'enabled="true" visible-to-user="true" clickable="true" content-desc="关闭" '
            'bounds="[0,0][100,100]" />'
        )
    action_node = (
        '<node class="android.widget.Button" package="com.facebook.katana" '
        'enabled="true" visible-to-user="true" clickable="true" text="评论" '
        'bounds="[950,600][1080,700]" />'
    )
    if progress:
        detail_nodes += (
            '<node class="android.widget.ProgressBar" package="com.facebook.katana" '
            'enabled="true" visible-to-user="true" bounds="[400,400][500,500]" />'
        )
    return (
        '<hierarchy rotation="0">'
        '<node class="android.widget.FrameLayout" package="com.facebook.katana" '
        'enabled="true" visible-to-user="true" bounds="[0,0][1080,1920]">'
        f'<node class="android.view.ViewGroup" package="{_attr(package)}" '
        'enabled="true" visible-to-user="true" bounds="[0,0][1080,1800]">'
        f"{author_node}{message_node}{action_node}{extra}{detail_nodes}"
        "</node></node></hierarchy>"
    )


def verification(
    *,
    expected_id=TARGET_ID,
    expected_url=TARGET_URL,
    matched_id=TARGET_ID,
    status="matched",
    matched_by="external-post-link-author",
    author_id=TARGET_AUTHOR_ID,
):
    result = {
        "status": status,
        "expected_post_id": expected_id,
        "expected_url": expected_url,
        "matched_by": matched_by,
        "matched_line": 17,
    }
    if matched_id is not None:
        result["matched_post_id"] = matched_id
    if author_id is not None:
        result["resolved_author_id"] = author_id
    return result


def post_row(
    *,
    post_id=TARGET_ID,
    author=TARGET_AUTHOR,
    message=TARGET_MESSAGE,
    author_id=TARGET_AUTHOR_ID,
    url=TARGET_URL,
):
    return {
        "post_id": post_id,
        "author_id": author_id,
        "author_name": author,
        "message": message,
        "url": url,
    }


def capture_story_record(post_id=TARGET_ID, author=TARGET_AUTHOR, message=TARGET_MESSAGE):
    response = {
        "data": {
            "story": {
                "__typename": "Story",
                "post_id": post_id,
                "actors": [{"id": TARGET_AUTHOR_ID, "name": author}],
                "message": {"text": message},
                "feedback": {
                    "legacy_api_post_id": post_id,
                    "reactors": {"count": 1},
                },
            }
        }
    }
    return {
        "friendly_name": "FBVideoHomeExternalPostLinkQuery",
        "status_code": 200,
        "path": "/graphql",
        "response_body": json.dumps(response, ensure_ascii=False),
    }


class TargetReadinessTests(unittest.TestCase):
    def test_matching_author_and_long_body_is_early_ready(self):
        result = evaluate_target_ui(
            ui_xml(),
            verification(),
            [post_row()],
            expected_post_id=TARGET_ID,
            expected_url=TARGET_URL,
        )
        self.assertTrue(result["ready"])
        self.assertEqual(result["matched_post_id"], TARGET_ID)

    def test_nfkc_xml_escape_and_truncated_body_prefix_are_supported(self):
        message = "ＡＢＣ 这是一段很长的正文，里面有 & 符号以及数字 1234567890，便于确认帖子。"
        row = post_row(author="ＡＢＣ", message=message)
        visible_prefix = "ＡＢＣ 这是一段很长的正文，里面有 & 符号以及数字 1234567890，便于确认帖子。"
        result = evaluate_target_ui(
            ui_xml(author="ＡＢＣ", message=visible_prefix + "…"),
            verification(),
            [row],
            expected_post_id=TARGET_ID,
            expected_url=TARGET_URL,
        )
        self.assertTrue(result["ready"])

    def test_wrong_old_page_and_reel_prefetch_do_not_pass(self):
        reel_url = "https://www.facebook.com/reel/1054985397317048/"
        result = evaluate_target_ui(
            ui_xml(author="旧帖子作者", message=TARGET_MESSAGE),
            verification(
                expected_id=None,
                expected_url=reel_url,
                matched_id=None,
                matched_by="graphql-response-url-token",
                author_id=None,
            ),
            [post_row()],
            expected_url=reel_url,
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "target-post-not-mapped")

    def test_reel_video_id_is_never_used_as_story_post_id(self):
        reel_id = "1054985397317048"
        reel_url = f"https://www.facebook.com/reel/{reel_id}/"
        result = evaluate_target_ui(
            ui_xml(),
            verification(
                expected_id=None,
                expected_url=reel_url,
                matched_id=reel_id,
                matched_by="graphql-response-url-token",
                author_id=None,
            ),
            [post_row(post_id=reel_id)],
            expected_url=reel_url,
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "video-id-is-not-post-id")

    def test_short_body_and_author_only_are_not_enough(self):
        short_row = post_row(message="短正文")
        short = evaluate_target_ui(
            ui_xml(message="短正文"),
            verification(),
            [short_row],
            expected_post_id=TARGET_ID,
            expected_url=TARGET_URL,
        )
        self.assertFalse(short["ready"])

        author_only = evaluate_target_ui(
            ui_xml(message=None),
            verification(),
            [post_row()],
            expected_post_id=TARGET_ID,
            expected_url=TARGET_URL,
        )
        self.assertFalse(author_only["ready"])

    def test_wrong_id_and_wrong_author_with_same_body_are_rejected(self):
        wrong_id = evaluate_target_ui(
            ui_xml(),
            verification(),
            [post_row(post_id="9999999999999999")],
            expected_post_id=TARGET_ID,
            expected_url=TARGET_URL,
        )
        self.assertFalse(wrong_id["ready"])

        wrong_author = evaluate_target_ui(
            ui_xml(author=TARGET_AUTHOR),
            verification(),
            [post_row(author="另一个作者")],
            expected_post_id=TARGET_ID,
            expected_url=TARGET_URL,
        )
        self.assertFalse(wrong_author["ready"])

    def test_hidden_nodes_do_not_prove_the_target(self):
        result = evaluate_target_ui(
            ui_xml(author_visible=False, message_visible=False),
            verification(),
            [post_row()],
            expected_post_id=TARGET_ID,
            expected_url=TARGET_URL,
        )
        self.assertFalse(result["ready"])

    def test_multiple_target_rows_are_ambiguous(self):
        other = post_row(
            post_id="1555555555555555",
            author="另一个作者",
            message=TARGET_MESSAGE,
            author_id="100000000000000",
            url=TARGET_URL,
        )
        result = evaluate_target_ui(
            ui_xml(),
            verification(expected_id=None, matched_id=None, author_id=None),
            [post_row(post_id=TARGET_ID), other],
            expected_url=TARGET_URL,
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "target-post-ambiguous")

    def test_blocked_loading_offline_search_and_nonfacebook_pages_fail(self):
        cases = [
            ui_xml(extra='<node class="android.view.View" package="com.facebook.katana" enabled="true" text="This content is not available" />'),
            ui_xml(progress=True),
            ui_xml(extra='<node class="android.view.View" package="com.facebook.katana" enabled="true" text="网络不可用" />'),
            ui_xml(extra='<node class="android.view.View" package="com.facebook.katana" enabled="true" text="搜索结果" />'),
            ui_xml(package="com.instagram.android"),
        ]
        for raw in cases:
            with self.subTest(raw=raw[:80]):
                result = evaluate_target_ui(
                    raw,
                    verification(),
                    [post_row()],
                    expected_post_id=TARGET_ID,
                    expected_url=TARGET_URL,
                )
                self.assertFalse(result["ready"])

    def test_multiple_posts_in_current_ui_are_ambiguous(self):
        other_message = (
            "另一条同时展示的正文也足够长，不能把两个完整卡片混在一起判断为唯一目标。"
            "编号 987654321。"
        )
        raw = ui_xml(
            extra=(
                f'<node class="android.widget.Button" package="com.facebook.katana" enabled="true" '
                f'text="另一个作者" />'
                f'<node class="android.view.View" package="com.facebook.katana" enabled="true" '
                f'content-desc="{_attr(other_message)}" />'
            )
        )
        result = evaluate_target_ui(
            raw,
            verification(matched_id=None, author_id=None),
            [
                post_row(),
                post_row(
                    post_id="1555555555555555",
                    author="另一个作者",
                    message=other_message,
                    author_id="100000000000000",
                    url="https://www.facebook.com/page/posts/1555555555555555/",
                ),
            ],
            expected_url=TARGET_URL,
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "target-post-ambiguous")

    def test_require_detail_needs_semantic_composer_and_close(self):
        base = evaluate_target_ui(
            ui_xml(),
            verification(),
            [post_row()],
            expected_post_id=TARGET_ID,
            expected_url=TARGET_URL,
            require_detail=True,
        )
        self.assertFalse(base["ready"])
        detail = evaluate_target_ui(
            ui_xml(detail=True),
            verification(),
            [post_row()],
            expected_post_id=TARGET_ID,
            expected_url=TARGET_URL,
            require_detail=True,
        )
        self.assertTrue(detail["ready"])

    def test_pfbid_strict_resolved_id_can_map_csv_without_url(self):
        pfbid = "pfbidABC123456789"
        url = f"https://www.facebook.com/page/posts/{pfbid}/"
        result = evaluate_target_ui(
            ui_xml(),
            verification(
                expected_id=None,
                expected_url=url,
                matched_id=TARGET_ID,
                matched_by="pfbid-external-post-link-navigation",
            ),
            [post_row(url="")],
            expected_url=url,
        )
        self.assertTrue(result["ready"])

    def test_numeric_url_can_map_exact_csv_post_id_when_url_is_empty(self):
        result = evaluate_target_ui(
            ui_xml(),
            verification(
                expected_id=None,
                expected_url=TARGET_URL,
                matched_id=None,
                matched_by="graphql-response-url-token",
                author_id=None,
            ),
            [post_row(url="")],
            expected_url=TARGET_URL,
        )
        self.assertTrue(result["ready"])

    def test_verification_status_and_expected_arguments_are_strict(self):
        not_matched = evaluate_target_ui(
            ui_xml(),
            verification(status="not-matched"),
            [post_row()],
            expected_post_id=TARGET_ID,
            expected_url=TARGET_URL,
        )
        self.assertFalse(not_matched["ready"])

        mismatch = evaluate_target_ui(
            ui_xml(),
            verification(expected_url="https://www.facebook.com/page/posts/1/"),
            [post_row()],
            expected_post_id=TARGET_ID,
            expected_url=TARGET_URL,
        )
        self.assertFalse(mismatch["ready"])


class TargetReadinessCliTests(unittest.TestCase):
    def test_binary_media_friendly_names_do_not_poison_graphql_readiness(self):
        records = [
            {"path": "/photo.jpg", "friendly_name": "image", "response_body": "\xff\xd8JPEG"},
            capture_story_record(),
            {"path": "/video.mp4", "friendly_name": "vps_playback_DASH_VIDEO", "response_body": "\x00ftypmp41"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "capture.jsonl"
            capture.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
            verified, posts, error = _load_capture(capture, 1, TARGET_ID, TARGET_URL)
            self.assertIsNone(error)
            self.assertEqual(verified["matched_line"], 2)
            self.assertTrue(evaluate_target_ui(ui_xml(), verified, posts)["ready"])

    def test_old_media_prefix_does_not_exclude_small_fresh_target_window(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "capture.jsonl"
            media = {"path": "/photo.jpg", "friendly_name": "image", "response_body": "x" * (5 * 1024 * 1024)}
            capture.write_text(json.dumps(media) + "\n" + json.dumps(capture_story_record()) + "\n", encoding="utf-8")
            verified, posts, error = _load_capture(capture, 2, TARGET_ID, TARGET_URL)
            self.assertIsNone(error)
            self.assertEqual(verified["matched_line"], 2)
            self.assertTrue(evaluate_target_ui(ui_xml(), verified, posts)["ready"])

    def test_visibility_and_enabled_are_not_conflated(self):
        raw = ui_xml(detail=True)
        # Mirrors real Facebook: a disabled layout contains enabled children.
        disabled_layout = raw.replace(
            '<node class="android.view.ViewGroup" package="com.facebook.katana" enabled="true"',
            '<node class="android.view.ViewGroup" package="com.facebook.katana" enabled="false"',
        )
        self.assertTrue(evaluate_target_ui(disabled_layout, verification(), [post_row()])["ready"])
        hidden_layout = raw.replace(
            '<node class="android.view.ViewGroup" package="com.facebook.katana" enabled="true" visible-to-user="true"',
            '<node class="android.view.ViewGroup" package="com.facebook.katana" enabled="true" visible-to-user="false"',
        )
        self.assertFalse(evaluate_target_ui(hidden_layout, verification(), [post_row()])["ready"])

    def test_hidden_or_disabled_actions_cannot_make_static_content_ready(self):
        for replacement in ('enabled="false"', 'visible-to-user="false"', 'clickable="false"'):
            with self.subTest(replacement=replacement):
                raw = ui_xml()
                # The fixture's only post action is its comment button.
                import xml.etree.ElementTree as ET
                root = ET.fromstring(raw)
                for node in root.iter("node"):
                    if node.get("text") == "评论":
                        key, value = replacement.replace('"', '').split('=')
                        node.set(key, value)
                self.assertFalse(evaluate_target_ui(ET.tostring(root), verification(), [post_row()])["ready"])

    def test_permission_overlay_anr_and_zero_area_text_are_rejected(self):
        for extra in (
            '<node package="com.android.permissioncontroller" enabled="true" clickable="true" text="Allow" />',
            '<node package="com.facebook.katana" enabled="true" resource-id="android:id/aerr_close" />',
        ):
            with self.subTest(extra=extra):
                self.assertFalse(evaluate_target_ui(ui_xml(extra=extra), verification(), [post_row()])["ready"])
        raw = ui_xml().replace('[0,160][1000,500]', '[0,0][0,0]')
        self.assertFalse(evaluate_target_ui(raw, verification(), [post_row()])["ready"])

    def test_report_mode_outputs_diagnostics_and_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            ui = temp / "ui.xml"
            report = temp / "report.json"
            posts = temp / "posts.csv"
            ui.write_text(ui_xml(), encoding="utf-8")
            report.write_text(
                json.dumps({"min_line": 7, "target_verification": verification()}, ensure_ascii=False),
                encoding="utf-8",
            )
            with posts.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=post_row().keys())
                writer.writeheader()
                writer.writerow(post_row())
            process = subprocess.run(
                [
                    sys.executable,
                    "facebook_target_readiness.py",
                    "--ui",
                    str(ui),
                    "--report",
                    str(report),
                    "--posts",
                    str(posts),
                    "--expected-post-id",
                    TARGET_ID,
                    "--expected-url",
                    TARGET_URL,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertTrue(result["ready"])
            self.assertEqual(result["matched_line"], 17)
            self.assertEqual(result["verification_min_line"], 7)

    def test_capture_mode_fails_closed_on_bad_jsonl_and_reports_min_line(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            ui = temp / "ui.xml"
            capture = temp / "capture.jsonl"
            ui.write_text(ui_xml(), encoding="utf-8")
            capture.write_text('{"friendly_name":"FeedbackReactorsGraphService"}\nnot-json\n', encoding="utf-8")
            process = subprocess.run(
                [
                    sys.executable,
                    "facebook_target_readiness.py",
                    "--ui",
                    str(ui),
                    "--capture",
                    str(capture),
                    "--min-line",
                    "2",
                    "--expected-post-id",
                    TARGET_ID,
                    "--expected-url",
                    TARGET_URL,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertFalse(result["ready"])
            self.assertEqual(result["reason"], "capture-jsonl-parse-error")
            self.assertEqual(result["verification_min_line"], 2)

    def test_capture_mode_can_early_ready_and_reuses_analyzer_verification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            ui = temp / "ui.xml"
            capture = temp / "capture.jsonl"
            ui.write_text(ui_xml(), encoding="utf-8")
            capture.write_text(
                json.dumps(capture_story_record(), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            process = subprocess.run(
                [
                    sys.executable,
                    "facebook_target_readiness.py",
                    "--ui",
                    str(ui),
                    "--capture",
                    str(capture),
                    "--expected-post-id",
                    TARGET_ID,
                    "--expected-url",
                    TARGET_URL,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertTrue(result["ready"])
            self.assertEqual(result["matched_post_id"], TARGET_ID)
            self.assertEqual(result["matched_line"], 1)
            self.assertEqual(result["verification_min_line"], 1)

    def test_capture_min_line_excludes_old_target_and_target_window_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            ui = temp / "ui.xml"
            capture = temp / "capture.jsonl"
            ui.write_text(ui_xml(), encoding="utf-8")
            capture.write_text(
                json.dumps(capture_story_record(), ensure_ascii=False)
                + "\n"
                + json.dumps(
                    capture_story_record(
                        post_id="1555555555555555",
                        author="旧页面作者",
                        message=TARGET_MESSAGE,
                    ),
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            process = subprocess.run(
                [
                    sys.executable,
                    "facebook_target_readiness.py",
                    "--ui",
                    str(ui),
                    "--capture",
                    str(capture),
                    "--min-line",
                    "2",
                    "--expected-post-id",
                    TARGET_ID,
                    "--expected-url",
                    TARGET_URL,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertFalse(result["ready"])
            self.assertEqual(result["reason"], "verification-not-matched")
            self.assertEqual(result["verification_min_line"], 2)

    def test_capture_mode_rejects_truncated_graphql_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            ui = temp / "ui.xml"
            capture = temp / "capture.jsonl"
            ui.write_text(ui_xml(), encoding="utf-8")
            record = capture_story_record()
            record["response_body"] = record["response_body"][:-5]
            capture.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            process = subprocess.run(
                [
                    sys.executable,
                    "facebook_target_readiness.py",
                    "--ui",
                    str(ui),
                    "--capture",
                    str(capture),
                    "--expected-post-id",
                    TARGET_ID,
                    "--expected-url",
                    TARGET_URL,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertFalse(result["ready"])
            self.assertEqual(result["reason"], "capture-response-parse-error")

    def test_invalid_report_is_json_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            ui = temp / "ui.xml"
            report = temp / "report.json"
            posts = temp / "posts.csv"
            ui.write_text(ui_xml(), encoding="utf-8")
            report.write_text("not-json", encoding="utf-8")
            posts.write_text("post_id,author_id,author_name,message,url\n", encoding="utf-8")
            process = subprocess.run(
                [
                    sys.executable,
                    "facebook_target_readiness.py",
                    "--ui",
                    str(ui),
                    "--report",
                    str(report),
                    "--posts",
                    str(posts),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertFalse(result["ready"])
            self.assertEqual(result["reason"], "report-invalid")


if __name__ == "__main__":
    unittest.main()
