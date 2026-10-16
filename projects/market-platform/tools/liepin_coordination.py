"""Cross-process coordination for forward and reverse Liepin detail workers."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


FORWARD_OWNER = "browser_forward"
REVERSE_OWNER = "api_reverse"
REVERSE_SECONDARY_OWNER = "camoufox_reverse_2"
DEFERRED_OWNER = "edge_deferred"
LEGACY_MIDDLE_OWNER = "camoufox_middle"
REVERSE_OWNERS = frozenset({REVERSE_OWNER, REVERSE_SECONDARY_OWNER})


class LiepinCoordination:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS claims (
                record_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                source_index INTEGER NOT NULL,
                status TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS progress (
                owner TEXT PRIMARY KEY,
                source_index INTEGER NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "LiepinCoordination":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def has_met(self, owner: str, source_index: int) -> bool:
        if owner == FORWARD_OWNER:
            placeholders = ",".join("?" for _ in REVERSE_OWNERS)
            row = self.connection.execute(
                f"SELECT MIN(source_index) FROM progress WHERE owner IN ({placeholders})",
                tuple(REVERSE_OWNERS),
            ).fetchone()
            return bool(row is not None and row[0] is not None and source_index >= int(row[0]))
        if owner not in REVERSE_OWNERS:
            raise ValueError(f"unknown coordination owner: {owner}")
        row = self.connection.execute(
            "SELECT source_index FROM progress WHERE owner = ?",
            (FORWARD_OWNER,),
        ).fetchone()
        return bool(row is not None and source_index <= int(row[0]))

    def advance(self, owner: str, source_index: int) -> None:
        if owner == FORWARD_OWNER:
            aggregate = "MAX"
        elif owner in REVERSE_OWNERS:
            aggregate = "MIN"
        else:
            raise ValueError(f"unknown coordination owner: {owner}")
        now = time.time()
        self.connection.execute(
            f"""
            INSERT INTO progress(owner, source_index, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(owner) DO UPDATE SET
                source_index = {aggregate}(progress.source_index, excluded.source_index),
                updated_at = excluded.updated_at
            """,
            (owner, source_index, now),
        )

    def migrate_legacy_middle_claims(self) -> int:
        """Move old midpoint claims to the second reverse owner once, without losing results."""
        now = time.time()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                """
                UPDATE claims SET owner = ?, updated_at = ?
                WHERE owner = ?
                """,
                (REVERSE_SECONDARY_OWNER, now, LEGACY_MIDDLE_OWNER),
            )
            self.connection.execute(
                "DELETE FROM progress WHERE owner = ?",
                (LEGACY_MIDDLE_OWNER,),
            )
            self.connection.execute("COMMIT")
            return int(cursor.rowcount)
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def claim(self, record_id: str, owner: str, source_index: int) -> bool:
        now = time.time()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO claims(record_id, owner, source_index, status, updated_at)
                VALUES (?, ?, ?, 'claimed', ?)
                """,
                (record_id, owner, source_index, now),
            )
            if cursor.rowcount == 1:
                self.connection.execute("COMMIT")
                return True
            row = self.connection.execute(
                "SELECT owner, status FROM claims WHERE record_id = ?",
                (record_id,),
            ).fetchone()
            reclaimable = bool(
                row
                and row[0] == owner
                and row[1] not in {"success", "unavailable", "blocked"}
            )
            if reclaimable:
                self.connection.execute(
                    """
                    UPDATE claims
                    SET source_index = ?, status = 'claimed', updated_at = ?
                    WHERE record_id = ?
                    """,
                    (source_index, now, record_id),
                )
            self.connection.execute("COMMIT")
            return reclaimable
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def finish(self, record_id: str, owner: str, status: str) -> None:
        self.connection.execute(
            """
            UPDATE claims SET status = ?, updated_at = ?
            WHERE record_id = ? AND owner = ?
            """,
            (status, time.time(), record_id, owner),
        )

    def deferred_claims(self) -> list[tuple[str, int, str]]:
        return [
            (str(record_id), int(source_index), str(status))
            for record_id, source_index, status in self.connection.execute(
                """
                SELECT record_id, source_index, status
                FROM claims
                WHERE status = 'challenge'
                   OR status LIKE 'deferred_%'
                   OR (owner = ? AND status = 'claimed')
                ORDER BY source_index
                """,
                (DEFERRED_OWNER,),
            )
        ]

    def claim_deferred(self, record_id: str) -> bool:
        now = time.time()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.connection.execute(
                """
                UPDATE claims
                SET owner = ?, status = 'claimed', updated_at = ?
                WHERE record_id = ?
                  AND (
                    status = 'challenge'
                    OR status LIKE 'deferred_%'
                    OR (owner = ? AND status = 'claimed')
                  )
                """,
                (DEFERRED_OWNER, now, record_id, DEFERRED_OWNER),
            )
            self.connection.execute("COMMIT")
            return cursor.rowcount == 1
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def status(self) -> dict[str, object]:
        progress = {
            str(owner): int(index)
            for owner, index in self.connection.execute(
                "SELECT owner, source_index FROM progress"
            )
        }
        claims = {
            str(owner): int(count)
            for owner, count in self.connection.execute(
                "SELECT owner, COUNT(*) FROM claims GROUP BY owner"
            )
        }
        duplicate_claims = int(
            self.connection.execute(
                "SELECT COUNT(*) FROM (SELECT record_id FROM claims GROUP BY record_id HAVING COUNT(*) > 1)"
            ).fetchone()[0]
        )
        return {"progress": progress, "claims": claims, "duplicate_claims": duplicate_claims}
