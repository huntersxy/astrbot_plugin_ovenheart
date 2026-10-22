"""Transactional SQLite persistence, with all disk work off the event loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .learning import jargon_term
from .settings import KINDS, PROFILE_DEFAULTS, SHARED_SCOPE, validate_settings


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

    def initialize(self, db, legacy_path=None):
        """Create schema and mark interrupted runs as retryable failures.

        Args:
            db: Open database connection.
            legacy_path: Old plugin database, copied only into an empty database.
        """
        if (
            legacy_path is not None
            and legacy_path.is_file()
            and not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
            ).fetchone()
        ):
            source = sqlite3.connect(
                legacy_path.as_uri() + "?mode=ro", uri=True, timeout=10
            )
            try:
                source.backup(db)
            finally:
                source.close()
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS config_sync (
                id INTEGER PRIMARY KEY CHECK(id=1), legacy_settings TEXT NOT NULL
            );
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
            CREATE TABLE IF NOT EXISTS trait_aliases (
                scope TEXT NOT NULL, fingerprint TEXT NOT NULL, trait_id INTEGER NOT NULL,
                PRIMARY KEY(scope, fingerprint)
            );
            CREATE TABLE IF NOT EXISTS embeddings (
                trait_id INTEGER NOT NULL, provider TEXT NOT NULL, digest TEXT NOT NULL,
                vector TEXT NOT NULL, PRIMARY KEY(trait_id, provider)
            );
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, event_key TEXT NOT NULL UNIQUE,
                trait_ids TEXT NOT NULL, mode TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY, scope TEXT NOT NULL, action TEXT NOT NULL,
                confidence REAL NOT NULL, reason TEXT NOT NULL, intent TEXT NOT NULL,
                mode TEXT NOT NULL, outcome TEXT NOT NULL, created REAL NOT NULL,
                room TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS decisions_scope ON decisions(scope,created);
            CREATE TABLE IF NOT EXISTS participation_state (
                scope TEXT PRIMARY KEY, last_reply REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS prompts (
                key TEXT PRIMARY KEY, body TEXT NOT NULL, updated REAL NOT NULL
            );
        """)
        db.execute(
            "INSERT OR IGNORE INTO scopes VALUES (?, '', '所有人格', '全会话共用', ?, ?)",
            (SHARED_SCOPE, json.dumps(PROFILE_DEFAULTS), time.time()),
        )
        if "room" not in {r[1] for r in db.execute("PRAGMA table_info(decisions)")}:
            db.execute("ALTER TABLE decisions ADD COLUMN room TEXT NOT NULL DEFAULT ''")
        db.execute(
            "CREATE INDEX IF NOT EXISTS decisions_room_created ON decisions(room,created)"
        )
        columns = {r[1] for r in db.execute("PRAGMA table_info(traits)")}
        if "review" not in {r[1] for r in db.execute("PRAGMA table_info(runs)")}:
            db.execute("ALTER TABLE runs ADD COLUMN review TEXT NOT NULL DEFAULT '{}'")
        for column, definition in {
            "support_count": "INTEGER NOT NULL DEFAULT 1",
            "evidence_count": "INTEGER NOT NULL DEFAULT 0",
            "last_seen": "REAL NOT NULL DEFAULT 0",
            "use_count": "INTEGER NOT NULL DEFAULT 0",
            "last_used": "REAL NOT NULL DEFAULT 0",
            "rating": "INTEGER NOT NULL DEFAULT 0",
            "manual": "INTEGER NOT NULL DEFAULT 0",
            "meaning_stage": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if column not in columns:
                db.execute(f"ALTER TABLE traits ADD COLUMN {column} {definition}")
        for row in db.execute(
            "SELECT id, evidence, created FROM traits WHERE evidence_count=0"
        ):
            db.execute(
                "UPDATE traits SET evidence_count=?, last_seen=? WHERE id=?",
                (len(json.loads(row["evidence"])), row["created"], row["id"]),
            )
        db.execute(
            "INSERT OR IGNORE INTO trait_aliases SELECT scope, fingerprint, id FROM traits"
        )
        if "manual" not in columns:
            # Previous releases did not distinguish operator edits from model output.
            db.execute("UPDATE traits SET manual=1")
        # Keep ambiguous legacy definitions in Library without guessing their origin.
        # A complete, single-source record can move intact, including its ID and counters.
        migrated_scopes = set()
        for row in db.execute(
            "SELECT * FROM traits WHERE scope=? AND kind='jargon'", (SHARED_SCOPE,)
        ).fetchall():
            evidence = json.loads(row["evidence"])
            origins = {e.get("scope") for e in evidence}
            if (
                not evidence
                or row["evidence_count"] > len(evidence)
                or len(origins) != 1
            ):
                continue
            origin = next(iter(origins))
            source = db.execute(
                "SELECT * FROM scopes WHERE id=? AND id<>?", (origin, SHARED_SCOPE)
            ).fetchone()
            aliases = db.execute(
                "SELECT fingerprint FROM trait_aliases WHERE trait_id=?", (row["id"],)
            ).fetchall()
            if (
                source is None
                or db.execute(
                    "SELECT 1 FROM traits WHERE scope=? AND fingerprint=?",
                    (origin, row["fingerprint"]),
                ).fetchone()
                or any(
                    db.execute(
                        "SELECT 1 FROM trait_aliases WHERE scope=? AND fingerprint=?",
                        (origin, alias[0]),
                    ).fetchone()
                    for alias in aliases
                )
            ):
                continue
            if origin not in migrated_scopes:
                snapshot = {
                    "profile": json.loads(source["profile"]),
                    "traits": [
                        dict(t)
                        for t in db.execute(
                            "SELECT * FROM traits WHERE scope=?", (origin,)
                        )
                    ],
                }
                db.execute(
                    "INSERT INTO revisions(scope, action, snapshot, created) VALUES (?, 'jargon_isolation', ?, ?)",
                    (origin, json.dumps(snapshot, ensure_ascii=False), time.time()),
                )
                db.execute(
                    "DELETE FROM revisions WHERE scope=? AND id NOT IN (SELECT id FROM revisions WHERE scope=? ORDER BY id DESC LIMIT 30)",
                    (origin, origin),
                )
                migrated_scopes.add(origin)
            db.execute("UPDATE traits SET scope=? WHERE id=?", (origin, row["id"]))
            db.execute(
                "UPDATE trait_aliases SET scope=? WHERE trait_id=?",
                (origin, row["id"]),
            )
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

    def sync_config(self, db, config, values, bootstrap=False, patch=None):
        """Persist native configuration and its transaction-guard mirror together.

        Args:
            db: Open database connection.
            config: Host configuration object, or a dictionary in isolated fixtures.
            values: Complete validated host settings.
            bootstrap: Migrate legacy Panel overrides once, then trust the host.
            patch: Settings changed during this instance, for stale-batch guards.

        Returns:
            Complete effective settings after the optional one-time migration.

        Raises:
            ValueError: If legacy settings are invalid or internally inconsistent.
            OSError: If the native configuration cannot be persisted.
        """
        if (
            bootstrap
            and not db.execute("SELECT 1 FROM config_sync WHERE id=1").fetchone()
        ):
            legacy = self.settings(db)
            values = values | validate_settings(legacy)
            db.execute(
                "INSERT INTO config_sync VALUES (1, ?)",
                (json.dumps(legacy, ensure_ascii=False),),
            )
        if values["min_messages"] > values["batch_size"]:
            raise ValueError("自动学习的素材门槛不能大于每批素材数量。")
        if values["min_evidence"] > values["batch_size"]:
            raise ValueError("最少证据数量不能大于每批素材数量。")
        # Only changes made during this instance can supersede an in-flight batch.
        # A reload starts with the host values, never with old database overrides.
        guards = (
            {}
            if bootstrap
            else self.settings(db) | (values if patch is None else patch)
        )
        db.execute(
            "INSERT OR REPLACE INTO settings VALUES (1, ?)",
            (json.dumps(guards, ensure_ascii=False),),
        )
        previous = dict(config)
        try:
            snapshot = json.loads(json.dumps(values))
            if callable(getattr(config, "save_config", None)):
                config.save_config(snapshot)
            else:
                config.update(snapshot)
        except Exception:
            # AstrBot updates its dictionary before attempting its atomic file write.
            config.clear()
            config.update(previous)
            raise
        return values

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
        self,
        db,
        scope,
        umo,
        persona,
        label,
        source_id,
        sender,
        text,
        settings,
        context_only=False,
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
            context_only: Check scoped learning permission without retaining raw text.

        Returns:
            Whether an observation was inserted or context-only capture is permitted.
        """
        now = time.time()
        settings = settings | self.settings(db)
        if not settings["enabled"] or not settings["learning_enabled"]:
            return False
        if (
            not context_only
            and settings["target_senders"]
            and sender not in settings["target_senders"]
        ):
            return False
        if sender in settings["blocked_senders"]:
            return False
        profile_scope = scope if settings["session_isolation"] else SHARED_SCOPE
        if context_only:
            row = db.execute(
                "SELECT profile FROM scopes WHERE id=?", (profile_scope,)
            ).fetchone()
            return not row or json.loads(row[0])["learning_enabled"]
        db.execute(
            "INSERT INTO scopes VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET label=excluded.label, updated=excluded.updated",
            (scope, umo, persona, label, json.dumps(PROFILE_DEFAULTS), now),
        )
        profile = json.loads(
            db.execute(
                "SELECT profile FROM scopes WHERE id=?", (profile_scope,)
            ).fetchone()[0]
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

    def state(
        self, db, scope=None, target_senders=None, blocked_senders=None, shared=False
    ):
        """Load the bounded dashboard state or one scope's detail.

        Args:
            db: Open database connection.
            scope: Optional selected scope.
            target_senders: Current learning targets for pending-material counts.
            blocked_senders: Excluded users, overriding learning targets.
            shared: Read the shared profile and learning space for this source.

        Returns:
            A serializable dashboard state.
        """
        targets = list(dict.fromkeys(target_senders or []))
        sender_filter = (
            " AND m.sender IN (" + ",".join("?" for _ in targets) + ")"
            if targets
            else ""
        )
        blocked = list(
            dict.fromkeys(
                [
                    *(blocked_senders or []),
                    *self.settings(db).get("blocked_senders", []),
                ]
            )
        )
        if blocked:
            sender_filter += (
                " AND m.sender NOT IN (" + ",".join("?" for _ in blocked) + ")"
            )
        scopes = [
            dict(row)
            for row in db.execute(
                f"SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.scope=s.id AND m.consumed=0{sender_filter}) pending, (SELECT COUNT(*) FROM traits t WHERE t.scope=s.id AND t.status='pending') review_count FROM scopes s ORDER BY updated DESC",
                (*targets, *blocked),
            )
        ]
        for item in scopes:
            item["profile"] = json.loads(item["profile"])
        common = next(item for item in scopes if item["id"] == SHARED_SCOPE)
        all_scopes = scopes
        scopes = [item for item in scopes if item["id"] != SHARED_SCOPE]
        if shared:
            scopes = [item | {"profile": common["profile"]} for item in scopes]
        result = {
            "scopes": scopes,
            "shared_scope": common,
            "effect_scope": SHARED_SCOPE if shared else scope,
            "active_count": db.execute(
                "SELECT COUNT(*) FROM traits WHERE status='active' AND ((kind='jargon' AND scope=? AND scope<>?) OR (kind<>'jargon' AND (scope=? OR ?)))",
                (scope, SHARED_SCOPE, scope, shared),
            ).fetchone()[0],
            "review_count": sum(item["review_count"] for item in all_scopes),
            "scope": None,
            "traits": [],
            "runs": [],
            "revisions": [],
            "applications": [],
            "decisions": [],
        }
        selected = next((s for s in scopes if s["id"] == scope), None)
        if scope == SHARED_SCOPE or (shared and not scopes):
            selected = common
        trait_scope = SHARED_SCOPE if shared else scope
        if shared and selected:
            selected = selected | {"profile": common["profile"]}
        if selected:
            result["scope"] = selected
            result["traits"] = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM traits WHERE "
                    + (
                        "((scope=? AND kind<>'jargon') OR (scope=? AND scope<>? AND kind='jargon'))"
                        if shared
                        else "scope=?"
                    )
                    + " ORDER BY updated DESC LIMIT 1000",
                    (trait_scope, scope, SHARED_SCOPE) if shared else (trait_scope,),
                )
            ]
            for trait in result["traits"]:
                trait["evidence"] = json.loads(trait["evidence"])
            result["runs"] = [
                dict(row) | {"review": json.loads(row["review"])}
                for row in db.execute(
                    "SELECT * FROM runs WHERE scope=? ORDER BY id DESC LIMIT 30",
                    (scope,),
                )
            ]
            result["revisions"] = [
                dict(row)
                for row in db.execute(
                    "SELECT id, scope, action, created FROM revisions WHERE scope IN (?, ?) ORDER BY id DESC LIMIT 30",
                    (trait_scope, scope),
                )
            ]
            result["applications"] = [
                dict(row) | {"trait_ids": json.loads(row["trait_ids"])}
                for row in db.execute(
                    "SELECT * FROM applications WHERE scope=? ORDER BY id DESC LIMIT 30",
                    (scope,),
                )
            ]
            result["decisions"] = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM decisions WHERE scope=? ORDER BY id DESC LIMIT 30",
                    (scope,),
                )
            ]
        return result

    def library(self, db, scope="all", kind="all", status="pending", offset=0):
        """Read a filtered page across conversations without changing their ownership.

        Args:
            db: Open database connection.
            scope: One conversation key, or all conversations.
            kind: Learning category filter.
            status: Review status filter.
            offset: Zero-based page offset.

        Returns:
            Entries with owner labels, scoped review audits and pagination metadata.

        Raises:
            ValueError: If any filter or page is invalid.
        """
        if (
            not isinstance(scope, str)
            or (scope != "all" and len(scope) != 24)
            or kind not in ("all", "personality", "expression", "jargon", "behavior")
            or status not in ("all", "pending", "active", "rejected", "disabled")
            or type(offset) is not int
            or offset < 0
        ):
            raise ValueError("学习库筛选无效，请刷新后重试。")
        filters, values = [], []
        for column, value in (("scope", scope), ("kind", kind), ("status", status)):
            if value != "all":
                filters.append(f"t.{column}=?")
                values.append(value)
        where = " AND ".join(filters) or "1=1"
        total = db.execute(
            f"SELECT COUNT(*) FROM traits t JOIN scopes s ON s.id=t.scope WHERE {where}",
            values,
        ).fetchone()[0]
        offset = min(offset // 100, max(0, (total - 1) // 100)) * 100
        traits = [
            dict(row) | {"evidence": json.loads(row["evidence"])}
            for row in db.execute(
                f"SELECT t.*, s.label, s.persona FROM traits t JOIN scopes s ON s.id=t.scope WHERE {where} ORDER BY t.updated DESC, t.id DESC LIMIT 100 OFFSET ?",
                (*values, offset),
            )
        ]
        for owner in dict.fromkeys(t["scope"] for t in traits):
            candidates = [
                candidate
                for row in db.execute(
                    "SELECT review FROM runs WHERE scope=? OR ?=? ORDER BY id DESC LIMIT 30",
                    (owner, owner, SHARED_SCOPE),
                )
                for candidate in json.loads(row["review"]).get("candidates", [])
            ]
            for trait in (t for t in traits if t["scope"] == owner):
                trait["review"] = next(
                    (
                        candidate
                        for candidate in candidates
                        if all(
                            candidate.get(key) == trait[key]
                            for key in ("kind", "situation", "content")
                        )
                    ),
                    None,
                )
        return {"traits": traits, "total": total, "offset": offset}

    def touch_scope(self, db, scope, umo, persona, label):
        """Create a scope even when raw-message learning is disabled.

        Args:
            db: Open database connection.
            scope: Session/persona key.
            umo: Native message origin.
            persona: Effective host persona identifier.
            label: Conversation display name.
        """
        db.execute(
            "INSERT INTO scopes VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET label=excluded.label, updated=excluded.updated",
            (scope, umo, persona, label, json.dumps(PROFILE_DEFAULTS), time.time()),
        )

    def participation_gate(self, db, scope, settings, room="", followup=False):
        """Check persistent cooldown and rolling autonomous admission limits.

        Args:
            db: Open database connection.
            scope: Session/persona key.
            settings: Effective timing and quota settings.
            room: Physical group key, shared across isolated native sessions.
            followup: Controller-validated same-member continuation. Only bypasses
                cooldown when the operator also enables this feature.

        Returns:
            An empty string when another autonomous reply may be admitted.
        """
        now = time.time()
        row = db.execute(
            "SELECT MAX(last_reply) FROM participation_state WHERE scope=? OR scope=?",
            (scope, room),
        ).fetchone()
        if (
            row
            and row[0] is not None
            and now - row[0] < settings["participation_cooldown"]
            and not (followup and settings["participation_followup"])
        ):
            return "距离上次回复或参与尝试过近，继续旁观。"
        count = db.execute(
            "SELECT COUNT(*) FROM decisions WHERE (scope=? OR (room<>'' AND room=?)) AND outcome='admitted' AND created>?",
            (scope, room, now - 3600),
        ).fetchone()[0]
        if count >= settings["participation_hourly_limit"]:
            return "已达到本小时自主参与上限，继续旁观。"
        return ""

    def decide(self, db, scope, plan, mode, outcome, settings):
        """Log a decision and reserve automatic participation atomically.

        Args:
            db: Open database connection.
            scope: Session/persona key.
            plan: Validated decision and human-readable reason.
            mode: Observe or auto mode at commit time.
            outcome: Outcome such as admitted, wait, observed or error.
            settings: Effective settings for final quota and pause checks.

        Returns:
            Decision identifier and the actual outcome.
        """
        if outcome == "admitted":
            settings = settings | self.settings(db)
            profile = db.execute(
                "SELECT profile FROM scopes WHERE id=?", (scope,)
            ).fetchone()
            reason = self.participation_gate(
                db, scope, settings, plan.get("room", ""), plan.get("followup", False)
            )
            if plan.get("followup") and not settings["participation_followup"]:
                reason = "连续接话已关闭，取消排队中的回复。"
            if (
                not settings["enabled"]
                or settings["participation_mode"] != "auto"
                or not settings["apply_enabled"]
                or not profile
                or not json.loads(profile[0])["apply_enabled"]
            ):
                reason = "自主参与已关闭或当前会话暂停应用，取消回复。"
            if reason:
                outcome = "cancelled"
                plan = plan | {"reason": reason}
            else:
                self.note_reply(db, scope, plan.get("room", ""))
        cursor = db.execute(
            "INSERT INTO decisions(scope,action,confidence,reason,intent,mode,outcome,created,room) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                scope,
                plan["action"],
                plan["confidence"],
                plan["reason"],
                plan.get("intent", ""),
                mode,
                outcome,
                time.time(),
                plan.get("room", ""),
            ),
        )
        # Keep every admission inside the rolling hour even if many decisions arrive.
        db.execute(
            "DELETE FROM decisions WHERE scope=? AND id NOT IN (SELECT id FROM decisions WHERE scope=? ORDER BY id DESC LIMIT 100) AND (outcome<>'admitted' OR created<?)",
            (scope, scope, time.time() - 3600),
        )
        return {"id": cursor.lastrowid, "outcome": outcome}

    def cancel_decision(self, db, scope, identifier, reason):
        """Mark an admission cancelled if its native request became stale in queue.

        Args:
            db: Open database connection.
            scope: Session/persona key.
            identifier: Original admitted decision identifier.
            reason: Actionable cancellation reason without provider internals.
        """
        db.execute(
            "UPDATE decisions SET outcome='cancelled',reason=? WHERE scope=? AND id=? AND outcome='admitted'",
            (reason, scope, identifier),
        )

    def note_reply(self, db, scope, room=""):
        """Update autonomous cooldown after any native host send-stage reply.

        Args:
            db: Open database connection.
            scope: Session/persona key.
            room: Optional physical-group cooldown shared across isolated sessions.
        """
        for key in {scope, room} - {""}:
            db.execute(
                "INSERT INTO participation_state VALUES (?, ?) ON CONFLICT(scope) DO UPDATE SET last_reply=excluded.last_reply",
                (key, time.time()),
            )

    def prompt_overrides(self, db, patch=None):
        """Read or replace operator-edited prompt templates.

        Args:
            db: Open database connection.
            patch: Template bodies keyed by identifier; a None body restores the
                shipped default by deleting the override.

        Returns:
            Persisted overrides keyed by template identifier.
        """
        if patch is not None:
            for key, body in patch.items():
                if body is None:
                    db.execute("DELETE FROM prompts WHERE key=?", (key,))
                else:
                    db.execute(
                        "INSERT OR REPLACE INTO prompts VALUES (?, ?, ?)",
                        (key, body, time.time()),
                    )
        return {
            row["key"]: row["body"]
            for row in db.execute("SELECT key, body FROM prompts")
        }

    def batch(self, db, scope, size, target_senders=None, blocked_senders=None):
        """Read the oldest unconsumed messages for a learning batch.

        Args:
            db: Open database connection.
            scope: Session/persona key.
            size: Batch size limit.
            target_senders: Current eligible senders, including after settings changes.
            blocked_senders: Users excluded even if also selected as targets.

        Returns:
            Retained observations in arrival order.
        """
        targets = list(dict.fromkeys(target_senders or []))
        sender_filter = (
            " AND sender IN (" + ",".join("?" for _ in targets) + ")" if targets else ""
        )
        blocked = list(
            dict.fromkeys(
                [
                    *(blocked_senders or []),
                    *self.settings(db).get("blocked_senders", []),
                ]
            )
        )
        if blocked:
            sender_filter += (
                " AND sender NOT IN (" + ",".join("?" for _ in blocked) + ")"
            )
        return [
            dict(row)
            for row in db.execute(
                f"SELECT * FROM messages WHERE scope=? AND consumed=0{sender_filter} ORDER BY id LIMIT ?",
                (scope, *targets, *blocked, size),
            )
        ]

    def guidance(self, db, scope, shared=False):
        """Load only the fields needed on the reply path, excluding evidence bodies.

        Args:
            db: Open database connection.
            scope: Source session/persona key.
            shared: Share non-jargon habits and profile, keeping jargon source-local.

        Returns:
            Profile, compact ranking records and recent expression exposure IDs
            from this native scope, without dashboard history or reply text.
        """
        row = db.execute(
            "SELECT profile FROM scopes WHERE id=?",
            (SHARED_SCOPE if shared else scope,),
        ).fetchone()
        traits = [
            dict(t)
            for t in db.execute(
                "SELECT id,kind,situation,content,confidence,status,created,updated,support_count,evidence_count,last_seen,use_count,rating FROM traits WHERE status='active' AND ((kind='jargon' AND scope=? AND scope<>?) OR (kind<>'jargon' AND (scope=? OR ?))) ORDER BY updated DESC LIMIT 1000",
                (scope, SHARED_SCOPE, scope, shared),
            )
        ]
        expressions = {
            t["id"]: t["updated"] for t in traits if t["kind"] == "expression"
        }
        recent = list(
            dict.fromkeys(
                identifier
                for application in db.execute(
                    "SELECT trait_ids,created FROM applications WHERE scope=? AND created>? ORDER BY id DESC LIMIT 3",
                    (scope, time.time() - 600),
                )
                for identifier in json.loads(application["trait_ids"])
                if identifier in expressions
                and expressions[identifier] <= application["created"]
            )
        )
        return {
            "scope": {"profile": json.loads(row[0])} if row else None,
            "traits": traits,
            "recent_expression_ids": recent,
        }

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
        expected_targets=None,
        review=None,
        expected_review_enabled=None,
        expected_blocked=None,
        shared=False,
        expected_isolation=None,
        expected_prompts=None,
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
            expected_targets: Learning targets at the start of generation.
            review: Server-assembled semantic review audit, without provider secrets.
            expected_review_enabled: Review switch captured before generation.
            expected_blocked: Blacklist captured before generation.
            shared: Share non-jargon learning while keeping jargon and sources local.
            expected_isolation: Mode captured before generation.
            expected_prompts: Prompt overrides captured before generation.

        Returns:
            Count of newly inserted candidates.
        """
        profile_scope = SHARED_SCOPE if shared else scope
        now = time.time()
        added = 0
        reinforced = set()
        batch_support = {}
        run = db.execute(
            "SELECT status FROM runs WHERE id=? AND scope=?", (run_id, scope)
        ).fetchone()
        if not run:
            raise ValueError("学习批次不存在或不属于当前会话。")
        if run["status"] != "running":
            return 0
        if not error:
            if (
                expected_prompts is not None
                and self.prompt_overrides(db) != expected_prompts
            ):
                raise ValueError("提示词在学习期间已修改，旧结果未保存，素材保留。")
            settings = settings | self.settings(db)
            if (
                expected_isolation is not None
                and settings["session_isolation"] != expected_isolation
            ):
                raise ValueError("会话隔离模式在学习期间已修改，结果未保存，素材保留。")
            if not any(settings[f"learn_{kind}"] for kind in KINDS):
                raise ValueError("全部学习类别已关闭，结果未保存，素材保留。")
            if (
                expected_review_enabled is not None
                and settings["learning_review_enabled"] != expected_review_enabled
            ):
                raise ValueError(
                    "入库自检开关在提交前已修改，旧结果未保存、素材已保留。"
                )
            if expected_targets is not None and set(settings["target_senders"]) != set(
                expected_targets
            ):
                raise ValueError("学习对象在学习期间已修改，旧结果未保存，素材保留。")
            if (
                expected_blocked is not None
                and set(settings["blocked_senders"]) != set(expected_blocked)
            ) or any(m["sender"] in settings["blocked_senders"] for m in messages):
                raise ValueError("学习黑名单在学习期间已修改，旧结果未保存，素材保留。")
            profile = json.loads(
                db.execute(
                    "SELECT profile FROM scopes WHERE id=?", (profile_scope,)
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
            # Compare review inputs before this batch can update a shared target.
            for trait in traits:
                trait_scope = (
                    SHARED_SCOPE if shared and trait["kind"] != "jargon" else scope
                )
                expected = trait.get("_merge_target")
                if expected is not None:
                    current = db.execute(
                        "SELECT * FROM traits WHERE scope=? AND id=?",
                        (trait_scope, expected["id"]),
                    ).fetchone()
                    if current is None or any(
                        current[key] != value for key, value in expected.items()
                    ):
                        raise ValueError(
                            "合并目标在自检后已修改，旧结果未保存、素材已保留，请重新学习。"
                        )
            for trait_scope in sorted(
                {
                    SHARED_SCOPE if shared and t["kind"] != "jargon" else scope
                    for t in traits
                }
            ):
                snapshot = {
                    "profile": json.loads(
                        db.execute(
                            "SELECT profile FROM scopes WHERE id=?", (trait_scope,)
                        ).fetchone()[0]
                    ),
                    "traits": [
                        dict(t)
                        for t in db.execute(
                            "SELECT * FROM traits WHERE scope=?", (trait_scope,)
                        )
                    ],
                }
                db.execute(
                    "INSERT INTO revisions(scope, action, snapshot, created) VALUES (?, 'learning', ?, ?)",
                    (trait_scope, json.dumps(snapshot, ensure_ascii=False), now),
                )
                db.execute(
                    "DELETE FROM revisions WHERE scope=? AND id NOT IN (SELECT id FROM revisions WHERE scope=? ORDER BY id DESC LIMIT 30)",
                    (trait_scope, trait_scope),
                )
            for trait in traits:
                trait_scope = (
                    SHARED_SCOPE if shared and trait["kind"] != "jargon" else scope
                )
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
                target = db.execute(
                    "SELECT t.* FROM traits t JOIN trait_aliases a ON a.trait_id=t.id WHERE a.scope=? AND a.fingerprint=?",
                    (trait_scope, fingerprint),
                ).fetchone()
                if target is None and trait.get("merge_id") is not None:
                    target = db.execute(
                        "SELECT * FROM traits WHERE scope=? AND id=? AND kind=?",
                        (trait_scope, trait["merge_id"], trait["kind"]),
                    ).fetchone()
                    if target is None:
                        raise ValueError("合并目标已不存在，素材保留，请重新学习。")
                # Similar spelling cannot establish compatible conditions or meaning.
                # Only exact aliases or an explicitly offered model merge may consolidate.
                if target is not None:
                    if (
                        trait.get("_merge_target") is not None
                        and target["id"] != trait["_merge_target"]["id"]
                    ):
                        raise ValueError(
                            "候选的既有别名与合并目标冲突，结果未保存、素材已保留，请重新学习。"
                        )
                    # A flagged proposal cannot modify or reinforce a stored entry.
                    if trait.get("_review_approved") is False and target[
                        "status"
                    ] not in ("rejected", "disabled"):
                        continue
                    db.execute(
                        "INSERT OR IGNORE INTO trait_aliases VALUES (?, ?, ?)",
                        (trait_scope, fingerprint, target["id"]),
                    )
                    # Human decisions are authoritative, even for paraphrased candidates.
                    if target["status"] in ("rejected", "disabled"):
                        continue
                    evidence = json.loads(target["evidence"])
                    keys = {
                        (
                            e.get("scope") or target["scope"],
                            e.get("source_id") or f"{e['id']}:{e['created']}",
                        )
                        for e in evidence
                    }
                    new_evidence = []
                    for item in trait["evidence"]:
                        key = (
                            item.get("scope") or scope,
                            item.get("source_id") or f"{item['id']}:{item['created']}",
                        )
                        if key not in keys:
                            keys.add(key)
                            new_evidence.append(item)
                    if not new_evidence:
                        continue
                    # Multiple candidates from one response are one independent batch.
                    support = batch_support.setdefault(
                        target["id"],
                        {
                            "count": target["support_count"],
                            "confidence": target["confidence"],
                            "scores": [],
                            "status": target["status"],
                        },
                    )
                    scores = support["scores"]
                    scores.append(trait["confidence"])
                    count = support["count"] + 1
                    confidence = (
                        support["confidence"] * support["count"]
                        + sum(scores) / len(scores)
                    ) / count
                    stage = target["meaning_stage"]
                    situation, content, status = (
                        target["situation"],
                        target["content"],
                        support["status"],
                    )
                    # Refine jargon only as independent batches accumulate: 2, 4, 8, 16.
                    if (
                        trait["kind"] == "jargon"
                        and not target["manual"]
                        and stage < 4
                        and count >= 2 ** (stage + 1)
                    ):
                        old_term = jargon_term(content)
                        new_term = jargon_term(trait["content"])
                        if old_term and old_term == new_term:
                            stage += 1
                            situation, content = trait["situation"], trait["content"]
                            if (
                                content != target["content"]
                                or situation != target["situation"]
                            ):
                                status = "pending"
                                support["status"] = status
                    if (
                        not target["manual"]
                        and status == "pending"
                        and settings["activation_mode"] == "auto"
                        and confidence >= settings["auto_confidence"]
                    ):
                        status = "active"
                    db.execute(
                        "UPDATE traits SET evidence=?, evidence_count=evidence_count+?, support_count=?, last_seen=?, confidence=?, situation=?, content=?, status=?, meaning_stage=?, updated=? WHERE id=?",
                        (
                            json.dumps(
                                (evidence + new_evidence)[-32:], ensure_ascii=False
                            ),
                            len(new_evidence),
                            count,
                            now,
                            target["confidence"] if target["manual"] else confidence,
                            situation,
                            content,
                            status,
                            stage,
                            now,
                            target["id"],
                        ),
                    )
                    reinforced.add(target["id"])
                    continue
                status = (
                    "active"
                    if settings["activation_mode"] == "auto"
                    and trait.get("_review_approved") is not False
                    and trait["confidence"] >= settings["auto_confidence"]
                    and len(trait["evidence"]) >= settings["min_evidence"]
                    else "pending"
                )
                cursor = db.execute(
                    "INSERT OR IGNORE INTO traits(scope, kind, situation, content, confidence, evidence, status, fingerprint, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        trait_scope,
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
                if cursor.rowcount:
                    added += 1
                    batch_support[cursor.lastrowid] = {
                        "count": 0,
                        "confidence": 0,
                        "scores": [trait["confidence"]],
                        "status": "pending",
                    }
                    db.execute(
                        "UPDATE traits SET evidence_count=?, last_seen=? WHERE id=?",
                        (len(trait["evidence"]), now, cursor.lastrowid),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO trait_aliases VALUES (?, ?, ?)",
                        (trait_scope, fingerprint, cursor.lastrowid),
                    )
            db.executemany(
                "UPDATE messages SET consumed=1 WHERE scope=? AND id=?",
                [(scope, m["id"]) for m in messages],
            )
        db.execute(
            "UPDATE runs SET status=?, finished=?, count=?, message=?, memory=?, review=? WHERE id=?",
            (
                "error" if error else "success",
                now,
                added,
                error
                or f"分析 {len(messages)} 条素材，新增 {added} 条，补充证据 {len(reinforced)} 条。",
                memory,
                json.dumps(review or {}, ensure_ascii=False),
                run_id,
            ),
        )
        db.execute(
            "DELETE FROM runs WHERE scope=? AND id NOT IN (SELECT id FROM runs WHERE scope=? ORDER BY id DESC LIMIT 100)",
            (scope, scope),
        )
        return added

    def vectors(self, db, provider, scope, entries=None):
        """Read or update content-addressed vectors for one model signature.

        Args:
            db: Open database connection.
            provider: Non-secret provider/model signature.
            scope: Session/persona scope to isolate vector reads.
            entries: Optional validated vector records.

        Returns:
            Cached vectors indexed by trait identifier.
        """
        if entries is not None:
            for item in entries:
                db.execute(
                    "DELETE FROM embeddings WHERE trait_id=? AND provider<>?",
                    (item["id"], provider),
                )
                db.execute(
                    "INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?, ?)",
                    (item["id"], provider, item["digest"], json.dumps(item["vector"])),
                )
        return {
            r["trait_id"]: {"digest": r["digest"], "vector": json.loads(r["vector"])}
            for r in db.execute(
                "SELECT e.* FROM embeddings e JOIN traits t ON t.id=e.trait_id WHERE provider=? AND (t.scope=? OR ?=?)",
                (provider, scope, scope, SHARED_SCOPE),
            )
        }

    def applied(self, db, scope, event_key, ids, mode, shared=False):
        """Record delivery once, without treating exposure as positive feedback.

        Args:
            db: Open database connection.
            scope: Session/persona key.
            event_key: Unique delivery identity shared by repeated hooks.
            ids: Habits that fit the actual prompt budget.
            mode: Retrieval mode used for selection.
            shared: Whether the actual injected habits came from the common pool.

        Returns:
            Whether a new delivery was recorded.
        """
        now = time.time()
        valid = [
            r[0]
            for r in db.execute(
                "SELECT id FROM traits WHERE status='active' AND ((kind='jargon' AND scope=? AND scope<>?) OR (kind<>'jargon' AND (scope=? OR ?)))",
                (scope, SHARED_SCOPE, scope, shared),
            )
            if r[0] in ids
        ]
        if not valid:
            return False
        cursor = db.execute(
            "INSERT OR IGNORE INTO applications(scope, event_key, trait_ids, mode, created) VALUES (?, ?, ?, ?, ?)",
            (scope, event_key, json.dumps(valid), mode, now),
        )
        if cursor.rowcount:
            db.executemany(
                "UPDATE traits SET use_count=use_count+1, last_used=? WHERE id=?",
                [(now, i) for i in valid],
            )
        db.execute(
            "DELETE FROM applications WHERE scope=? AND id NOT IN (SELECT id FROM applications WHERE scope=? ORDER BY id DESC LIMIT 200)",
            (scope, scope),
        )
        return cursor.rowcount == 1

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
        elif action in ("trait", "feedback"):
            row = db.execute(
                "SELECT id,kind FROM traits WHERE scope=? AND id=?",
                (scope, payload["id"]),
            ).fetchone()
            if not row:
                raise ValueError("学习条目不存在或不属于当前会话。")
            if (
                row["kind"] == "jargon"
                and scope == SHARED_SCOPE
                and payload.get("status") == "active"
            ):
                raise ValueError(
                    "旧共用词汇的来源尚未确定，原条目与证据已保留；请在来源会话重新学习后审核。"
                )
            updates = {
                k: v
                for k, v in payload.items()
                if k
                in (
                    ("rating",)
                    if action == "feedback"
                    else ("content", "situation", "status")
                )
            }
            for key, value in updates.items():
                db.execute(
                    f"UPDATE traits SET {key}=?, updated=? WHERE scope=? AND id=?",
                    (value, time.time(), scope, payload["id"]),
                )
            if action == "trait":
                db.execute(
                    "UPDATE traits SET manual=1 WHERE scope=? AND id=?",
                    (scope, payload["id"]),
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
            db.execute(
                "UPDATE traits SET status='disabled', manual=1 WHERE scope=?", (scope,)
            )
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
                for key in (
                    "support_count",
                    "evidence_count",
                    "last_seen",
                    "rating",
                    "manual",
                    "meaning_stage",
                ):
                    if key in trait:
                        db.execute(
                            f"UPDATE traits SET {key}=? WHERE scope=? AND id=?",
                            (trait[key], scope, trait["id"]),
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
