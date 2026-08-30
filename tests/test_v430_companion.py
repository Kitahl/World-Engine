from __future__ import annotations

import ast
import hashlib
import sqlite3
import urllib.error
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from world_engine.companion import (
    MAX_ENVELOPE_BYTES,
    OUTBOX_DEAD,
    OUTBOX_DELIVERY_UNKNOWN,
    OUTBOX_PENDING,
    OUTBOX_SENT,
    AmbiguousTransportError,
    CompanionConflict,
    CompanionMigrationError,
    CompanionService,
    FoundryBridge,
    FoundryConfig,
    PermanentDisabled,
    PermanentTransportError,
    PresentationConflict,
    PresentationEnvelope,
    RetryableTransportError,
    canonical_json_bytes,
    install_companion_schema_db,
)


class Clock:
    def __init__(self) -> None:
        self.value = 100

    def __call__(self) -> int:
        return self.value


class Engine:
    def __init__(self, path) -> None:
        self.path = path
        with self._write_db() as db:
            db.execute(
                "CREATE TABLE campaigns(id TEXT PRIMARY KEY, revision INTEGER NOT NULL)"
            )
            db.execute("INSERT INTO campaigns VALUES('c', 1)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _write_db(self):
        db = sqlite3.connect(self.path, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def evidence(**updates):
    value = {
        "verification_version": "NOV-1.0",
        "campaign_id": "c",
        "turn_id": "t1",
        "authoritative_revision": 1,
        "packet_id": "packet-1",
        "packet_digest": "1" * 64,
        "packet_version": "NRP-1.2",
        "output_id": "output-1",
        "output_hash": "2" * 64,
        "receipt_id": "receipt-1",
        "receipt_version": "NQR-1.2",
        "accepted": True,
        "hard_pass": True,
    }
    explicit_digest = updates.pop("evidence_digest", None)
    value.update(updates)
    value["evidence_digest"] = (
        explicit_digest
        if explicit_digest is not None
        else hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    )
    return value


_MISSING = object()


def presentation(value=_MISSING, *, presentation_id="p1"):
    return {
        "presentation_version": "WEP-1.0",
        "kind": "narrative",
        "presentation_id": presentation_id,
        "narrative_evidence": evidence() if value is _MISSING else value,
    }


def envelope(**updates) -> PresentationEnvelope:
    value = {
        "campaign_id": "c",
        "presentation_id": "p1",
        "revision": 1,
        "narration": "hello",
        "turn_id": "t1",
    }
    value.update(updates)
    value.setdefault(
        "presentation",
        presentation(presentation_id=value["presentation_id"]),
    )
    return PresentationEnvelope(**value)


@pytest.fixture
def svc(tmp_path):
    clock = Clock()
    service = CompanionService(Engine(tmp_path / "companion.db"), clock=clock)
    service.ensure_schema()
    return service, clock


def publish(service: CompanionService):
    return service.publish(envelope())


def claim(service: CompanionService):
    result = service.claim_one("foundry", "worker-a")
    assert result is not None
    return result


def test_canonical_bytes_are_deep_snapshot_and_size_bounded():
    metadata = presentation()
    item = envelope(presentation=metadata)
    original = item.canonical_bytes()
    metadata["narrative_evidence"]["packet_id"] = "forged"
    assert item.canonical_bytes() == original
    assert item.as_dict()["presentation"]["narrative_evidence"]["packet_id"] == "packet-1"
    assert item.content_sha256()
    with pytest.raises(ValueError, match="PUBLIC_PRESENTATION_CONTENT_INVALID"):
        envelope(narration="x" * 24_001)
    assert len(original) < MAX_ENVELOPE_BYTES


@pytest.mark.parametrize(
    "bad_evidence",
    [
        None,
        evidence(campaign_id="other"),
        evidence(turn_id="other"),
        evidence(authoritative_revision=2),
        evidence(packet_digest="not-a-digest"),
        evidence(accepted=False),
        evidence(private_validation_context="secret"),
    ],
)
def test_narrative_evidence_is_required_public_and_bound(bad_evidence):
    with pytest.raises((TypeError, ValueError)):
        envelope(presentation=presentation(bad_evidence))


def test_evidence_digest_detects_public_field_tamper():
    tampered = evidence()
    tampered["packet_id"] = "packet-2"
    with pytest.raises(ValueError, match="digest mismatch"):
        envelope(presentation=presentation(tampered))


def test_schema_install_validates_every_object_and_is_atomic(tmp_path):
    path = tmp_path / "bad.db"
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("CREATE TABLE campaigns(id TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
    db.execute("CREATE TABLE we_companion_outbox(campaign_id TEXT)")
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(CompanionMigrationError):
        install_companion_schema_db(db, 100)
    assert not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='we_companion_presentations'"
    ).fetchone()
    db.rollback()
    db.close()


def test_publish_checks_revision_and_exact_conflicts(svc):
    service, _clock = svc
    first = publish(service)
    assert service.publish(envelope())["content_sha256"] == first["content_sha256"]
    with service.engine._write_db() as db:
        db.execute(
            "UPDATE we_companion_outbox SET provider_route='forged' "
            "WHERE campaign_id='c'"
        )
    with pytest.raises(CompanionConflict):
        service.publish(envelope())
    with service.engine._write_db() as db:
        db.execute("UPDATE campaigns SET revision=2 WHERE id='c'")
    with pytest.raises(PresentationConflict, match="stale"):
        service.publish(envelope(presentation_id="p2"))


def test_leases_are_validated_renewed_and_fenced(svc):
    service, _clock = svc
    publish(service)
    item = claim(service)
    assert item.fencing_token == 1
    with pytest.raises(ValueError):
        service.renew(item, lease_seconds=True)
    with pytest.raises(ValueError):
        service.renew(item, lease_seconds=3_601)
    item = service.renew(item)
    assert item.fencing_token == 2
    service.assert_current_claim(item)
    with pytest.raises(CompanionConflict):
        service.mark_sent(replace(item, claim_owner="worker-b"))
    service.mark_sent(item)
    assert service.outbox_row("c", item.outbox_id)["status"] == OUTBOX_SENT


def test_expired_send_becomes_unknown_and_reconcile_checks_rowcount(svc):
    service, clock = svc
    publish(service)
    item = claim(service)
    clock.value = item.lease_deadline
    assert service.recover_expired_claims("foundry") == 1
    assert service.outbox_row("c", item.outbox_id)["status"] == OUTBOX_DELIVERY_UNKNOWN
    with pytest.raises(ValueError):
        service.reconcile_delivery_unknown(
            "c", item.outbox_id, confirmed="not_delivered", evidence=""
        )
    service.reconcile_delivery_unknown(
        "c",
        item.outbox_id,
        confirmed="not_delivered",
        evidence="relay audit",
    )
    assert service.outbox_row("c", item.outbox_id)["status"] == OUTBOX_DEAD
    with pytest.raises(CompanionConflict):
        service.reconcile_delivery_unknown(
            "c", item.outbox_id, confirmed="sent", evidence="duplicate audit"
        )


def test_retry_transition_uses_current_db_attempts_and_safe_flag(svc):
    service, _clock = svc
    publish(service)
    item = claim(service)
    unsafe = RetryableTransportError(
        "post-send",
        safe_to_retry=True,
        request_started=True,
    )
    with pytest.raises(ValueError, match="proven pre-send"):
        service.mark_retryable_failure(item, unsafe)
    assert service.outbox_row("c", item.outbox_id)["status"] != OUTBOX_PENDING

    with service.engine._write_db() as db:
        db.execute(
            "UPDATE we_companion_outbox SET attempts=5 "
            "WHERE campaign_id=? AND outbox_id=?",
            (item.campaign_id, item.outbox_id),
        )
    safe = RetryableTransportError("local busy", safe_to_retry=True)
    service.mark_retryable_failure(item, safe)
    assert service.outbox_row("c", item.outbox_id)["status"] == OUTBOX_DEAD


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:3010",
        "https://example.test",
        "http://2130706433:3010",
        "http://127.0.0.1:3010/path",
        "http://127.0.0.1:3010?x=1",
        "http://user@127.0.0.1:3010",
        "http://192.168.1.5:3010",
    ],
)
def test_foundry_origin_is_strict_literal_loopback_root(base_url):
    with pytest.raises(ValueError):
        FoundryConfig(base_url=base_url)


