"""World Engine v4.3 companion publication and loopback Foundry delivery.

The database lease and fencing token protect local SQLite transitions only.
The current Foundry relay does not expose an enforceable idempotency or fencing
contract. Diagnostic headers are sent, but this module deliberately makes no
remote exactly-once claim. A process paused after the final lease check can
still send after its local lease expires; uncertain delivery is therefore a
first-class terminal state requiring operator reconciliation.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import ipaddress
import json
import math
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

COMPANION_SCHEMA_VERSION = 1
MAX_ENVELOPE_BYTES = 65_536
MAX_EVIDENCE_BYTES = 4_096
MAX_LEASE_SECONDS = 3_600
OUTBOX_PENDING = "pending"
OUTBOX_SENDING = "sending"
OUTBOX_SENT = "sent"
OUTBOX_DEAD = "dead"
OUTBOX_DELIVERY_UNKNOWN = "delivery_unknown"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_API_KEY_RE = re.compile(r"^[A-Za-z0-9._~+/=-]*$")
_OWNER_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.-]*://|www\."
    r"|\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}(?::\d{1,5})?(?:[/?:#][^\s]*)?)",
    re.I,
)
_HTML_RE = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_MARKDOWN_RE = re.compile(
    r"(?:```|~~~|!\[[^\]]*\]\(|\[[^\]]+\]\(|`[^`\n]+`|\*\*|__|~~)"
    r"|(?<!\w)(?:\*[^*\n]+\*|_[^_\n]+_)(?!\w)"
    r"|(?:^|\n)(?: {4}|\t|\s{0,3}(?:#{1,6}\s|>\s|[-+*]\s+|\d+[.)]\s+"
    r"|(?:-{3,}|\*{3,}|_{3,}|={3,})\s*$))",
    re.M,
)
_EVIDENCE_KEYS = frozenset(
    {
        "verification_version",
        "campaign_id",
        "turn_id",
        "authoritative_revision",
        "packet_id",
        "packet_digest",
        "packet_version",
        "output_id",
        "output_hash",
        "receipt_id",
        "receipt_version",
        "accepted",
        "hard_pass",
        "evidence_digest",
    }
)
_PRESENTATION_KEYS = frozenset(
    {"presentation_version", "kind", "presentation_id", "narrative_evidence"}
)


@dataclass(frozen=True)
class RouteSpec:
    http_path: str
    event_type: str


ROUTES = {
    ("foundry", "foundry-relay-v1", "chat"): RouteSpec(
        "/chat", "presentation.published"
    )
}

_SCHEMA_DDL = (
    """
    CREATE TABLE IF NOT EXISTS we_companion_schema_meta (
        component TEXT NOT NULL PRIMARY KEY CHECK(component = 'companion'),
        version INTEGER NOT NULL,
        applied_at INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS we_companion_presentations (
        campaign_id TEXT NOT NULL,
        presentation_id TEXT NOT NULL,
        packet_id TEXT NOT NULL,
        accepted_output_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        canonical_bytes BLOB NOT NULL,
        content_sha256 TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY(campaign_id, presentation_id),
        UNIQUE(campaign_id, packet_id),
        UNIQUE(campaign_id, accepted_output_id),
        FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS we_companion_outbox (
        campaign_id TEXT NOT NULL,
        outbox_id TEXT NOT NULL,
        presentation_id TEXT NOT NULL,
        packet_id TEXT NOT NULL,
        accepted_output_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        provider_route TEXT NOT NULL,
        provider_version TEXT NOT NULL,
        event_type TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        payload_bytes BLOB NOT NULL,
        status TEXT NOT NULL CHECK(
            status IN ('pending', 'sending', 'sent', 'dead', 'delivery_unknown')
        ),
        attempts INTEGER NOT NULL DEFAULT 0,
        claim_owner TEXT,
        lease_deadline INTEGER,
        fencing_token INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        next_attempt_at INTEGER,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY(campaign_id, outbox_id),
        UNIQUE(campaign_id, provider, idempotency_key),
        UNIQUE(campaign_id, packet_id),
        UNIQUE(campaign_id, accepted_output_id),
        FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS we_companion_retry_policy (
        provider TEXT NOT NULL,
        event_type TEXT NOT NULL,
        max_attempts INTEGER NOT NULL CHECK(max_attempts > 0),
        base_delay_seconds INTEGER NOT NULL CHECK(base_delay_seconds > 0),
        max_delay_seconds INTEGER NOT NULL CHECK(max_delay_seconds > 0),
        PRIMARY KEY(provider, event_type)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_we_companion_outbox_due
    ON we_companion_outbox(provider, status, next_attempt_at, created_at)
    """,
)

# Retained as a human-readable export only. Installation must use
# install_companion_schema_db(); multi-statement script execution would implicitly commit.
COMPANION_SCHEMA = ";\n".join(statement.strip() for statement in _SCHEMA_DDL)

_EXPECTED_COLUMNS = {
    "we_companion_schema_meta": (
        ("component", "TEXT", 1, 1),
        ("version", "INTEGER", 1, 0),
        ("applied_at", "INTEGER", 1, 0),
    ),
    "we_companion_presentations": (
        ("campaign_id", "TEXT", 1, 1),
        ("presentation_id", "TEXT", 1, 2),
        ("packet_id", "TEXT", 1, 0),
        ("accepted_output_id", "TEXT", 1, 0),
        ("revision", "INTEGER", 1, 0),
        ("canonical_bytes", "BLOB", 1, 0),
        ("content_sha256", "TEXT", 1, 0),
        ("created_at", "INTEGER", 1, 0),
    ),
    "we_companion_outbox": (
        ("campaign_id", "TEXT", 1, 1),
        ("outbox_id", "TEXT", 1, 2),
        ("presentation_id", "TEXT", 1, 0),
        ("packet_id", "TEXT", 1, 0),
        ("accepted_output_id", "TEXT", 1, 0),
        ("provider", "TEXT", 1, 0),
        ("provider_route", "TEXT", 1, 0),
        ("provider_version", "TEXT", 1, 0),
        ("event_type", "TEXT", 1, 0),
        ("idempotency_key", "TEXT", 1, 0),
        ("payload_bytes", "BLOB", 1, 0),
        ("status", "TEXT", 1, 0),
        ("attempts", "INTEGER", 1, 0),
        ("claim_owner", "TEXT", 0, 0),
        ("lease_deadline", "INTEGER", 0, 0),
        ("fencing_token", "INTEGER", 1, 0),
        ("last_error", "TEXT", 0, 0),
        ("next_attempt_at", "INTEGER", 0, 0),
        ("created_at", "INTEGER", 1, 0),
        ("updated_at", "INTEGER", 1, 0),
    ),
    "we_companion_retry_policy": (
        ("provider", "TEXT", 1, 1),
        ("event_type", "TEXT", 1, 2),
        ("max_attempts", "INTEGER", 1, 0),
        ("base_delay_seconds", "INTEGER", 1, 0),
        ("max_delay_seconds", "INTEGER", 1, 0),
    ),
}


class CompanionConflict(RuntimeError):
    """A lease, identity, or immutable-record comparison failed."""


class PresentationConflict(ValueError):
    """The requested publication conflicts with authoritative state."""


class CompanionMigrationError(RuntimeError):
    """An existing companion schema cannot be safely adopted implicitly."""


class PermanentDisabled(RuntimeError):
    """The requested companion capability is intentionally unavailable."""


class TransportError(RuntimeError):
    """Base class for typed Foundry transport outcomes."""


class RetryableTransportError(TransportError):
    """A failure proven to have happened before any remote send began."""

    def __init__(
        self,
        message: str,
        *,
        safe_to_retry: bool = False,
        request_started: bool = False,
    ):
        super().__init__(message)
        self.safe_to_retry = safe_to_retry
        self.request_started = request_started


class PermanentTransportError(TransportError):
    """A deterministic pre-send validation or configuration failure."""


class AmbiguousTransportError(TransportError):
    """A request may have reached the remote system; do not auto-retry."""


FoundryError = PermanentTransportError


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def validate_public_text(
    value: Any,
    *,
    max_chars: int,
    allow_empty: bool = False,
) -> str:
    """Validate exact plain player-facing text; never normalize its bytes."""
    if not isinstance(value, str) or len(value) > max_chars:
        raise ValueError("PUBLIC_PRESENTATION_CONTENT_INVALID")
    if not allow_empty and not value.strip():
        raise ValueError("PUBLIC_PRESENTATION_CONTENT_INVALID")
    if (
        _CONTROL_RE.search(value)
        or _URL_RE.search(value)
        or _HTML_RE.search(value)
        or _MARKDOWN_RE.search(value)
    ):
        raise ValueError("PUBLIC_PRESENTATION_CONTENT_INVALID")
    return value


def _clock() -> int:
    import time

    return int(time.time())


def _redact(value: object, secret: str = "") -> str:
    text = str(value)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    return text[:2_000]


def _normalize(value: Any, *, depth: int = 0) -> Any:
    """Deep-copy a strict JSON value before evidence bytes are captured."""
    if depth > 8:
        raise ValueError("DTO nesting exceeds 8")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("DTO number must be finite")
        return value
    if isinstance(value, str):
        if len(value) > 24_000:
            raise ValueError("DTO string too large")
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > 64:
            raise ValueError("DTO collection too large")
        return [_normalize(item, depth=depth + 1) for item in value]
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise ValueError("DTO object too large")
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not 1 <= len(key) <= 128:
                raise ValueError("invalid DTO object key")
            normalized[key] = _normalize(item, depth=depth + 1)
        return normalized
    raise ValueError("DTO contains unsupported value")


def _strict_public_id(value: Any, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or _PUBLIC_ID_RE.fullmatch(value) is None
    ):
        raise ValueError(f"invalid narrative_evidence.{field_name}")
    return value


def _validate_evidence(
    presentation: Mapping[str, Any],
    *,
    campaign_id: str,
    turn_id: str | None,
    revision: int,
) -> None:
    evidence = presentation.get("narrative_evidence")
    if not isinstance(evidence, Mapping):
        raise TypeError("presentation.narrative_evidence is required")
    if set(evidence) != _EVIDENCE_KEYS:
        raise ValueError("narrative_evidence has missing, extra, or private fields")
    if len(canonical_json_bytes(_normalize(evidence))) > MAX_EVIDENCE_BYTES:
        raise ValueError("narrative_evidence exceeds aggregate byte cap")
    if evidence["verification_version"] != "NOV-1.0":
        raise ValueError("unsupported verification_version")
    if evidence["packet_version"] != "NRP-1.2":
        raise ValueError("unsupported packet_version")
    if evidence["receipt_version"] != "NQR-1.2":
        raise ValueError("unsupported receipt_version")
    if evidence["campaign_id"] != campaign_id:
        raise ValueError("narrative_evidence campaign binding mismatch")
    if turn_id is None or evidence["turn_id"] != turn_id:
        raise ValueError("narrative_evidence turn binding mismatch")
    authoritative_revision = evidence["authoritative_revision"]
    if (
        isinstance(authoritative_revision, bool)
        or not isinstance(authoritative_revision, int)
        or authoritative_revision != revision
    ):
        raise ValueError("narrative_evidence revision binding mismatch")
    if evidence["accepted"] is not True or evidence["hard_pass"] is not True:
        raise ValueError("narrative_evidence is not an accepted hard pass")
    for name in ("packet_id", "output_id", "receipt_id"):
        _strict_public_id(evidence[name], name)
    for name in ("packet_digest", "output_hash", "evidence_digest"):
        value = evidence[name]
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise ValueError(f"invalid narrative_evidence.{name}")
    digest_payload = dict(evidence)
    actual_evidence_digest = digest_payload.pop("evidence_digest")
    expected_evidence_digest = hashlib.sha256(
        canonical_json_bytes(digest_payload)
    ).hexdigest()
    if not hmac.compare_digest(actual_evidence_digest, expected_evidence_digest):
        raise ValueError("narrative_evidence digest mismatch")


@dataclass(frozen=True)
class PresentationEnvelope:
    campaign_id: str
    presentation_id: str
    revision: int
    narration: str
    turn_id: str | None = None
    choices: tuple[str, ...] = ()
    presentation: Mapping[str, Any] = field(default_factory=dict)
    _canonical_bytes: bytes = field(init=False, repr=False, compare=False)
    _content_sha256: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _strict_public_id(self.campaign_id, "campaign_id")
        _strict_public_id(self.presentation_id, "presentation_id")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise TypeError("invalid revision")
        if self.revision < 0:
            raise ValueError("invalid revision")
        validate_public_text(self.narration, max_chars=24_000)
        if self.turn_id is not None:
            _strict_public_id(self.turn_id, "turn_id")
        if (
            not isinstance(self.choices, tuple)
            or len(self.choices) > 12
        ):
            raise ValueError("invalid choices")
        for choice in self.choices:
            validate_public_text(choice, max_chars=500)
        if not isinstance(self.presentation, Mapping):
            raise TypeError("invalid presentation")

        normalized_presentation = _normalize(self.presentation)
        if set(normalized_presentation) != _PRESENTATION_KEYS:
            raise ValueError("presentation has missing, extra, or private fields")
        if (
            normalized_presentation.get("presentation_version") != "WEP-1.0"
            or normalized_presentation.get("kind") != "narrative"
            or normalized_presentation.get("presentation_id") != self.presentation_id
        ):
            raise ValueError("invalid closed presentation object")
        _validate_evidence(
            normalized_presentation,
            campaign_id=self.campaign_id,
            turn_id=self.turn_id,
            revision=self.revision,
        )
        normalized = {
            "campaign_id": self.campaign_id,
            "presentation_id": self.presentation_id,
            "revision": self.revision,
            "narration": self.narration,
            "turn_id": self.turn_id,
            "choices": list(self.choices),
            "presentation": normalized_presentation,
        }
        raw = canonical_json_bytes(normalized)
        if len(raw) > MAX_ENVELOPE_BYTES:
            raise ValueError("presentation envelope exceeds aggregate byte cap")
        object.__setattr__(self, "_canonical_bytes", raw)
        object.__setattr__(self, "_content_sha256", hashlib.sha256(raw).hexdigest())

    def as_dict(self) -> dict[str, Any]:
        """Return a fresh value decoded from the immutable captured evidence."""
        value = json.loads(self._canonical_bytes.decode("utf-8"))
        if not isinstance(value, dict):  # pragma: no cover - construction invariant
            raise TypeError("invalid canonical presentation envelope")
        return value

    def canonical_bytes(self) -> bytes:
        return self._canonical_bytes

    def content_sha256(self) -> str:
        return self._content_sha256


@dataclass(frozen=True)
class OutboxClaim:
    campaign_id: str
    outbox_id: str
    provider: str
    provider_route: str
    provider_version: str
    event_type: str
    idempotency_key: str
    payload_bytes: bytes
    attempts: int
    claim_owner: str
    lease_deadline: int
    fencing_token: int

    def payload(self) -> dict[str, Any]:
        value = json.loads(self.payload_bytes)
        if not isinstance(value, dict):
            raise TypeError("outbox payload is not an object")
        return value


class EngineRepository(Protocol):
    def _db(self): ...

    def _write_db(self): ...


def _table_columns(db: sqlite3.Connection, table: str) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (row[1], str(row[2]).upper(), int(row[3]), int(row[5]))
        for row in db.execute(f'PRAGMA table_info("{table}")')
    )


def _index_columns(db: sqlite3.Connection, index_name: str) -> tuple[str, ...]:
    return tuple(row[2] for row in db.execute(f'PRAGMA index_info("{index_name}")'))


def _has_unique_index(
    db: sqlite3.Connection, table: str, columns: tuple[str, ...]
) -> bool:
    for row in db.execute(f'PRAGMA index_list("{table}")'):
        if (
            int(row[2]) == 1
            and int(row[4]) == 0
            and _index_columns(db, row[1]) == columns
        ):
            return True
    return False


def _validate_schema(db: sqlite3.Connection) -> None:
    campaign_columns = {row[1] for row in db.execute('PRAGMA table_info("campaigns")')}
    if not {"id", "revision"}.issubset(campaign_columns):
        raise CompanionMigrationError("campaigns(id, revision) must exist first")
    for table, expected in _EXPECTED_COLUMNS.items():
        actual = _table_columns(db, table)
        if actual != expected:
            raise CompanionMigrationError(
                f"unsafe legacy companion table {table}; explicit migration required"
            )
    due_index = db.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?",
        ("idx_we_companion_outbox_due",),
    ).fetchone()
    due_index_rows = {row[1]: row for row in db.execute(
        'PRAGMA index_list("we_companion_outbox")'
    )}
    due_metadata = due_index_rows.get("idx_we_companion_outbox_due")
    if (
        due_index is None
        or due_index[0] != "we_companion_outbox"
        or due_metadata is None
        or int(due_metadata[2]) != 0
        or int(due_metadata[4]) != 0
        or _index_columns(db, "idx_we_companion_outbox_due") != (
            "provider",
            "status",
            "next_attempt_at",
            "created_at",
        )
    ):
        raise CompanionMigrationError("companion due index mismatch")
    if not _has_unique_index(
        db,
        "we_companion_outbox",
        ("campaign_id", "provider", "idempotency_key"),
    ):
        raise CompanionMigrationError("companion idempotency index mismatch")
    for table in ("we_companion_presentations", "we_companion_outbox"):
        for binding in ("packet_id", "accepted_output_id"):
            if not _has_unique_index(db, table, ("campaign_id", binding)):
                raise CompanionMigrationError(
                    f"companion {table} {binding} binding mismatch"
                )
    expected_foreign_keys = {
        ("campaigns", "campaign_id", "id", "CASCADE")
    }
    for table in ("we_companion_presentations", "we_companion_outbox"):
        foreign_keys = {
            (row[2], row[3], row[4], str(row[6]).upper())
            for row in db.execute(f'PRAGMA foreign_key_list("{table}")')
        }
        if foreign_keys != expected_foreign_keys:
            raise CompanionMigrationError(f"companion foreign key mismatch for {table}")


def install_companion_schema_db(db: sqlite3.Connection, now: int) -> None:
    """Atomically install and fully validate schema in the caller's connection."""
    if isinstance(now, bool) or not isinstance(now, int) or now < 0:
        raise ValueError("invalid companion schema timestamp")
    savepoint = "we_companion_schema_install"
    db.execute(f"SAVEPOINT {savepoint}")
    try:
        for statement in _SCHEMA_DDL:
            db.execute(statement)
        _validate_schema(db)
        row = db.execute(
            "SELECT version FROM we_companion_schema_meta WHERE component='companion'"
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO we_companion_schema_meta(component, version, applied_at) "
                "VALUES('companion', ?, ?)",
                (COMPANION_SCHEMA_VERSION, now),
            )
        elif int(row[0]) != COMPANION_SCHEMA_VERSION:
            raise CompanionMigrationError("unsupported companion schema version")

        policy = (5, 2, 300)
        db.execute(
            "INSERT INTO we_companion_retry_policy("
            "provider, event_type, max_attempts, base_delay_seconds, max_delay_seconds"
            ") VALUES('foundry', 'presentation.published', ?, ?, ?) "
            "ON CONFLICT(provider, event_type) DO NOTHING",
            policy,
        )
        existing = db.execute(
            "SELECT max_attempts, base_delay_seconds, max_delay_seconds "
            "FROM we_companion_retry_policy "
            "WHERE provider='foundry' AND event_type='presentation.published'"
        ).fetchone()
        if existing is None or tuple(existing) != policy:
            raise CompanionMigrationError("existing companion retry policy mismatch")
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception as exc:
        db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
        if isinstance(exc, (CompanionMigrationError, ValueError)):
            raise
        raise CompanionMigrationError("companion schema install failed") from exc


class CompanionService:
    def __init__(
        self,
        engine: EngineRepository,
        *,
        clock: Callable[[], int] | None = None,
    ):
        self.engine = engine
        self.clock = clock or _clock

    def now(self) -> int:
        value = self.clock()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("invalid companion clock")
        return value

    def ensure_schema(self) -> None:
        with self.engine._write_db() as db:
            install_companion_schema_db(db, self.now())

    def publish(
        self,
        envelope: PresentationEnvelope,
        provider: str = "foundry",
        route: str = "chat",
        version: str = "foundry-relay-v1",
    ) -> dict[str, Any]:
        """Standalone wrapper for callers outside acceptance orchestration."""
        with self.engine._write_db() as db:
            # A full WorldEngine owns narrative evidence. Its wrapper may only
            # replay an already committed acceptance; new atomic acceptances
            # use enqueue_presentation_db from the orchestrating transaction.
            has_acceptance_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                ("we43_narrative_packet_acceptances",),
            ).fetchone()
            if has_acceptance_table is not None:
                payload = envelope.as_dict()
                evidence = payload["presentation"]["narrative_evidence"]
                accepted = db.execute(
                    "SELECT accepted_output_id,receipt_id,presentation_id "
                    "FROM we43_narrative_packet_acceptances "
                    "WHERE campaign_id=? AND packet_id=?",
                    (envelope.campaign_id, evidence["packet_id"]),
                ).fetchone()
                if accepted is None or (
                    accepted["accepted_output_id"] != evidence["output_id"]
                    or accepted["receipt_id"] != evidence["receipt_id"]
                    or accepted["presentation_id"] != envelope.presentation_id
                ):
                    raise PresentationConflict("COMPANION_ACCEPTANCE_REQUIRED")
            return self.enqueue_presentation_db(
                db,
                envelope,
                provider=provider,
                route=route,
                version=version,
            )

    def enqueue_presentation_db(
        self,
        db: sqlite3.Connection,
        envelope: PresentationEnvelope,
        *,
        provider: str = "foundry",
        route: str = "chat",
        version: str = "foundry-relay-v1",
    ) -> dict[str, Any]:
        """Insert immutable presentation and outbox rows on the caller's tx."""
        if not db.in_transaction:
            raise ValueError("COMPANION_TRANSACTION_REQUIRED")
        route_spec = ROUTES.get((provider, version, route))
        if route_spec is None:
            raise ValueError("unallowlisted provider route/version")
        raw = envelope.canonical_bytes()
        digest = envelope.content_sha256()
        payload = envelope.as_dict()
        evidence = payload["presentation"]["narrative_evidence"]
        packet_id = _strict_public_id(evidence["packet_id"], "packet_id")
        accepted_output_id = _strict_public_id(evidence["output_id"], "output_id")
        now = self.now()
        idempotency_key = f"presentation:{envelope.presentation_id}:{digest}"
        outbox_id = "out_" + uuid.uuid4().hex

        campaign = db.execute(
            "SELECT revision FROM campaigns WHERE id=?", (envelope.campaign_id,)
        ).fetchone()
        if campaign is None:
            raise PresentationConflict("campaign does not exist")
        if int(campaign["revision"]) != envelope.revision:
            raise PresentationConflict("stale authoritative campaign revision")

        db.execute(
            "INSERT INTO we_companion_presentations("
            "campaign_id, presentation_id, packet_id, accepted_output_id, revision, "
            "canonical_bytes, content_sha256, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (
                envelope.campaign_id,
                envelope.presentation_id,
                packet_id,
                accepted_output_id,
                envelope.revision,
                raw,
                digest,
                now,
            ),
        )
        stored_presentation = db.execute(
            "SELECT packet_id, accepted_output_id, revision, canonical_bytes, content_sha256 "
            "FROM we_companion_presentations "
            "WHERE campaign_id=? AND presentation_id=?",
            (envelope.campaign_id, envelope.presentation_id),
        ).fetchone()
        if stored_presentation is None or (
            stored_presentation["packet_id"] != packet_id
            or stored_presentation["accepted_output_id"] != accepted_output_id
            or int(stored_presentation["revision"]) != envelope.revision
            or bytes(stored_presentation["canonical_bytes"]) != raw
            or stored_presentation["content_sha256"] != digest
        ):
            raise PresentationConflict(
                "presentation identity already exists with different canonical evidence"
            )

        db.execute(
            "INSERT INTO we_companion_outbox("
            "campaign_id, outbox_id, presentation_id, packet_id, accepted_output_id, "
            "provider, provider_route, provider_version, event_type, idempotency_key, "
            "payload_bytes, status, attempts, fencing_token, created_at, updated_at"
            ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 0, ?, ?) "
            "ON CONFLICT(campaign_id, provider, idempotency_key) DO NOTHING",
            (
                envelope.campaign_id,
                outbox_id,
                envelope.presentation_id,
                packet_id,
                accepted_output_id,
                provider,
                route,
                version,
                route_spec.event_type,
                idempotency_key,
                raw,
                now,
                now,
            ),
        )
        stored_outbox = db.execute(
            "SELECT outbox_id, presentation_id, packet_id, accepted_output_id, "
            "provider_route, provider_version, event_type, payload_bytes "
            "FROM we_companion_outbox "
            "WHERE campaign_id=? AND provider=? AND idempotency_key=?",
            (envelope.campaign_id, provider, idempotency_key),
        ).fetchone()
        if stored_outbox is None or (
            stored_outbox["presentation_id"] != envelope.presentation_id
            or stored_outbox["packet_id"] != packet_id
            or stored_outbox["accepted_output_id"] != accepted_output_id
            or stored_outbox["provider_route"] != route
            or stored_outbox["provider_version"] != version
            or stored_outbox["event_type"] != route_spec.event_type
            or bytes(stored_outbox["payload_bytes"]) != raw
        ):
            raise CompanionConflict("existing outbox identity or payload mismatch")

        return payload | {
            "content_sha256": digest,
            "outbox_id": stored_outbox["outbox_id"],
            "packet_id": packet_id,
            "accepted_output_id": accepted_output_id,
        }

    def enqueue_snapshot(self, *_args: Any, **_kwargs: Any) -> None:
        raise PermanentDisabled(
            "snapshot publication is disabled until a reviewed safe projection exists"
        )

    @staticmethod
    def _validate_provider(provider: Any) -> str:
        if not isinstance(provider, str) or provider not in {
            key[0] for key in ROUTES
        }:
            raise ValueError("invalid provider")
        return provider

    @staticmethod
    def _validate_owner(owner: Any) -> str:
        if (
            not isinstance(owner, str)
            or not 1 <= len(owner) <= 128
            or _OWNER_RE.fullmatch(owner) is None
        ):
            raise ValueError("invalid claim owner")
        return owner

    @staticmethod
    def _validate_lease_seconds(lease_seconds: Any) -> int:
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 1 <= lease_seconds <= MAX_LEASE_SECONDS
        ):
            raise ValueError("invalid lease duration")
        return lease_seconds

    def _expire(self, db: sqlite3.Connection, provider: str, now: int) -> int:
        rows = db.execute(
            "SELECT campaign_id, outbox_id, fencing_token "
            "FROM we_companion_outbox "
            "WHERE provider=? AND status='sending' AND lease_deadline<=?",
            (provider, now),
        ).fetchall()
        changed = 0
        for row in rows:
            changed += db.execute(
                "UPDATE we_companion_outbox SET "
                "status='delivery_unknown', claim_owner=NULL, lease_deadline=NULL, "
                "fencing_token=fencing_token+1, last_error=?, updated_at=? "
                "WHERE campaign_id=? AND outbox_id=? AND status='sending' "
                "AND fencing_token=? AND lease_deadline<=?",
                (
                    "lease expired: remote delivery unknown",
                    now,
                    row["campaign_id"],
                    row["outbox_id"],
                    row["fencing_token"],
                    now,
                ),
            ).rowcount
        return changed

    def recover_expired_claims(self, provider: str) -> int:
        self._validate_provider(provider)
        with self.engine._write_db() as db:
            return self._expire(db, provider, self.now())

    def claim_one(
        self,
        provider: str,
        owner: str,
        *,
        lease_seconds: int = 30,
    ) -> OutboxClaim | None:
        self._validate_provider(provider)
        self._validate_owner(owner)
        self._validate_lease_seconds(lease_seconds)
        now = self.now()
        deadline = now + lease_seconds
        with self.engine._write_db() as db:
            self._expire(db, provider, now)
            row = db.execute(
                "SELECT * FROM we_companion_outbox "
                "WHERE provider=? AND status='pending' "
                "AND (next_attempt_at IS NULL OR next_attempt_at<=?) "
                "ORDER BY created_at, outbox_id LIMIT 1",
                (provider, now),
            ).fetchone()
            if row is None:
                return None
            updated = db.execute(
                "UPDATE we_companion_outbox SET "
                "status='sending', attempts=attempts+1, claim_owner=?, "
                "lease_deadline=?, fencing_token=fencing_token+1, updated_at=? "
                "WHERE campaign_id=? AND outbox_id=? AND status='pending' "
                "AND fencing_token=?",
                (
                    owner,
                    deadline,
                    now,
                    row["campaign_id"],
                    row["outbox_id"],
                    row["fencing_token"],
                ),
            )
            if updated.rowcount != 1:
                return None
            return OutboxClaim(
                campaign_id=row["campaign_id"],
                outbox_id=row["outbox_id"],
                provider=row["provider"],
                provider_route=row["provider_route"],
                provider_version=row["provider_version"],
                event_type=row["event_type"],
                idempotency_key=row["idempotency_key"],
                payload_bytes=bytes(row["payload_bytes"]),
                attempts=int(row["attempts"]) + 1,
                claim_owner=owner,
                lease_deadline=deadline,
                fencing_token=int(row["fencing_token"]) + 1,
            )

    def assert_current_claim(self, claim: OutboxClaim) -> None:
        """Check the local lease immediately before send.

        This cannot revoke a process that pauses after this check; the remote
        relay would need to enforce the fencing token to close that gap.
        """
        self._validate_claim_route(claim)
        now = self.now()
        with self.engine._db() as db:
            row = db.execute(
                "SELECT attempts FROM we_companion_outbox "
                "WHERE campaign_id=? AND outbox_id=? AND status='sending' "
                "AND claim_owner=? AND fencing_token=? AND lease_deadline>?",
                (
                    claim.campaign_id,
                    claim.outbox_id,
                    claim.claim_owner,
                    claim.fencing_token,
                    now,
                ),
            ).fetchone()
        if row is None or int(row["attempts"]) != claim.attempts:
            raise CompanionConflict("lease lost")

    def renew(self, claim: OutboxClaim, lease_seconds: int = 30) -> OutboxClaim:
        self._validate_lease_seconds(lease_seconds)
        now = self.now()
        deadline = now + lease_seconds
        with self.engine._write_db() as db:
            updated = db.execute(
                "UPDATE we_companion_outbox SET "
                "lease_deadline=?, fencing_token=fencing_token+1, updated_at=? "
                "WHERE campaign_id=? AND outbox_id=? AND status='sending' "
                "AND claim_owner=? AND fencing_token=? AND lease_deadline>?",
                (
                    deadline,
                    now,
                    claim.campaign_id,
                    claim.outbox_id,
                    claim.claim_owner,
                    claim.fencing_token,
                    now,
                ),
            )
            if updated.rowcount != 1:
                raise CompanionConflict("lease lost")
        return replace(
            claim,
            lease_deadline=deadline,
            fencing_token=claim.fencing_token + 1,
        )

    @staticmethod
    def _validate_claim_route(claim: OutboxClaim) -> RouteSpec:
        spec = ROUTES.get(
            (claim.provider, claim.provider_version, claim.provider_route)
        )
        if spec is None or claim.event_type != spec.event_type:
            raise PermanentTransportError(
                "immutable provider/version/route/event_type refused"
            )
        if (
            not isinstance(claim.idempotency_key, str)
            or not 1 <= len(claim.idempotency_key) <= 256
            or _PUBLIC_ID_RE.fullmatch(claim.idempotency_key) is None
        ):
            raise PermanentTransportError("invalid diagnostic idempotency key")
        return spec

    def _finish(
        self,
        claim: OutboxClaim,
        status: str,
        error: str | None = None,
        next_attempt_at: int | None = None,
    ) -> None:
        if status not in {OUTBOX_SENT, OUTBOX_DEAD, OUTBOX_DELIVERY_UNKNOWN}:
            raise ValueError("invalid terminal outbox status")
        now = self.now()
        with self.engine._write_db() as db:
            updated = db.execute(
                "UPDATE we_companion_outbox SET "
                "status=?, last_error=?, next_attempt_at=?, claim_owner=NULL, "
                "lease_deadline=NULL, fencing_token=fencing_token+1, updated_at=? "
                "WHERE campaign_id=? AND outbox_id=? AND status='sending' "
                "AND claim_owner=? AND fencing_token=? AND lease_deadline>?",
                (
                    status,
                    _redact(error or ""),
                    next_attempt_at,
                    now,
                    claim.campaign_id,
                    claim.outbox_id,
                    claim.claim_owner,
                    claim.fencing_token,
                    now,
                ),
            )
            if updated.rowcount != 1:
                raise CompanionConflict("lease lost")

    def mark_sent(self, claim: OutboxClaim) -> None:
        self._finish(claim, OUTBOX_SENT)

    def mark_unknown(self, claim: OutboxClaim, error: object) -> None:
        self._finish(claim, OUTBOX_DELIVERY_UNKNOWN, str(error))

    def mark_permanent_failure(self, claim: OutboxClaim, error: object) -> None:
        self._finish(claim, OUTBOX_DEAD, str(error))

    def mark_retryable_failure(
        self, claim: OutboxClaim, error: RetryableTransportError
    ) -> None:
        if not error.safe_to_retry or error.request_started:
            raise ValueError("retry requires proven pre-send failure")
        now = self.now()
        with self.engine._write_db() as db:
            row = db.execute(
                "SELECT o.attempts, p.max_attempts, p.base_delay_seconds, "
                "p.max_delay_seconds FROM we_companion_outbox AS o "
                "JOIN we_companion_retry_policy AS p "
                "ON p.provider=o.provider AND p.event_type=o.event_type "
                "WHERE o.campaign_id=? AND o.outbox_id=? AND o.status='sending' "
                "AND o.claim_owner=? AND o.fencing_token=? AND o.lease_deadline>?",
                (
                    claim.campaign_id,
                    claim.outbox_id,
                    claim.claim_owner,
                    claim.fencing_token,
                    now,
                ),
            ).fetchone()
            if row is None:
                raise CompanionConflict("lease lost or retry policy missing")
            attempts = int(row["attempts"])
            if attempts >= int(row["max_attempts"]):
                status = OUTBOX_DEAD
                next_attempt_at = None
                message = "retry budget exhausted: " + str(error)
            else:
                status = OUTBOX_PENDING
                delay = min(
                    int(row["max_delay_seconds"]),
                    int(row["base_delay_seconds"]) * (2 ** max(0, attempts - 1)),
                )
                next_attempt_at = now + delay
                message = str(error)
            updated = db.execute(
                "UPDATE we_companion_outbox SET "
                "status=?, last_error=?, next_attempt_at=?, claim_owner=NULL, "
                "lease_deadline=NULL, fencing_token=fencing_token+1, updated_at=? "
                "WHERE campaign_id=? AND outbox_id=? AND status='sending' "
                "AND claim_owner=? AND fencing_token=? AND lease_deadline>? "
                "AND attempts=?",
                (
                    status,
                    _redact(message),
                    next_attempt_at,
                    now,
                    claim.campaign_id,
                    claim.outbox_id,
                    claim.claim_owner,
                    claim.fencing_token,
                    now,
                    attempts,
                ),
            )
            if updated.rowcount != 1:
                raise CompanionConflict("lease lost during retry transition")

    def reconcile_delivery_unknown(
        self,
        campaign_id: str,
        outbox_id: str,
        *,
        confirmed: str,
        evidence: str,
    ) -> None:
        if confirmed not in {OUTBOX_SENT, "not_delivered"}:
            raise ValueError("explicit delivery confirmation required")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("reconciliation evidence required")
        now = self.now()
        with self.engine._write_db() as db:
            updated = db.execute(
                "UPDATE we_companion_outbox SET "
                "status=?, last_error=?, fencing_token=fencing_token+1, updated_at=? "
                "WHERE campaign_id=? AND outbox_id=? AND status='delivery_unknown'",
                (
                    OUTBOX_SENT if confirmed == OUTBOX_SENT else OUTBOX_DEAD,
                    "reconciled: " + evidence[:1_800],
                    now,
                    campaign_id,
                    outbox_id,
                ),
            )
            if updated.rowcount != 1:
                raise CompanionConflict("delivery_unknown row was not reconciled")

    def outbox_row(self, campaign_id: str, outbox_id: str) -> dict[str, Any] | None:
        with self.engine._db() as db:
            row = db.execute(
                "SELECT * FROM we_companion_outbox "
                "WHERE campaign_id=? AND outbox_id=?",
                (campaign_id, outbox_id),
            ).fetchone()
        return dict(row) if row is not None else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any):
        raise AmbiguousTransportError(
            "redirect refused after send; credential was not forwarded"
        )


