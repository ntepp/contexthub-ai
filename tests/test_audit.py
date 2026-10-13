from src.audit import service as audit
from src.db.models import AuditEvent


def _seed(n: int = 5):
    for i in range(n):
        audit.record("test.event", details={"i": i, "score": 0.123456, "texte": "é"})


def test_chain_is_valid_and_detects_tampering(db):
    _seed()
    report = audit.verify_chain(db)
    assert report.valid and report.checked == 5

    event = db.query(AuditEvent).order_by(AuditEvent.id).offset(2).first()
    event.details = {"i": 999}
    db.commit()
    report = audit.verify_chain(db)
    assert not report.valid and report.broken_at == event.id


def test_deleting_a_middle_row_is_detected(db):
    _seed()
    middle = db.query(AuditEvent).order_by(AuditEvent.id).offset(2).first()
    db.delete(middle)
    db.commit()
    assert not audit.verify_chain(db).valid


def test_gdpr_redaction_keeps_chain_valid(db, admin_user):
    actor = audit.Actor(id=admin_user.id, email=admin_user.email, ip="10.0.0.1", user_agent="Firefox")
    audit.record("query.ask", actor, details={"question": "q"})
    assert audit.redact_actor(db, admin_user.id) == 1
    db.commit()
    event = db.query(AuditEvent).one()
    assert event.actor_email == "[utilisateur supprimé]" and event.ip is None
    assert audit.verify_chain(db).valid


def test_user_deletion_erases_account_and_redacts_audit(admin, member, member_user, db):
    member.get("/auth/me")
    resp = admin.delete(f"/admin/users/{member_user.id}")
    assert resp.status_code == 200
    assert resp.json()["audit_events_redacted"] >= 1  # the member's login event
    emails = {e["actor_email"] for e in admin.get("/admin/audit", params={"limit": 500}).json()["items"]}
    assert member_user.email not in emails
    assert admin.get("/admin/audit/verify").json()["valid"] is True


def test_export_csv_and_filters(admin, collection):
    resp = admin.get("/admin/audit/export.csv", params={"action": "collection."})
    assert resp.status_code == 200
    lines = resp.text.lstrip("﻿").strip().splitlines()
    assert lines[0].startswith("id;ts;actor_id")
    assert len(lines) == 2 and "collection.create" in lines[1]
    # exporting is itself audited
    assert admin.get("/admin/audit", params={"action": "audit.export"}).json()["total"] == 1


def test_retention_purge(db, monkeypatch):
    from datetime import timedelta

    from src.db.models import utcnow

    _seed(3)
    old = db.query(AuditEvent).order_by(AuditEvent.id).first()
    old.ts = utcnow() - timedelta(days=400)
    old.hash = audit.compute_hash(old.prev_hash, old)  # simulate an event genuinely written long ago
    db.commit()
    # re-chain the following event onto the rewritten hash so the fixture is consistent
    nxt = db.query(AuditEvent).order_by(AuditEvent.id).offset(1).first()
    nxt.prev_hash = old.hash
    nxt.hash = audit.compute_hash(nxt.prev_hash, nxt)
    third = db.query(AuditEvent).order_by(AuditEvent.id).offset(2).first()
    third.prev_hash = nxt.hash
    third.hash = audit.compute_hash(third.prev_hash, third)
    db.commit()

    assert audit.purge_older_than(db, 365) == 1
    db.commit()
    report = audit.verify_chain(db)
    assert report.valid and report.checked == 2  # the first surviving row is the new anchor
