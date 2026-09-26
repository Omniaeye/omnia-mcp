# Copyright 2026 OMNIA EYE Corporation.
# SPDX-License-Identifier: Apache-2.0
"""Durable decisions, bounded usage and expiring cross-process ownership."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from omnia_mcp.errors import OmniaError
from omnia_mcp.schemas import DecisionResult


class Ledger:
    def __init__(self, path: Path, *, clock=time.time):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.connection = sqlite3.connect(self.path, timeout=1.5, isolation_level=None)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, created REAL NOT NULL,
                result TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS decisions_cache ON decisions(cache_key, created DESC);
            CREATE TABLE IF NOT EXISTS claims (
                cache_key TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage (
                bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS failures (
                id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, code TEXT NOT NULL,
                created REAL NOT NULL
            );
            PRAGMA user_version=1;
        """)

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def cached(self, cache_key: str, ttl: int) -> DecisionResult | None:
        if ttl == 0:
            return None
        row = self.connection.execute(
            "SELECT result FROM decisions WHERE cache_key=? AND created>=? ORDER BY created DESC LIMIT 1",
            (cache_key, self.clock() - ttl),
        ).fetchone()
        return DecisionResult.model_validate_json(row[0]) if row else None

    def get(self, decision_id: str) -> DecisionResult | None:
        row = self.connection.execute(
            "SELECT result FROM decisions WHERE id=?", (decision_id,)
        ).fetchone()
        return DecisionResult.model_validate_json(row[0]) if row else None

    def claim(self, cache_key: str, lease: float) -> str | None:
        now = self.clock()
        owner = uuid.uuid4().hex
        with self.transaction():
            self.connection.execute("DELETE FROM claims WHERE cache_key=? AND expires<=?", (cache_key, now))
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO claims VALUES (?,?,?)", (cache_key, owner, now + lease)
            )
        return owner if cursor.rowcount else None

    def release(self, cache_key: str, owner: str) -> None:
        self.connection.execute("DELETE FROM claims WHERE cache_key=? AND owner=?", (cache_key, owner))

    def complete(self, cache_key: str, owner: str, result: DecisionResult) -> None:
        now = self.clock()
        with self.transaction():
            owned = self.connection.execute(
                "SELECT 1 FROM claims WHERE cache_key=? AND owner=? AND expires>?",
                (cache_key, owner, now),
            ).fetchone()
            if not owned:
                raise OmniaError("claim_expired", "Decision ownership expired; retry the request.", True)
            self.connection.execute(
                "INSERT INTO decisions VALUES (?,?,?,?)",
                (result.decision_id, cache_key, now, result.model_dump_json()),
            )
            self.connection.execute("DELETE FROM claims WHERE cache_key=? AND owner=?", (cache_key, owner))

    def reserve_usage(self, per_minute: int, per_day: int) -> None:
        now = self.clock()
        buckets = [(f"minute:{int(now // 60)}", per_minute, (int(now // 60) + 1) * 60),
                   (f"day:{int(now // 86400)}", per_day, (int(now // 86400) + 1) * 86400)]
        with self.transaction():
            for key, limit, expiry in buckets:
                row = self.connection.execute("SELECT count FROM usage WHERE bucket=?", (key,)).fetchone()
                if row and row[0] >= limit:
                    raise OmniaError("rate_limited", "Evaluation allowance exhausted for this period.", True)
                self.connection.execute(
                    "INSERT INTO usage VALUES (?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1",
                    (key, expiry),
                )

    def failure(self, input_hash: str, code: str) -> None:
        self.connection.execute(
            "INSERT INTO failures VALUES (?,?,?,?)",
            (uuid.uuid4().hex, input_hash, code, self.clock()),
        )

    def prune(self, retention_days: int) -> None:
        now = self.clock()
        with self.transaction():
            self.connection.execute("DELETE FROM decisions WHERE created<?", (now - retention_days * 86400,))
            self.connection.execute("DELETE FROM failures WHERE created<?", (now - retention_days * 86400,))
            self.connection.execute("DELETE FROM claims WHERE expires<=?", (now,))
            self.connection.execute("DELETE FROM usage WHERE expires<=?", (now,))

    def close(self) -> None:
        self.connection.close()


def read_decision(path: Path, decision_id: str) -> DecisionResult | None:
    """Read an existing ledger without creating directories, schema or WAL settings."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        return None
    with sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=1.5) as connection:
        row = connection.execute("SELECT result FROM decisions WHERE id=?", (decision_id,)).fetchone()
    return DecisionResult.model_validate_json(row[0]) if row else None