@dataclass(frozen=True)
class FoundryConfig:
    base_url: str = "http://127.0.0.1:3010"
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 10.0
    max_request_bytes: int = 262_144
    max_response_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if not isinstance(self.base_url, str) or self.base_url != self.base_url.strip():
            raise ValueError("invalid Foundry base_url")
        parsed = urllib.parse.urlsplit(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or "%" in parsed.hostname
        ):
            raise ValueError("Foundry relay must be a root loopback IP origin")
        try:
            address = ipaddress.ip_address(parsed.hostname)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Foundry relay host must be a literal loopback IP") from exc
        if not (
            isinstance(address, ipaddress.IPv4Address)
            and address.packed[0] == 127
            or isinstance(address, ipaddress.IPv6Address)
            and address == ipaddress.IPv6Address("::1")
        ):
            raise ValueError("Foundry relay host must be 127/8 or ::1")
        if port is not None and not 1 <= port <= 65_535:
            raise ValueError("invalid Foundry relay port")
        if (
            not isinstance(self.api_key, str)
            or len(self.api_key) > 512
            or _API_KEY_RE.fullmatch(self.api_key) is None
        ):
            raise ValueError("invalid Foundry api_key")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or not 0.1 <= float(self.timeout_seconds) <= 120.0
        ):
            raise ValueError("invalid Foundry timeout")
        for name, value, maximum in (
            ("max_request_bytes", self.max_request_bytes, 1_048_576),
            ("max_response_bytes", self.max_response_bytes, 4_194_304),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= maximum
            ):
                raise ValueError(f"invalid Foundry {name}")


