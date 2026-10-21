"""Transactional SQLite persistence, with all disk work off the event loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .settings import PROFILE_DEFAULTS


class Store:
    """Serialize transactions and give every operation its own connection."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()

    async def call(self, operation: str, *args):
        """Run one atomic operation in a worker thread.

        Args:
            operation: Name of an internal database operation.
            *args: Arguments to that operation.

        Returns:
            The result produced by the operation.
        """
        async with self.lock:
            # Shield the worker so cancellation cannot release the lock mid-write.
            task = asyncio.create_task(
                asyncio.to_thread(self._transaction, operation, args)
            )
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    def _transaction(self, operation: str, args: tuple):
        """Open, commit or roll back, and close one database transaction.

        Args:
            operation: Internal method name.
            args: Operation arguments.

        Returns:
            The database operation result.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                return getattr(self, operation)(connection, *args)
        finally:
            connection.close()

    def initialize(self, db):
        """Create schema and mark interrupted runs as retryable failures.

        Args:
            db: Open database connection.
        """
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scopes (
                id TEXT PRIMARY KEY, umo TEXT NOT NULL, persona TEXT NOT NULL,
                label TEXT NOT NULL, profile TEXT NOT NULL, updated REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, source_id TEXT NOT NULL,
                sender TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0, UNIQUE(scope, source_id)
            );
            CREATE INDEX IF NOT EXISTS messages_pending ON messages(scope, consumed, id);
            CREATE TABLE IF NOT EXISTS traits (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL,
                situation TEXT NOT NULL, content TEXT NOT NULL, confidence REAL NOT NULL,
                evidence TEXT NOT NULL, status TEXT NOT NULL, fingerprint TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, UNIQUE(scope, fingerprint)
            );
            CREATE INDEX IF NOT EXISTS traits_scope ON traits(scope, status);
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, status TEXT NOT NULL,
                started REAL NOT NULL, finished REAL, count INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '', memory TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS revisions (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, action TEXT NOT NULL,
                snapshot TEXT NOT NULL, created REAL NOT NULL
            );
        """)
        db.execute(
            "UPDATE runs SET status='error', finished=?, message=? WHERE status='running'",
            (time.time(), "插件重启中断了学习，素材已保留，可重新学习。"),
        )

    def settings(self, db, patch=None):
        """Read or patch runtime settings.

        Args:
            db: Open database connection.
            patch: Validated settings patch, or None for a read.

        Returns:
            Persisted settings overrides.
        """
        row = db.execute("SELECT value FROM settings WHERE id=1").fetchone()
        settings = json.loads(row[0]) if row else {}
        if patch is not None:
            settings.update(patch)
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES (1, ?)", (json.dumps(settings),)
            )
        return settings

    def prune(self, db, retention_days):
        """Expire raw observations even when no new messages arrive.

        Args:
            db: Open database connection.
            retention_days: Raw observation retention period.
        """
        db.execute(
            "DELETE FROM messages WHERE created < ?",
            (time.time() - retention_days * 86400,),
        )

    def capture(
        self, db, scope, umo, persona, label, source_id, sender, text, settings
    ):
        """Retain a bounded, deduplicated observation for a session and persona.

        Args:
            db: Open database connection.
            scope: Stable session/persona key.
            umo: AstrBot unified message origin.
            persona: AstrBot persona name or sentinel.
            label: Human-readable session label.
            source_id: Platform message identity.
            sender: Platform sender identity.
            text: Truncated plain text observation.
            settings: Effective runtime settings.

        Returns:
            Whether a new observation was inserted.
        """
        now = time.time()
        db.execute(
            "INSERT INTO scopes VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET label=excluded.label, updated=excluded.updated",
            (scope, umo, persona, label, json.dumps(PROFILE_DEFAULTS), now),
        )
        profile = json.loads(
            db.execute("SELECT profile FROM scopes WHERE id=?", (scope,)).fetchone()[0]
        )
        if not profile["learning_enabled"]:
            return False
        cursor = db.execute(
            "INSERT OR IGNORE INTO messages(scope, source_id, sender, text, created) VALUES (?, ?, ?, ?, ?)",
            (scope, source_id, sender, text, now),
        )
        db.execute(
            "DELETE FROM messages WHERE created < ?",
            (now - settings["retention_days"] * 86400,),
        )
        db.execute(
            "DELETE FROM messages WHERE scope=? AND id NOT IN (SELECT id FROM messages WHERE scope=? ORDER BY id DESC LIMIT ?)",
            (scope, scope, settings["max_messages_per_scope"]),
        )
        return cursor.rowcount == 1

    def state(self, db, scope=None):
        """Load the bounded dashboard state or one scope's detail.

        Args:
            db: Open database connection.
            scope: Optional selected scope.

        Returns:
            A serializable dashboard state.
        """
        scopes = [
            dict(row)
            for row in db.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.scope=s.id AND m.consumed=0) pending, (SELECT COUNT(*) FROM traits t WHERE t.scope=s.id AND t.status='pending') review_count FROM scopes s ORDER BY updated DESC"
            )
        ]
        for item in scopes:
            item["profile"] = json.loads(item["profile"])
        result = {
            "scopes": scopes,
            "scope": None,
            "traits": [],
            "runs": [],
            "revisions": [],
        }
        selected = next((s for s in scopes if s["id"] == scope), None)
        if selected:
            result["scope"] = selected
            result["traits"] = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM traits WHERE scope=? ORDER BY updated DESC LIMIT 1000",
                    (scope,),
                )
            ]
            for trait in result["traits"]:
                trait["evidence"] = json.loads(trait["evidence"])
            result["runs"] = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM runs WHERE scope=? ORDER BY id DESC LIMIT 30",
                    (scope,),
                )
            ]
            result["revisions"] = [
                dict(row)
                for row in db.execute(
                    "SELECT id, action, created FROM revisions WHERE scope=? ORDER BY id DESC LIMIT 30",
                    (scope,),
                )
            ]
        return result

    def batch(self, db, scope, size):
        """Read the oldest unconsumed messages for a learning batch.

        Args:
            db: Open database connection.
            scope: Session/persona key.
            size: Batch size limit.

        Returns:
            Retained observations in arrival order.
        """
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM messages WHERE scope=? AND consumed=0 ORDER BY id LIMIT ?",
                (scope, size),
            )
        ]

    def start_run(self, db, scope):
        """Create a running batch record.

        Args:
            db: Open database connection.
            scope: Session/persona key.

        Returns:
            New run identifier.
        """
        return db.execute(
            "INSERT INTO runs(scope, status, started) VALUES (?, 'running', ?)",
            (scope, time.time()),
        ).lastrowid

    def finish_run(
        self,
        db,
        run_id,
        scope,
        messages,
        traits,
        settings,
        memory,
        error="",
        expected_profile=None,
    ):
        """Commit candidates and consume exactly the successful batch atomically.

        Args:
            db: Open database connection.
            run_id: Run being completed.
            scope: Session/persona key.
            messages: Exact batch snapshot, never newly arrived messages.
            traits: Validated candidates.
            settings: Activation settings at commit time.
            memory: Integration status description.
            error: Actionable failure description, if any.
            expected_profile: Identity anchor at the start of generation.

        Returns:
            Count of newly inserted candidates.
        """
        now = time.time()
        added = 0
        if not error:
            settings = settings | self.settings(db)
            profile = json.loads(
                db.execute(
                    "SELECT profile FROM scopes WHERE id=?", (scope,)
                ).fetchone()[0]
            )
            if (
                not settings["enabled"]
                or not settings["learning_enabled"]
                or not profile["learning_enabled"]
            ):
                raise ValueError("学习过程中已暂停，结果未保存，素材保留。")
            if expected_profile is not None and any(
                profile[k] != expected_profile[k]
                for k in ("identity", "reply_style", "behavior")
            ):
                raise ValueError("人格底稿在学习期间已修改，旧结果未保存，请重新学习。")
            traits = [
                t
                for t in traits
                if settings[f"learn_{t['kind']}"]
                and len(t["evidence"]) >= settings["min_evidence"]
            ]
            for trait in traits:
                fingerprint = hashlib.sha256(
                    json.dumps(
                        [
                            trait["kind"],
                            trait["situation"].casefold(),
                            trait["content"].casefold(),
                        ],
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest()
                status = (
                    "active"
                    if settings["activation_mode"] == "auto"
                    and trait["confidence"] >= settings["auto_confidence"]
                    and len(trait["evidence"]) >= settings["min_evidence"]
                    else "pending"
                )
                cursor = db.execute(
                    "INSERT OR IGNORE INTO traits(scope, kind, situation, content, confidence, evidence, status, fingerprint, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        scope,
                        trait["kind"],
                        trait["situation"],
                        trait["content"],
                        trait["confidence"],
                        json.dumps(trait["evidence"], ensure_ascii=False),
                        status,
                        fingerprint,
                        now,
                        now,
                    ),
                )
                added += cursor.rowcount
            db.executemany(
                "UPDATE messages SET consumed=1 WHERE scope=? AND id=?",
                [(scope, m["id"]) for m in messages],
            )
        db.execute(
            "UPDATE runs SET status=?, finished=?, count=?, message=?, memory=? WHERE id=?",
            (
                "error" if error else "success",
                now,
                added,
                error or f"分析 {len(messages)} 条素材，新增 {added} 条学习结果。",
                memory,
                run_id,
            ),
        )
        db.execute(
            "DELETE FROM runs WHERE scope=? AND id NOT IN (SELECT id FROM runs WHERE scope=? ORDER BY id DESC LIMIT 100)",
            (scope, scope),
        )
        return added

    def mutate(self, db, scope, action, payload):
        """Version and mutate a profile or learned entries in one transaction.

        Args:
            db: Open database connection.
            scope: Target session/persona key.
            action: Profile, trait, restore or clear action.
            payload: Validated operation payload.

        Returns:
            True when the operation succeeds.

        Raises:
            ValueError: If the scope, entry or revision does not exist.
        """
        row = db.execute("SELECT profile FROM scopes WHERE id=?", (scope,)).fetchone()
        if not row:
            raise ValueError("会话不存在，请刷新后重新选择。")
        snapshot = {
            "profile": json.loads(row[0]),
            "traits": [
                dict(t)
                for t in db.execute("SELECT * FROM traits WHERE scope=?", (scope,))
            ],
        }
        db.execute(
            "INSERT INTO revisions(scope, action, snapshot, created) VALUES (?, ?, ?, ?)",
            (scope, action, json.dumps(snapshot, ensure_ascii=False), time.time()),
        )
        if action == "profile":
            profile = snapshot["profile"] | payload
            db.execute(
                "UPDATE scopes SET profile=?, updated=? WHERE id=?",
                (json.dumps(profile, ensure_ascii=False), time.time(), scope),
            )
        elif action == "trait":
            row = db.execute(
                "SELECT id FROM traits WHERE scope=? AND id=?", (scope, payload["id"])
            ).fetchone()
            if not row:
                raise ValueError("学习条目不存在或不属于当前会话。")
            updates = {
                k: v
                for k, v in payload.items()
                if k in ("content", "situation", "status")
            }
            for key, value in updates.items():
                db.execute(
                    f"UPDATE traits SET {key}=?, updated=? WHERE scope=? AND id=?",
                    (value, time.time(), scope, payload["id"]),
                )
        elif action == "restore":
            revision = db.execute(
                "SELECT snapshot FROM revisions WHERE scope=? AND id=?",
                (scope, payload["id"]),
            ).fetchone()
            if not revision:
                raise ValueError("版本不存在或不属于当前会话。")
            data = json.loads(revision[0])
            db.execute(
                "UPDATE scopes SET profile=? WHERE id=?",
                (json.dumps(data["profile"], ensure_ascii=False), scope),
            )
            # Keep later entries as disabled tombstones so relearning cannot undo rollback.
            db.execute("UPDATE traits SET status='disabled' WHERE scope=?", (scope,))
            for trait in data["traits"]:
                db.execute(
                    "UPDATE traits SET situation=?, content=?, confidence=?, evidence=?, status=?, updated=? WHERE scope=? AND id=?",
                    (
                        trait["situation"],
                        trait["content"],
                        trait["confidence"],
                        trait["evidence"],
                        trait["status"],
                        time.time(),
                        scope,
                        trait["id"],
                    ),
                )
        elif action == "clear_messages":
            db.execute("DELETE FROM messages WHERE scope=?", (scope,))
        else:
            raise ValueError("不支持的操作。")
        db.execute(
            "DELETE FROM revisions WHERE scope=? AND id NOT IN (SELECT id FROM revisions WHERE scope=? ORDER BY id DESC LIMIT 30)",
            (scope, scope),
        )
        return True