def test_foundry_config_bounds_and_secret_repr():
    assert "secret" not in repr(FoundryConfig(api_key="secret"))
    FoundryConfig(base_url="http://127.255.255.255:3010")
    FoundryConfig(base_url="http://[::1]:3010")
    for kwargs in (
        {"api_key": "bad\r\nheader"},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"max_request_bytes": True},
        {"max_response_bytes": 5_000_000},
    ):
        with pytest.raises(ValueError):
            FoundryConfig(**kwargs)


def test_snapshot_is_typed_disabled_and_claim_route_is_exact(svc):
    service, _clock = svc
    with pytest.raises(PermanentDisabled):
        service.enqueue_snapshot({})
    publish(service)
    item = claim(service)
    bridge = FoundryBridge(FoundryConfig())
    with pytest.raises(PermanentTransportError, match="event_type"):
        bridge.prepare_presentation(replace(item, event_type="snapshot.published"))


class _HTTPErrorOpener:
    def open(self, *_args, **_kwargs):
        raise urllib.error.HTTPError(
            "http://127.0.0.1:3010/chat", 429, "busy", {}, None
        )


def test_http_429_after_post_is_unknown_not_retryable(svc):
    service, _clock = svc
    publish(service)
    item = claim(service)
    bridge = FoundryBridge(FoundryConfig())
    bridge._opener = _HTTPErrorOpener()
    prepared = bridge.prepare_presentation(item)
    with pytest.raises(AmbiguousTransportError, match="delivery unknown"):
        bridge._send_prepared(item, prepared)


def test_static_security_and_atomicity_contract():
    source_path = (
        Path(__file__).resolve().parents[1] / "world_engine" / "companion.py"
    )
    source = source_path.read_text(encoding="utf8")
    ast.parse(source)
    for needle in (
        "ProxyHandler({})",
        "delivery_unknown",
        "ON CONFLICT(campaign_id, provider, idempotency_key) DO NOTHING",
        "assert_current_claim",
        "install_companion_schema_db",
        "diagnostics only",
    ):
        assert needle in source
    assert "executescript(" not in source
    assert "socket.getaddrinfo" not in source