@dataclass(frozen=True)
class _PreparedPresentation:
    route: str
    body: bytes


class FoundryBridge:
    def __init__(self, config: FoundryConfig):
        self.config = config
        self.origin = config.base_url.rstrip("/")
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
        )

    def prepare_presentation(self, claim: OutboxClaim) -> _PreparedPresentation:
        spec = CompanionService._validate_claim_route(claim)
        try:
            payload = claim.payload()
            narration = payload["narration"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PermanentTransportError("invalid presentation payload") from exc
        if not isinstance(narration, str):
            raise PermanentTransportError("invalid presentation narration")
        content = (
            '<div class="world-engine-presentation">'
            + html.escape(narration).replace("\n", "<br>")
            + "</div>"
        )
        try:
            raw = canonical_json_bytes(
                {"content": content, "alias": "World Engine", "flavor": "World Engine"}
            )
        except (TypeError, ValueError) as exc:
            raise PermanentTransportError("invalid Foundry request body") from exc
        if len(raw) > self.config.max_request_bytes:
            raise PermanentTransportError("request exceeds cap")
        return _PreparedPresentation(route=spec.http_path, body=raw)

    def _send_prepared(
        self,
        claim: OutboxClaim,
        prepared: _PreparedPresentation,
    ) -> Any:
        # These headers are diagnostics only. The current relay is not proven to
        # enforce either value and they do not provide remote exactly-once delivery.
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": self.config.api_key,
            "x-we-idempotency-key": claim.idempotency_key,
            "x-we-fencing-token": str(claim.fencing_token),
        }
        request = urllib.request.Request(
            self.origin + prepared.route,
            data=prepared.body,
            headers=headers,
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.config.timeout_seconds) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError as exc:
                        raise AmbiguousTransportError(
                            "invalid response length after send"
                        ) from exc
                    if declared_length < 0 or declared_length > self.config.max_response_bytes:
                        raise AmbiguousTransportError("response exceeds cap after send")
                raw = response.read(self.config.max_response_bytes + 1)
                if len(raw) > self.config.max_response_bytes:
                    raise AmbiguousTransportError("response exceeds cap after send")
        except AmbiguousTransportError:
            raise
        except urllib.error.HTTPError as exc:
            # Includes 429: the POST may already have been applied remotely.
            raise AmbiguousTransportError(
                f"Foundry HTTP {exc.code} after send; delivery unknown"
            ) from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise AmbiguousTransportError(
                _redact(exc, self.config.api_key) + "; delivery unknown"
            ) from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AmbiguousTransportError("invalid JSON response after send") from exc

    def deliver_presentation(self, claim: OutboxClaim) -> Any:
        prepared = self.prepare_presentation(claim)
        return self._send_prepared(claim, prepared)


