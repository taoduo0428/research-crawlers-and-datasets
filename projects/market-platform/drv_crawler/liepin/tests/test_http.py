from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests

from drv_crawler.liepin.errors import AccessDeniedError
from drv_crawler.liepin.http import (
    apply_cookies,
    LiepinHttpClient,
    load_cookie_file,
    parse_cookie_header,
    redact_proxy_url,
)


class _Response:
    def __init__(self, status: int, body: bytes = b"ok") -> None:
        self.status_code = status
        self.content = body
        self.url = "https://www.liepin.com/"
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class _SequenceSession(requests.Session):
    def __init__(self, responses: list[_Response]) -> None:
        super().__init__()
        self.responses = responses
        self.calls = 0

    def get(self, *args: object, **kwargs: object) -> _Response:
        response = self.responses[self.calls]
        self.calls += 1
        return response


class HttpTests(unittest.TestCase):
    def test_cookie_sources_are_parsed_without_value_logging(self) -> None:
        self.assertEqual({"a": "1", "token": "abc=def"}, parse_cookie_header("a=1; token=abc=def"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps({"cookies": [{"name": "session", "value": "secret"}]}),
                encoding="utf-8",
            )
            cookies = load_cookie_file(path)
        self.assertEqual("session", cookies[0]["name"])

    def test_expired_storage_state_cookie_is_discarded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "cookies": [
                            {"name": "expired", "value": "old", "expires": 1},
                            {"name": "active", "value": "new", "expires": -1},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            session = requests.Session()
            count = apply_cookies(session, cookie_file=path)
        self.assertEqual(1, count)
        self.assertIsNone(session.cookies.get("expired"))
        self.assertEqual("new", session.cookies.get("active"))

    def test_proxy_credentials_are_redacted(self) -> None:
        self.assertEqual(
            "http://127.0.0.1:7897",
            redact_proxy_url("http://user:secret@127.0.0.1:7897"),
        )

    @patch("drv_crawler.liepin.http.time.sleep")
    def test_429_retries_once_then_succeeds(self, sleep: object) -> None:
        session = _SequenceSession([_Response(429), _Response(200, "正常".encode())])
        client = LiepinHttpClient(
            proxy_policy="direct", retries=1, backoff_base=0, session=session
        )
        page = client.get("https://www.liepin.com/")
        self.assertEqual("正常", page.text)
        self.assertEqual(2, session.calls)

    def test_403_is_not_blindly_retried(self) -> None:
        session = _SequenceSession([_Response(403), _Response(200)])
        client = LiepinHttpClient(
            proxy_policy="direct", retries=1, session=session
        )
        with self.assertRaises(AccessDeniedError):
            client.get("https://www.liepin.com/")
        self.assertEqual(1, session.calls)


if __name__ == "__main__":
    unittest.main()
