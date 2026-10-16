from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator, Sequence

from .models import CategoryPath


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
    )


class StateError(RuntimeError):
    pass


class RunScopeMismatch(StateError):
    pass


class CrawlState:
    """SQLite checkpoint and idempotency index."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CrawlState":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS categories (
                path_key TEXT PRIMARY KEY,
                search_code TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                level_1_code TEXT NOT NULL,
                level_1_name TEXT NOT NULL,
                level_2_code TEXT NOT NULL,
                level_2_name TEXT NOT NULL,
                level_3_code TEXT NOT NULL,
                level_3_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_page INTEGER NOT NULL DEFAULT 1,
                items_count INTEGER NOT NULL DEFAULT 0,
                mismatch_count INTEGER NOT NULL DEFAULT 0,
                total_available INTEGER,
                last_error_type TEXT,
                last_error_message TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_categories_work
            ON categories(active, status, search_code, ordinal);

            CREATE TABLE IF NOT EXISTS records (
                record_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS category_jobs (
                path_key TEXT NOT NULL,
                record_id TEXT NOT NULL,
                PRIMARY KEY(path_key, record_id),
                FOREIGN KEY(path_key) REFERENCES categories(path_key),
                FOREIGN KEY(record_id) REFERENCES records(record_id)
            );
            """
        )
        self.connection.commit()

    def prepare_run(
        self, scope: dict[str, Any], categories: Sequence[CategoryPath]
    ) -> None:
        stored_scope = dict(scope)
        stored_scope.setdefault("numeric_salary_only", False)
        existing = self._metadata("run_scope")
        if existing is not None:
            existing_comparable = dict(existing) if isinstance(existing, dict) else existing
            requested_comparable = dict(stored_scope)
            if isinstance(existing_comparable, dict):
                # Older checkpoints predate the explicit salary-quota mode; their
                # behavior is equivalent to numeric_salary_only=False.
                existing_comparable.setdefault("numeric_salary_only", False)
            requested_comparable.setdefault("numeric_salary_only", False)
            if existing_comparable != requested_comparable:
                raise RunScopeMismatch(
                    "checkpoint belongs to a different crawl scope; use another state_db_path"
                )
        now = utc_now_iso()
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value_json) VALUES(?, ?)",
                (
                    "run_scope",
                    json.dumps(stored_scope, ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.execute("UPDATE categories SET active = 0")
            connection.execute(
                "UPDATE categories SET status = 'partial' WHERE status = 'running'"
            )
            for category in categories:
                connection.execute(
                    """
                    INSERT INTO categories(
                        path_key, search_code, ordinal,
                        level_1_code, level_1_name,
                        level_2_code, level_2_name,
                        level_3_code, level_3_name,
                        active, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(path_key) DO UPDATE SET
                        search_code = excluded.search_code,
                        ordinal = excluded.ordinal,
                        level_1_code = excluded.level_1_code,
                        level_1_name = excluded.level_1_name,
                        level_2_code = excluded.level_2_code,
                        level_2_name = excluded.level_2_name,
                        level_3_code = excluded.level_3_code,
                        level_3_name = excluded.level_3_name,
                        active = 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        category.path_key,
                        category.search_code,
                        category.ordinal,
                        category.level_1_code,
                        category.level_1_name,
                        category.level_2_code,
                        category.level_2_name,
                        category.level_3_code,
                        category.level_3_name,
                        now,
                    ),
                )

    def _metadata(self, key: str) -> Any | None:
        row = self.connection.execute(
            "SELECT value_json FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else json.loads(row["value_json"])

    def set_rate_limit_cooldown(
        self,
        seconds: float,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist a bounded global cooldown so immediate reruns stay offline."""
        duration = float(seconds)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("rate-limit cooldown must be a positive finite number")
        duration = min(duration, 86_400.0)
        started = now or datetime.now(UTC)
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        payload = {
            "started_at": started.astimezone(UTC).isoformat(timespec="seconds"),
            "cooldown_until": (
                started.astimezone(UTC) + timedelta(seconds=duration)
            ).isoformat(timespec="seconds"),
            "cooldown_seconds": duration,
            "reason": str(reason)[:1000],
        }
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value_json) VALUES(?, ?)",
                (
                    "rate_limit_cooldown",
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
        return payload

    def clear_rate_limit_cooldown(self) -> None:
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM metadata WHERE key = ?", ("rate_limit_cooldown",)
            )

    def active_rate_limit_cooldown(
        self, *, now: datetime | None = None
    ) -> dict[str, Any] | None:
        payload = self._metadata("rate_limit_cooldown")
        if not isinstance(payload, dict):
            return None
        try:
            cooldown_until = datetime.fromisoformat(
                str(payload["cooldown_until"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            self.clear_rate_limit_cooldown()
            return None
        if cooldown_until.tzinfo is None:
            cooldown_until = cooldown_until.replace(tzinfo=UTC)
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        remaining = (cooldown_until - current.astimezone(UTC)).total_seconds()
        if remaining <= 0:
            self.clear_rate_limit_cooldown()
            return None
        return {**payload, "remaining_seconds": remaining}

    def pending_search_codes(self, max_attempts: int) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT search_code, MIN(ordinal) AS first_ordinal,
                   MAX(CASE WHEN last_error_type = 'RateLimitedError' THEN 1 ELSE 0 END)
                       AS deferred_sort
            FROM categories
            WHERE active = 1
              AND (
                    status IN ('pending', 'partial', 'running')
                    OR (status = 'failed' AND attempts < ?)
                  )
            GROUP BY search_code
            ORDER BY deferred_sort, first_ordinal, search_code
            """,
            (max_attempts,),
        ).fetchall()
        return [str(row["search_code"]) for row in rows]

    def aliases_for_code(self, search_code: str) -> list[CategoryPath]:
        rows = self.connection.execute(
            """
            SELECT * FROM categories
            WHERE active = 1 AND search_code = ?
            ORDER BY ordinal
            """,
            (search_code,),
        ).fetchall()
        return [
            CategoryPath(
                level_1_code=row["level_1_code"],
                level_1_name=row["level_1_name"],
                level_2_code=row["level_2_code"],
                level_2_name=row["level_2_name"],
                level_3_code=row["level_3_code"],
                level_3_name=row["level_3_name"],
                ordinal=row["ordinal"],
            )
            for row in rows
        ]

    def begin_code(self, search_code: str) -> tuple[int, int]:
        now = utc_now_iso()
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE categories
                SET status = 'running', attempts = attempts + 1, updated_at = ?,
                    last_error_type = NULL, last_error_message = NULL
                WHERE active = 1 AND search_code = ?
                """,
                (now, search_code),
            )
        row = self.connection.execute(
            """
            SELECT MIN(next_page) AS next_page, MAX(attempts) AS attempts
            FROM categories WHERE active = 1 AND search_code = ?
            """,
            (search_code,),
        ).fetchone()
        return int(row["next_page"] or 1), int(row["attempts"] or 1)

    def code_record_count(self, search_code: str) -> int:
        return len(self.code_record_ids(search_code))

    def code_record_ids(self, search_code: str) -> set[str]:
        rows = self.connection.execute(
            """
            SELECT DISTINCT cj.record_id
            FROM category_jobs cj
            JOIN categories c ON c.path_key = cj.path_key
            WHERE c.active = 1 AND c.search_code = ?
            """,
            (search_code,),
        ).fetchall()
        return {str(row["record_id"]) for row in rows}

    def finish_code(
        self,
        search_code: str,
        records: Iterable[dict[str, Any]],
        *,
        status: str,
        next_page: int,
        mismatch_count: int,
        total_available: int | None,
    ) -> None:
        if status not in {"completed", "empty", "partial"}:
            raise ValueError(f"invalid completion status: {status}")
        aliases = self.aliases_for_code(search_code)
        now = utc_now_iso()
        with self.transaction() as connection:
            for record in records:
                payload = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                metadata = record.get("metadata")
                metadata = metadata if isinstance(metadata, dict) else {}
                job_id = str(metadata.get("job_id") or record["record_id"])
                connection.execute(
                    """
                    INSERT INTO records(record_id, job_id, payload_json, updated_at)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(record_id) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    (record["record_id"], job_id, payload, now),
                )
                for category in aliases:
                    connection.execute(
                        "INSERT OR IGNORE INTO category_jobs(path_key, record_id) VALUES(?, ?)",
                        (category.path_key, record["record_id"]),
                    )
            connection.execute(
                """
                INSERT OR IGNORE INTO category_jobs(path_key, record_id)
                SELECT target.path_key, existing_jobs.record_id
                FROM categories target
                JOIN categories source
                  ON source.active = 1
                 AND source.search_code = target.search_code
                JOIN category_jobs existing_jobs
                  ON existing_jobs.path_key = source.path_key
                WHERE target.active = 1 AND target.search_code = ?
                """,
                (search_code,),
            )
            connection.execute(
                """
                UPDATE categories
                SET status = ?, next_page = ?, mismatch_count = mismatch_count + ?,
                    total_available = ?, last_error_type = NULL,
                    last_error_message = NULL, updated_at = ?
                WHERE active = 1 AND search_code = ?
                """,
                (
                    status,
                    max(1, next_page),
                    mismatch_count,
                    total_available,
                    now,
                    search_code,
                ),
            )
            connection.execute(
                """
                UPDATE categories
                SET items_count = (
                    SELECT COUNT(*) FROM category_jobs cj
                    WHERE cj.path_key = categories.path_key
                )
                WHERE active = 1 AND search_code = ?
                """,
                (search_code,),
            )

    def fail_code(
        self, search_code: str, error_type: str, message: str, next_page: int
    ) -> None:
        now = utc_now_iso()
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE categories
                SET status = 'failed', next_page = ?, last_error_type = ?,
                    last_error_message = ?, updated_at = ?
                WHERE active = 1 AND search_code = ?
                """,
                (max(1, next_page), error_type, message[:1000], now, search_code),
            )

    def defer_code(
        self, search_code: str, error_type: str, message: str, next_page: int
    ) -> None:
        """Return a globally rate-limited code to the queue without poisoning it."""
        now = utc_now_iso()
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE categories
                SET status = 'pending', attempts = MAX(0, attempts - 1),
                    next_page = ?, last_error_type = ?, last_error_message = ?,
                    updated_at = ?
                WHERE active = 1 AND search_code = ?
                """,
                (max(1, next_page), error_type, message[:1000], now, search_code),
            )

    def coverage(self) -> dict[str, Any]:
        summary_rows = self.connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM categories WHERE active = 1 GROUP BY status
            """
        ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in summary_rows}
        categories = [dict(row) for row in self.connection.execute(
            """
            SELECT path_key, search_code, ordinal,
                   level_1_code, level_1_name,
                   level_2_code, level_2_name,
                   level_3_code, level_3_name,
                   status, attempts, next_page, items_count,
                   mismatch_count, total_available,
                   last_error_type, last_error_message, updated_at
            FROM categories WHERE active = 1 ORDER BY ordinal
            """
        ).fetchall()]
        total_paths = len(categories)
        unique_codes = len({row["search_code"] for row in categories})
        run_scope = self._metadata("run_scope")
        run_scope = run_scope if isinstance(run_scope, dict) else {}
        quota_target = int(run_scope.get("per_category_limit") or 0)
        quota_counts = {
            "unique_search_codes_total": unique_codes,
            "target_per_unique_search_code": quota_target,
            "numeric_salary_only": bool(run_scope.get("numeric_salary_only", False)),
            "quota_met": 0,
            "exhausted_below_quota": 0,
            "empty": 0,
            "failed": 0,
            "pending": 0,
            "page_limit_below_quota": 0,
            "running": 0,
        }
        code_states: dict[str, dict[str, Any]] = {}
        for row in categories:
            code = str(row["search_code"])
            current = code_states.setdefault(
                code,
                {
                    "status": str(row["status"]),
                    "items_count": int(row["items_count"]),
                },
            )
            current["items_count"] = max(
                int(current["items_count"]), int(row["items_count"])
            )
        code_outcomes: dict[str, str] = {}
        for code, current in code_states.items():
            status = str(current["status"])
            items_count = int(current["items_count"])
            if status == "empty":
                outcome = "empty"
            elif status == "failed":
                outcome = "failed"
            elif quota_target > 0 and items_count >= quota_target:
                outcome = "quota_met"
            elif status == "completed":
                outcome = "exhausted_below_quota"
            elif status == "partial":
                outcome = "page_limit_below_quota"
            elif status in {"pending", "running"}:
                outcome = status
            else:
                outcome = "failed"
            quota_counts[outcome] += 1
            code_outcomes[code] = outcome
        for row in categories:
            row["quota_outcome"] = code_outcomes[str(row["search_code"])]
        record_count = int(
            self.connection.execute(
                """
                SELECT COUNT(DISTINCT r.record_id)
                FROM records r
                JOIN category_jobs cj ON cj.record_id = r.record_id
                JOIN categories c ON c.path_key = cj.path_key
                WHERE c.active = 1
                """
            ).fetchone()[0]
        )
        covered_paths = counts.get("completed", 0) + counts.get("empty", 0)
        quota_met_rate = (
            0.0
            if not unique_codes
            else round(quota_counts["quota_met"] / unique_codes, 6)
        )
        return {
            "schema_version": 2,
            "site": "51job",
            "generated_at": utc_now_iso(),
            "run_scope": run_scope,
            "official_category_paths": total_paths,
            "unique_search_codes": unique_codes,
            "covered_paths": covered_paths,
            "coverage_rate": 0.0 if not total_paths else round(covered_paths / total_paths, 6),
            "quota_met_rate": quota_met_rate,
            "quota_counts": quota_counts,
            "record_count": record_count,
            "rate_limit_cooldown": self.active_rate_limit_cooldown(),
            "status_counts": counts,
            "categories": categories,
        }

    def export(self, output_path: Path, coverage_path: Path) -> dict[str, Any]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_name(f".{output_path.name}.tmp")
        rows = self.connection.execute(
            """
            SELECT DISTINCT r.record_id, r.payload_json
            FROM records r
            JOIN category_jobs cj ON cj.record_id = r.record_id
            JOIN categories c ON c.path_key = cj.path_key
            WHERE c.active = 1
            ORDER BY r.record_id
            """
        )
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            while batch := rows.fetchmany(100):
                for row in batch:
                    payload = json.loads(row["payload_json"])
                    category_rows = self.connection.execute(
                        """
                        SELECT c.level_1_code, c.level_1_name,
                               c.level_2_code, c.level_2_name,
                               c.level_3_code, c.level_3_name,
                               c.path_key
                        FROM category_jobs cj
                        JOIN categories c ON c.path_key = cj.path_key
                        WHERE cj.record_id = ? AND c.active = 1
                        ORDER BY c.ordinal
                        """,
                        (row["record_id"],),
                    ).fetchall()
                    category_paths = [dict(category) for category in category_rows]
                    payload["category_paths"] = category_paths
                    if category_paths:
                        primary = category_paths[0]
                        payload["category_level_1_raw"] = primary["level_1_name"]
                        payload["category_level_2_raw"] = primary["level_2_name"]
                        payload["category_level_3_raw"] = primary["level_3_name"]
                        payload["category_path_raw"] = " > ".join(
                            (
                                primary["level_1_name"],
                                primary["level_2_name"],
                                primary["level_3_name"],
                            )
                        )
                    metadata = payload.setdefault("metadata", {})
                    metadata["category_paths"] = category_paths
                    handle.write(
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
        coverage = self.coverage()
        atomic_write_json(coverage_path, coverage)
        return coverage