class CompanionWorker:
    def __init__(
        self,
        service: CompanionService,
        foundry: FoundryBridge,
        *,
        provider: str = "foundry",
        claim_owner: str | None = None,
    ):
        self.service = service
        self.foundry = foundry
        self.provider = provider
        self.claim_owner = claim_owner or "companion-" + uuid.uuid4().hex

    def run_once(self) -> int:
        self.service.recover_expired_claims(self.provider)
        claim = self.service.claim_one(self.provider, self.claim_owner)
        if claim is None:
            return 0
        try:
            # All deterministic validation and serialization happens before the
            # last lease renewal. The assert is the final operation before send.
            prepared = self.foundry.prepare_presentation(claim)
            claim = self.service.renew(claim)
            self.service.assert_current_claim(claim)
            self.foundry._send_prepared(claim, prepared)
            self.service.mark_sent(claim)
            return 1
        except RetryableTransportError as exc:
            try:
                if exc.safe_to_retry and not exc.request_started:
                    self.service.mark_retryable_failure(claim, exc)
                else:
                    self.service.mark_unknown(claim, exc)
            except CompanionConflict:
                pass
        except CompanionConflict:
            # A later recovery changes an expired sending row to delivery_unknown.
            return 0
        except PermanentTransportError as exc:
            try:
                self.service.mark_permanent_failure(claim, exc)
            except CompanionConflict:
                pass
        except AmbiguousTransportError as exc:
            try:
                self.service.mark_unknown(claim, exc)
            except CompanionConflict:
                pass

        return 0
