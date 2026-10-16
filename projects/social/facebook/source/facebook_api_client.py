"""Small HTTP client for the local Facebook capture API.

The client deliberately keeps submission one-shot.  Polling may issue repeated
GET requests, but a failed POST is never retried and a wait timeout never
cancels the server-side job.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote, urlparse

import requests
from requests import Response, Session
from requests.exceptions import RequestException


DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_HTTP_TIMEOUT = 30.0
DEFAULT_WAIT_TIMEOUT = 900.0
DEFAULT_POLL_INTERVAL = 2.0
POLLING_STATUSES = frozenset({"queued", "running", "cancelling"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
REACTION_USERS_ARTIFACT = "facebook-reactor-users.csv"


TimeoutValue = float | tuple[float, float]


class FacebookApiError(RuntimeError):
    """Base class for client, protocol, and job errors."""


class FacebookApiTransportError(FacebookApiError):
    """The HTTP exchange could not be completed."""


class FacebookApiHttpError(FacebookApiError):
    """The API returned a non-2xx HTTP response."""

    def __init__(
        self,
        *,
        method: str,
        url: str,
        status_code: int,
        detail: str,
        payload: Any = None,
    ) -> None:
        self.method = method
        self.url = url
        self.status_code = status_code
        self.payload = payload
        self.detail = detail
        super().__init__(f"HTTP {status_code} for {method} {url}: {detail}")


class FacebookApiProtocolError(FacebookApiError):
    """The API returned a successful response with an unusable payload."""


class FacebookJobError(FacebookApiError):
    """A server-side job reached failed or cancelled."""

    def __init__(self, job_id: str, status: str, error: Any, payload: Mapping[str, Any]) -> None:
        self.job_id = job_id
        self.status = status
        self.error = error
        self.payload = dict(payload)
        detail = _format_detail(error) or "no server error was provided"
        super().__init__(f"job {job_id} ended with status {status}: {detail}")


class FacebookUnknownStatusError(FacebookApiError):
    """The server returned a status outside the client's known contract."""

    def __init__(self, job_id: str, status: Any, payload: Mapping[str, Any]) -> None:
        self.job_id = job_id
        self.status = status
        self.payload = dict(payload)
        super().__init__(f"job {job_id} returned unknown status {status!r}")


class FacebookWaitTimeoutError(FacebookApiError):
    """The local wait deadline elapsed; the server job was left untouched."""

    def __init__(self, job_id: str, overall_timeout: float) -> None:
        self.job_id = job_id
        self.overall_timeout = overall_timeout
        super().__init__(
            f"timed out waiting {overall_timeout:g}s for job {job_id}; "
            "the server-side job was not cancelled"
        )


class FacebookApiDownloadError(FacebookApiError):
    """An artifact could not be downloaded and committed atomically."""


