"""Case dossier checks against the real PostgreSQL schema/SQL.

Besides behavior parity with the memory backend these cover the persistence
acceptance criterion: dossiers, memberships and the full change history must
survive a repository restart (a brand-new connection over the same DSN).
"""
import psycopg
import pytest

from conftest import SAMPLES
from app.pg_repository import PgRepository

pytestmark = pytest.mark.pg


def _ingest(c, name):
    r = c.post(
        "/ingest",
        files={"file": (name, (SAMPLES / name).read_bytes(), "message/rfc822")},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_dossier_full_flow_pg(pg_client):
    c, _ = pg_client
    a = _ingest(c, "01_multibyte.eml")
    b = _ingest(c, "09_html_xss.eml")
    pk_a, pk_b = a["message_pk"], b["message_pk"]

    d = c.post(
        "/dossiers",
        json={"name": "PG-CASE-1", "description": "postgres case", "actor": "alice"},
    ).json()
    assert d["created_by"] == "alice"

    # same message in two dossiers, independent notes
    d2 = c.post("/dossiers", json={"name": "PG-CASE-2", "actor": "bob"}).json()
    assert c.post(
        f"/dossiers/{d['id']}/messages/{pk_a}", json={"note": "A-note", "actor": "alice"}
    ).status_code == 201
    assert c.post(
        f"/dossiers/{d2['id']}/messages/{pk_a}", json={"note": "B-note", "actor": "bob"}
    ).status_code == 201
    assert c.post(
        f"/dossiers/{d['id']}/messages/{pk_b}", json={"actor": "alice"}
    ).status_code == 201

    assert c.post(
        f"/dossiers/{d['id']}/messages/{pk_a}", json={"actor": "x"}
    ).status_code == 409

    listing = c.get(f"/dossiers/{d['id']}/messages").json()
    assert listing["dossier"]["message_count"] == 2
    by_pk = {e["message"]["id"]: e for e in listing["messages"]}
    assert set(by_pk) == {pk_a, pk_b}
    assert by_pk[pk_a]["note"] == "A-note"
    # the second dossier's independent note is intact
    note_b = c.get(f"/dossiers/{d2['id']}/messages").json()["messages"][0]
    assert note_b["note"] == "B-note"
    # ingest summary with provenance and defects is embedded
    ing = by_pk[pk_a]["ingest"]
    assert ing["ingest_id"] == a["ingest_id"]
    assert ing["raw_sha256"] == a["raw_sha256"]
    assert ing["raw_path"] is not None
    assert "defects" in ing and ing["defect_count"] >= 0

    # audit trail
    events = c.get(f"/dossiers/{d['id']}/events").json()["events"]
    assert [e["event_type"] for e in events] == ["added", "added", "created"]
    assert all(e["occurred_at"] and e["actor"] for e in events)


def test_removal_preserves_search_attachment_and_thread_pg(pg_client):
    c, _ = pg_client
    a = _ingest(c, "01_multibyte.eml")
    pk_a = a["message_pk"]
    c.post("/threads/rebuild")
    thread_before = c.get(f"/messages/{pk_a}").json()["thread_key"]
    atts = c.get(f"/messages/{pk_a}").json()["attachments"]
    assert atts, "01_multibyte.eml carries attachments"

    d = c.post("/dossiers", json={"name": "RM", "actor": "alice"}).json()
    c.post(f"/dossiers/{d['id']}/messages/{pk_a}", json={"note": "n", "actor": "alice"})

    assert c.delete(f"/dossiers/{d['id']}/messages/{pk_a}?actor=bob").status_code == 200
    assert c.get(f"/dossiers/{d['id']}/messages").json()["messages"] == []

    # search, detail, attachment bytes and Message-ID thread all unchanged
    assert c.get("/search", params={"q": "multi-01"}).json()["count"] == 1
    assert c.get(f"/messages/{pk_a}").status_code == 200
    assert c.get(f"/messages/{pk_a}").json()["thread_key"] == thread_before
    for att in atts:
        dl = c.get(f"/messages/{pk_a}/attachments/{att['id']}/download")
        assert dl.status_code == 200
        assert dl.content

    # removal event is retained in history
    events = c.get(f"/dossiers/{d['id']}/events").json()
    types = [(e["event_type"], e["actor"]) for e in events["events"]]
    assert ("removed", "bob") in types
    assert types[-1] == ("created", "alice")


def test_dossiers_survive_repository_restart_pg(pg_dsn):
    # First "process": build state through one repository instance.
    repo = PgRepository(pg_dsn)
    repo.init_schema()
    # ingest one message directly so the test needs no HTTP client
    data = (SAMPLES / "01_multibyte.eml").read_bytes()
    from app.parser import parse_eml

    parsed = parse_eml(data)
    saved = repo.save_ingest(
        parsed,
        raw_relpath="raw/test.eml",
        stored_attachments=[],
        source_name="01_multibyte.eml",
        status=parsed.status.value,
        fatal_error=parsed.fatal_error,
    )
    pk = saved["message_id"]

    dossier = repo.create_dossier("PERSIST", description="keep me", actor="alice")
    repo.add_dossier_message(dossier["id"], pk, note="persisted note", actor="alice")
    repo.update_dossier_note(dossier["id"], pk, note="edited", actor="bob")
    del repo  # drop every connection / in-process state

    # Simulate a service restart: a fresh repository instance over the same DSN.
    fresh = PgRepository(pg_dsn)
    fresh.init_schema()  # DDL must be idempotent over the new tables
    got = fresh.get_dossier(dossier["id"])
    assert got is not None
    assert got["name"] == "PERSIST"
    assert got["description"] == "keep me"
    assert got["created_by"] == "alice"
    assert got["message_count"] == 1

    listing = fresh.list_dossier_messages(dossier["id"])
    assert len(listing["messages"]) == 1
    entry = listing["messages"][0]
    assert entry["message"]["id"] == pk
    assert entry["note"] == "edited"
    assert entry["added_by"] == "alice"
    assert entry["ingest"]["raw_sha256"]
    assert entry["ingest"]["source_name"] == "01_multibyte.eml"

    # full change history, including the superseded note value
    history = fresh.list_dossier_events(dossier["id"], 100, 0)
    types = [e["event_type"] for e in history["events"]]
    assert types == ["note_updated", "added", "created"]
    edited_event = next(e for e in history["events"] if e["event_type"] == "note_updated")
    assert edited_event["actor"] == "bob" and edited_event["note"] == "edited"
    added_event = next(e for e in history["events"] if e["event_type"] == "added")
    assert added_event["note"] == "persisted note" and added_event["actor"] == "alice"

    # the dossier list view also comes back after restart
    assert any(x["id"] == dossier["id"] for x in fresh.list_dossiers(50, 0))


def test_dossier_does_not_touch_thread_columns_pg(pg_client, pg_dsn):
    c, _ = pg_client
    pk = _ingest(c, "05_same_subject_root.eml")["message_pk"]
    other = _ingest(c, "05_same_subject_other.eml")["message_pk"]
    c.post("/threads/rebuild")
    d = c.post("/dossiers", json={"name": "NO-AUTO", "actor": "a"}).json()
    c.post(f"/dossiers/{d['id']}/messages/{pk}", json={"actor": "a"})

    # direct SQL: exactly one membership exists and the other message's
    # Message-ID-derived thread is unaffected / not auto-joined
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM dossier_messages")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT thread_key FROM messages WHERE id = %s", (other,))
        other_thread = cur.fetchone()[0]
        cur.execute("SELECT thread_key FROM messages WHERE id = %s", (pk,))
        member_thread = cur.fetchone()[0]
    assert other_thread != member_thread
