from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any, Iterator

from .errors import ConfigurationError
from .models import CareerCategory, LiepinJobRecord


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def atomic_replace(source: Path, destination: Path, *, attempts: int = 4) -> None:
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt >= attempts - 1:
                raise
            time.sleep(0.1 * (attempt + 1))


def _temporary_path(destination: Path) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    return Path(raw_path)


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class LiepinStateStore:
    """SQLite-backed checkpoint and deduplication store.

    A category and all of its records are committed in one transaction. A crash
    can therefore replay a category without producing duplicate rows.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "LiepinStateStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def _initialize(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS categories (
                    url TEXT PRIMARY KEY,
                    level_1 TEXT NOT NULL,
                    level_2 TEXT NOT NULL,
                    level_3 TEXT NOT NULL,
                    path TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    record_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    record_id TEXT PRIMARY KEY,
                    source_job_id TEXT NOT NULL,
                    category_url TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    UNIQUE(category_url, source_job_id),
                    FOREIGN KEY(category_url) REFERENCES categories(url)
                );

                CREATE INDEX IF NOT EXISTS idx_categories_status
                    ON categories(active, status, attempts);
                CREATE INDEX IF NOT EXISTS idx_jobs_source_job_id
                    ON jobs(source_job_id);
                """
            )
            self._ensure_category_column("completion_reason", "TEXT")
            self._ensure_category_column(
                "pages_fetched", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_category_column(
                "filtered_non_numeric", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_category_column(
                "quota_target", "INTEGER NOT NULL DEFAULT 0"
            )

    def _ensure_category_column(self, name: str, declaration: str) -> None:
        columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(categories)")
        }
        if name not in columns:
            self.connection.execute(
                f"ALTER TABLE categories ADD COLUMN {name} {declaration}"
            )

    def set_meta(self, key: str, value: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO meta(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row else None

    def prepare_run(self, *, quota_target: int, numeric_salary_only: bool) -> int:
        """Bind the checkpoint to one salary mode and reopen obsolete small quotas."""
        if quota_target < 1:
            raise ConfigurationError("quota_target must be positive")
        raw_scope = self.get_meta("run_scope")
        existing_scope: dict[str, Any] | None = None
        if raw_scope:
            try:
                decoded = json.loads(raw_scope)
            except json.JSONDecodeError as exc:
                raise ConfigurationError("Liepin checkpoint run_scope is malformed") from exc
            if isinstance(decoded, dict):
                existing_scope = decoded
        existing_data = bool(
            self.connection.execute(
                """
                SELECT EXISTS(SELECT 1 FROM jobs)
                    OR EXISTS(SELECT 1 FROM categories WHERE status != 'pending')
                """
            ).fetchone()[0]
        )
        existing_numeric = (
            bool(existing_scope.get("numeric_salary_only", False))
            if existing_scope is not None
            else False
        )
        if existing_data and existing_numeric != numeric_salary_only:
            raise ConfigurationError(
                "checkpoint belongs to a different numeric_salary_only scope; "
                "use another output directory"
            )

        scope = {
            "schema_version": 2,
            "site": "liepin",
            "numeric_salary_only": numeric_salary_only,
        }
        now = _utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO meta(key, value) VALUES('run_scope', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (json.dumps(scope, ensure_ascii=False, sort_keys=True),),
            )
            connection.execute(
                """
                INSERT INTO meta(key, value) VALUES('quota_target', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(quota_target),),
            )
            cursor = connection.execute(
                """
                UPDATE categories
                SET status='pending', attempts=0, completion_reason=NULL,
                    last_error=NULL, quota_target=?, updated_at=?
                WHERE active=1 AND status='done' AND record_count < ?
                  AND (completion_reason IS NULL OR completion_reason='quota_met')
                """,
                (quota_target, now, quota_target),
            )
        return cursor.rowcount

    def upsert_taxonomy(
        self,
        categories: list[CareerCategory],
        *,
        snapshot_hash: str,
    ) -> None:
        now = _utc_now()
        with self.transaction() as connection:
            connection.execute("UPDATE categories SET active = 0")
            for category in categories:
                connection.execute(
                    """
                    INSERT INTO categories(
                        url, level_1, level_2, level_3, path, active, updated_at
                    ) VALUES(?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        level_1=excluded.level_1,
                        level_2=excluded.level_2,
                        level_3=excluded.level_3,
                        path=excluded.path,
                        active=1,
                        updated_at=excluded.updated_at
                    """,
                    (
                        category.url,
                        category.level_1,
                        category.level_2,
                        category.level_3,
                        category.path,
                        now,
                    ),
                )
            for key, value in (
                ("taxonomy_snapshot_hash", snapshot_hash),
                ("taxonomy_refreshed_at", now),
            ):
                connection.execute(
                    """
                    INSERT INTO meta(key, value) VALUES(?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (key, value),
                )

    def active_categories(self) -> list[CareerCategory]:
        rows = self.connection.execute(
            """
            SELECT level_1, level_2, level_3, url
            FROM categories
            WHERE active = 1
            ORDER BY level_1, level_2, level_3, url
            """
        ).fetchall()
        return [
            CareerCategory(row["level_1"], row["level_2"], row["level_3"], row["url"])
            for row in rows
        ]

    def reset_interrupted(self) -> int:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE categories
                SET status='pending', last_error='interrupted_before_commit', updated_at=?
                WHERE active=1 AND status='running'
                """,
                (_utc_now(),),
            )
        return cursor.rowcount

    def list_candidates(
        self,
        *,
        limit: int | None,
        retry_failed: bool,
        max_attempts: int,
    ) -> list[CareerCategory]:
        if retry_failed:
            predicate = "status='pending' OR (status='failed' AND attempts < ?)"
            parameters: list[Any] = [max_attempts]
        else:
            predicate = "status='pending'"
            parameters = []
        query = f"""
            SELECT level_1, level_2, level_3, url
            FROM categories
            WHERE active=1 AND ({predicate})
            ORDER BY level_1, level_2, level_3, url
        """
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)
        rows = self.connection.execute(query, parameters).fetchall()
        return [
            CareerCategory(row["level_1"], row["level_2"], row["level_3"], row["url"])
            for row in rows
        ]

    def claim_category(self, category: CareerCategory, *, max_attempts: int) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE categories
                SET status='running', attempts=attempts+1, last_error=NULL, updated_at=?
                WHERE url=? AND active=1 AND status IN ('pending', 'failed')
                    AND attempts < ?
                """,
                (_utc_now(), category.url, max_attempts),
            )
        return cursor.rowcount == 1

    def complete_category(
        self,
        category: CareerCategory,
        records: list[LiepinJobRecord],
        *,
        completion_reason: str,
        pages_fetched: int,
        filtered_non_numeric: int,
        quota_target: int,
    ) -> int:
        if completion_reason not in {
            "quota_met",
            "exhausted_below_quota",
            "page_limit_below_quota",
            "empty",
        }:
            raise ValueError(f"invalid completion_reason: {completion_reason}")
        if pages_fetched < 1 or filtered_non_numeric < 0 or quota_target < 1:
            raise ValueError("invalid completion metrics")
        with self.transaction() as connection:
            for record in records:
                payload = json.dumps(
                    record.to_unified_dict(), ensure_ascii=False, sort_keys=True
                )
                connection.execute(
                    """
                    INSERT INTO jobs(
                        record_id, source_job_id, category_url, payload_json, captured_at
                    ) VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(category_url, source_job_id) DO UPDATE SET
                        payload_json=excluded.payload_json,
                        captured_at=excluded.captured_at
                    """,
                    (
                        record.record_id,
                        record.job_id,
                        category.url,
                        payload,
                        record.captured_at,
                    ),
                )
            count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM jobs WHERE category_url=?",
                    (category.url,),
                ).fetchone()["count"]
            )
            connection.execute(
                """
                UPDATE categories
                SET status=?, record_count=?, last_error=NULL,
                    completion_reason=?, pages_fetched=?, filtered_non_numeric=?,
                    quota_target=?, updated_at=?
                WHERE url=?
                """,
                (
                    "empty" if completion_reason == "empty" else "done",
                    count,
                    completion_reason,
                    pages_fetched,
                    filtered_non_numeric,
                    quota_target,
                    _utc_now(),
                    category.url,
                ),
            )
        return count

    def mark_failed(
        self,
        category: CareerCategory,
        message: str,
        *,
        pages_fetched: int = 0,
        filtered_non_numeric: int = 0,
    ) -> None:
        safe_message = " ".join(message.split())[:500]
        with self.connection:
            self.connection.execute(
                """
                UPDATE categories
                SET status='failed', last_error=?, completion_reason='failed',
                    pages_fetched=?, filtered_non_numeric=?, updated_at=?
                WHERE url=?
                """,
                (
                    safe_message,
                    max(0, pages_fetched),
                    max(0, filtered_non_numeric),
                    _utc_now(),
                    category.url,
                ),
            )

    def summary(self) -> dict[str, int]:
        status_rows = self.connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM categories
            WHERE active=1
            GROUP BY status
            """
        ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in status_rows}
        records = self.connection.execute(
            """
            SELECT COUNT(*) AS records, COUNT(DISTINCT source_job_id) AS unique_jobs
            FROM jobs
            WHERE category_url IN (SELECT url FROM categories WHERE active=1)
            """
        ).fetchone()
        return {
            "categories_total": sum(counts.values()),
            "categories_done": counts.get("done", 0),
            "categories_empty": counts.get("empty", 0),
            "categories_failed": counts.get("failed", 0),
            "categories_running": counts.get("running", 0),
            "categories_pending": counts.get("pending", 0),
            "records_total": int(records["records"]),
            "unique_source_jobs": int(records["unique_jobs"]),
        }

    def export_jobs(self, path: Path) -> int:
        rows = self.connection.execute(
            """
            SELECT payload_json
            FROM jobs
            WHERE category_url IN (SELECT url FROM categories WHERE active=1)
            ORDER BY category_url, record_id
            """
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = _temporary_path(path)
        count = 0
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                for row in rows:
                    handle.write(str(row["payload_json"]))
                    handle.write("\n")
                    count += 1
                handle.flush()
                os.fsync(handle.fileno())
            atomic_replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return count

    def export_coverage(self, path: Path) -> dict[str, Any]:
        payload = self.coverage()
        atomic_write_json(path, payload)
        return payload

    def coverage(self) -> dict[str, Any]:
        categories = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT level_1, level_2, level_3, path, url, status,
                       attempts, record_count, last_error, completion_reason,
                       pages_fetched, filtered_non_numeric, quota_target, updated_at
                FROM categories
                WHERE active=1
                ORDER BY level_1, level_2, level_3, url
                """
            ).fetchall()
        ]
        quota_target = int(self.get_meta("quota_target") or 0)
        raw_scope = self.get_meta("run_scope")
        try:
            scope = json.loads(raw_scope) if raw_scope else {}
        except json.JSONDecodeError:
            scope = {}
        scope = scope if isinstance(scope, dict) else {}
        outcomes = {
            "quota_met": 0,
            "exhausted_below_quota": 0,
            "empty": 0,
            "failed": 0,
            "page_limit_below_quota": 0,
            "pending": 0,
            "running": 0,
        }
        for category in categories:
            status = str(category["status"])
            reason = category.get("completion_reason")
            count = int(category["record_count"])
            if status == "empty":
                outcome = "empty"
            elif status == "failed":
                outcome = "failed"
            elif status in {"pending", "running"}:
                outcome = status
            elif reason in {
                "quota_met",
                "exhausted_below_quota",
                "page_limit_below_quota",
            }:
                outcome = str(reason)
            elif quota_target and count >= quota_target:
                outcome = "quota_met"
            else:
                outcome = "failed"
            category["quota_outcome"] = outcome
            outcomes[outcome] += 1

        quota_met_rate = (
            0.0
            if not categories
            else round(outcomes["quota_met"] / len(categories), 6)
        )
        payload: dict[str, Any] = {
            "generated_at": _utc_now(),
            "taxonomy_snapshot_hash": self.get_meta("taxonomy_snapshot_hash"),
            "taxonomy_refreshed_at": self.get_meta("taxonomy_refreshed_at"),
            "quota_target": quota_target,
            "numeric_salary_only": bool(scope.get("numeric_salary_only", False)),
            "quota_outcomes": outcomes,
            "quota_met_rate": quota_met_rate,
            "summary": self.summary(),
            "categories": categories,
        }
        return payload