def _format_detail(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _validate_timeout(value: TimeoutValue, name: str) -> TimeoutValue:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be positive")
    if isinstance(value, tuple):
        if len(value) != 2 or any(isinstance(item, bool) for item in value):
            raise ValueError(f"{name} must be a finite positive number or (connect, read) tuple")
        try:
            values = (float(value[0]), float(value[1]))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite positive number or (connect, read) tuple") from exc
        if any(not math.isfinite(item) or item <= 0 for item in values):
            raise ValueError(f"{name} must be a finite positive number or (connect, read) tuple")
        return values
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite positive number") from exc
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return numeric


def _format_timeout(value: TimeoutValue, remaining: float | None = None) -> float | tuple[float, float]:
    if remaining is None:
        return value
    # requests accepts a tuple and applies the connect/read values separately.
    # Cap both values so an individual poll cannot outlive the overall wait.
    remaining = max(float(remaining), 0.001)
    if isinstance(value, tuple):
        return (min(value[0], remaining), min(value[1], remaining))
    return min(value, remaining)


def _response_text(response: Response, limit: int = 4096) -> str:
    try:
        return response.text[:limit]
    except (UnicodeError, RequestException, OSError):
        return "<response body unavailable>"


def _response_json(response: Response) -> Any:
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return None


class FacebookApiClient:
    """Requests-based client for ``facebook_capture_api.py``.

    ``session`` can be an authenticated or tunnel-aware requests Session.  The
    optional ``headers`` mapping is applied to every request without changing
    the caller-owned Session object.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        session: Session | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: TimeoutValue = DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty URL")
        normalized = base_url.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute http(s) URL")
        self.base_url = normalized
        self.session = session or requests.Session()
        self._owns_session = session is None
        self.headers = dict(headers or {})
        self.timeout = _validate_timeout(timeout, "timeout")

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def __enter__(self) -> "FacebookApiClient":
        return self

    def __exit__(self, _exc_type: Any, _exc_value: Any, _traceback: Any) -> None:
        self.close()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            raise ValueError("API paths must start with '/'")
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, *, timeout: TimeoutValue | None = None, **kwargs: Any) -> Response:
        url = self._url(path)
        request_kwargs = dict(kwargs)
        request_kwargs["timeout"] = _validate_timeout(timeout or self.timeout, "timeout")
        if self.headers:
            request_kwargs["headers"] = dict(self.headers)
        try:
            return self.session.request(method, url, **request_kwargs)
        except RequestException as exc:
            raise FacebookApiTransportError(f"HTTP request failed for {method} {url}: {exc}") from exc

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        timeout: TimeoutValue | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if payload is not None:
            kwargs["json"] = dict(payload)
        response = self._request(method, path, timeout=timeout, **kwargs)
        body = _response_json(response)
        if not 200 <= response.status_code < 300:
            detail = ""
            if isinstance(body, Mapping):
                detail = _format_detail(body.get("error"))
            if not detail:
                detail = _response_text(response) or response.reason or "HTTP error"
            raise FacebookApiHttpError(
                method=method,
                url=response.url,
                status_code=response.status_code,
                detail=detail,
                payload=body,
            )
        if body is None:
            raise FacebookApiProtocolError(
                f"expected JSON from {method} {response.url}, got: {_response_text(response)}"
            )
        if not isinstance(body, dict):
            raise FacebookApiProtocolError(
                f"expected a JSON object from {method} {response.url}, got {type(body).__name__}"
            )
        return body

    @staticmethod
    def _job_id(job_id: str) -> str:
        value = str(job_id or "").strip()
        if not value:
            raise ValueError("job_id must be non-empty")
        return value

    def submit(
        self,
        post_url: str | None = None,
        *,
        post_id: str | None = None,
        collect: Iterable[str] | None = None,
        save_artifacts: Iterable[str] | None = None,
        reaction_scroll_limit: int | None = None,
    ) -> dict[str, Any]:
        """Submit exactly one job and return the server's 202 payload.

        Omitting ``reaction_scroll_limit`` preserves the API's unlimited,
        terminal-pagination default.  Passing an integer explicitly requests a
        bounded sample.
        """
        payload: dict[str, Any] = {}
        if post_url is not None:
            payload["post_url"] = post_url
        if post_id is not None:
            payload["post_id"] = post_id
        if not payload:
            raise ValueError("post_url or post_id is required")
        if collect is not None:
            if isinstance(collect, (str, bytes)):
                raise TypeError("collect must be an iterable of names, not a string")
            payload["collect"] = list(collect)
        if save_artifacts is not None:
            if isinstance(save_artifacts, (str, bytes)):
                raise TypeError("save_artifacts must be an iterable of groups, not a string")
            payload["save_artifacts"] = list(save_artifacts)
        if reaction_scroll_limit is not None:
            payload["reaction_scroll_limit"] = reaction_scroll_limit
        response = self._request_json("POST", "/v1/jobs", payload=payload)
        if not str(response.get("job_id") or "").strip():
            raise FacebookApiProtocolError("submit response did not contain job_id")
        return response

    def get_status(self, job_id: str) -> dict[str, Any]:
        value = self._job_id(job_id)
        return self._request_json("GET", f"/v1/jobs/{quote(value, safe='')}")

    def wait(
        self,
        job_id: str,
        overall_timeout: float = DEFAULT_WAIT_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> dict[str, Any]:
        """Poll until terminal, without cancelling on timeout.

        ``overall_timeout`` is a local polling deadline.  The requests timeout
        controls connect/read inactivity for each HTTP call and is not an
        absolute wall-clock cutoff for an in-flight request.
        """
        value = self._job_id(job_id)
        if isinstance(overall_timeout, bool):
            raise ValueError("overall_timeout must be a finite non-negative number")
        try:
            overall_timeout = float(overall_timeout)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("overall_timeout must be a finite non-negative number") from exc
        if not math.isfinite(overall_timeout) or overall_timeout < 0:
            raise ValueError("overall_timeout must be a finite non-negative number")
        if isinstance(poll_interval, bool):
            raise ValueError("poll_interval must be a finite positive number")
        try:
            poll_interval = float(poll_interval)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("poll_interval must be a finite positive number") from exc
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be a finite positive number")
        deadline = time.monotonic() + overall_timeout
        first_poll = True
        while True:
            remaining = deadline - time.monotonic()
            if not first_poll and remaining <= 0:
                raise FacebookWaitTimeoutError(value, overall_timeout)
            try:
                status = self._request_json(
                    "GET",
                    f"/v1/jobs/{quote(value, safe='')}",
                    timeout=_format_timeout(self.timeout, max(remaining, 0.001)),
                )
            except FacebookApiTransportError as exc:
                if time.monotonic() >= deadline:
                    raise FacebookWaitTimeoutError(value, overall_timeout) from exc
                raise
            first_poll = False
            state = str(status.get("status") or "").strip().lower()
            if state == "completed":
                return status
            if state in {"failed", "cancelled"}:
                raise FacebookJobError(value, state, status.get("error"), status)
            if state not in POLLING_STATUSES:
                raise FacebookUnknownStatusError(value, status.get("status"), status)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FacebookWaitTimeoutError(value, overall_timeout)
            time.sleep(min(poll_interval, remaining))

    def result(self, job_id: str) -> dict[str, Any]:
        value = self._job_id(job_id)
        return self._request_json("GET", f"/v1/jobs/{quote(value, safe='')}/result")

    def cancel(self, job_id: str) -> dict[str, Any]:
        value = self._job_id(job_id)
        return self._request_json("POST", f"/v1/jobs/{quote(value, safe='')}/cancel")

    def download(self, job_id: str, artifact_name: str, output_path: str | os.PathLike[str]) -> Path:
        """Download an API artifact to ``output_path`` using temp-file replacement."""
        value = self._job_id(job_id)
        name = str(artifact_name or "").strip()
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError("artifact_name must be a single API artifact name")
        target = Path(output_path)
        if target.exists() and target.is_dir():
            raise FacebookApiDownloadError(f"artifact output path is a directory: {target}")
        url_path = f"/v1/jobs/{quote(value, safe='')}/artifacts/{quote(name, safe='')}"
        try:
            response = self._request(
                "GET",
                url_path + "?download=1",
                timeout=self.timeout,
                stream=True,
            )
        except FacebookApiTransportError as exc:
            raise FacebookApiDownloadError(f"artifact download failed for job {value}: {exc}") from exc

        try:
            if not 200 <= response.status_code < 300:
                body = _response_json(response)
                detail = _format_detail(body.get("error")) if isinstance(body, Mapping) else ""
                if not detail:
                    detail = _response_text(response) or response.reason or "HTTP error"
                raise FacebookApiDownloadError(
                    f"artifact download returned HTTP {response.status_code} for job {value}: {detail}"
                )
            try:
                content_encoding = response.headers.get("Content-Encoding", "").strip().lower()
                if content_encoding and content_encoding != "identity":
                    raise FacebookApiDownloadError(
                        "artifact download uses unsupported Content-Encoding "
                        f"{content_encoding!r}; cannot verify Content-Length after requests decoding"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
                expected_header = response.headers.get("Content-Length")
                expected = int(expected_header) if expected_header is not None else None
                received = 0
                with temporary.open("xb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        received += len(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                if expected is not None and expected != received:
                    raise FacebookApiDownloadError(
                        f"artifact download truncated for job {value}: expected {expected} bytes, received {received}"
                    )
                if received <= 0:
                    raise FacebookApiDownloadError(f"artifact download was empty for job {value}")
                os.replace(temporary, target)
                return target
            except FacebookApiDownloadError:
                raise
            except (OSError, RequestException, TypeError, ValueError) as exc:
                raise FacebookApiDownloadError(f"artifact download failed for job {value}: {exc}") from exc
        finally:
            response.close()
            temporary = locals().get("temporary")
            if isinstance(temporary, Path):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _parse_headers(values: Sequence[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values:
        name, separator, content = value.partition("=")
        if not separator or not name.strip():
            raise ValueError("--header must use NAME=VALUE")
        headers[name.strip()] = content
    return headers


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Submit or resume a Facebook capture API job")
    parser.add_argument("post_url", nargs="?", help="Facebook post URL; omit when resuming --job-id")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--job-id", help="resume an existing job instead of submitting a new one")
    parser.add_argument("--header", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--request-timeout", type=float, default=DEFAULT_HTTP_TIMEOUT)
    parser.add_argument("--overall-timeout", type=float, default=DEFAULT_WAIT_TIMEOUT)
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument(
        "--reaction-scroll-limit",
        "--bounded-sample",
        type=int,
        default=None,
        dest="reaction_scroll_limit",
        help="explicit bounded sample; omitted keeps the full terminal-pagination default",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        if bool(args.post_url) == bool(args.job_id):
            parser.error("provide exactly one of POST_URL or --job-id")
        headers = _parse_headers(args.header)
        with FacebookApiClient(
            args.base_url,
            headers=headers,
            timeout=args.request_timeout,
        ) as client:
            if args.job_id:
                job = {"job_id": args.job_id}
            else:
                job = client.submit(args.post_url, reaction_scroll_limit=args.reaction_scroll_limit)
            job_id = str(job.get("job_id") or args.job_id or "")
            if not job_id:
                raise FacebookApiProtocolError("submit response did not contain job_id")
            print(
                json.dumps(
                    {
                        "job_id": job_id,
                        "status": job.get("status") or ("resuming" if args.job_id else "submitted"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            completed = client.wait(
                job_id,
                overall_timeout=args.overall_timeout,
                poll_interval=args.poll_interval,
            )
            result = client.result(job_id)
            output_dir = args.output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(output_dir / "result.json", result)
            client.download(
                job_id,
                REACTION_USERS_ARTIFACT,
                output_dir / REACTION_USERS_ARTIFACT,
            )
            print(
                json.dumps(
                    {
                        "job_id": job_id,
                        "status": completed.get("status"),
                        "completion_scope": result.get("completion_scope"),
                        "result_path": str(output_dir / "result.json"),
                        "artifact_path": str(output_dir / REACTION_USERS_ARTIFACT),
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    except (FacebookApiError, OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
