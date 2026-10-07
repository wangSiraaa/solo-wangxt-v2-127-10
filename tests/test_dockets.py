"""Case docket (manual legal curation) behavior.

The same flows run against the in-memory repository and real PostgreSQL via
the parametrized ``any_client`` fixture; PostgreSQL additionally proves
restart durability in ``test_pg_docket_relations_and_history_survive_restart``.
"""
import pytest

from conftest import SAMPLES


def _ingest(c, name):
    r = c.post(
        "/ingest",
        files={"file": (name, (SAMPLES / name).read_bytes(), "message/rfc822")},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _create_docket(c, name="CASE-2026-001 contract dispute", actor="alice@legal", description=None):
    r = c.post("/dockets", json={"name": name, "description": description, "actor": actor})
    assert r.status_code == 201, r.text
    return r.json()


def _entry(docket, message_pk):
    hits = [e for e in docket["entries"] if e["message_pk"] == message_pk]
    assert len(hits) == 1
    return hits[0]


# --- creation / validation ------------------------------------------------

def test_create_docket_records_creation_event(any_client):
    c, _ = any_client
    d = _create_docket(c, description="v1")
    assert d["id"] > 0
    assert d["created_by"] == "alice@legal"
    assert d["message_count"] == 0 and d["entries"] == []
    history = c.get(f"/dockets/{d['id']}/history").json()
    assert len(history) == 1
    assert history[0]["action"] == "created"
    assert history[0]["actor"] == "alice@legal"
    assert history[0]["occurred_at"]
    # listing works
    listed = c.get("/dockets").json()
    assert any(x["id"] == d["id"] and x["message_count"] == 0 for x in listed)


def test_docket_name_and_actor_validated(any_client):
    c, _ = any_client
    assert c.post("/dockets", json={"name": "   ", "actor": "a"}).status_code == 422
    assert c.post("/dockets", json={"name": "x", "actor": "  "}).status_code == 422


# --- acceptance: same mail in two dockets, independent notes --------------

def test_message_can_belong_to_two_dockets_with_independent_notes(any_client):
    c, _ = any_client
    pk = _ingest(c, "01_multibyte.eml")["message_pk"]
    d1 = _create_docket(c, name="Docket A")
    d2 = _create_docket(c, name="Docket B", actor="bob@legal")

    r = c.put(f"/dockets/{d1['id']}/entries",
              json={"message_pk": pk, "note": "plaintiff exhibit", "actor": "alice@legal"})
    assert r.status_code == 201, r.text
    r = c.put(f"/dockets/{d2['id']}/entries",
              json={"message_pk": pk, "note": "reviewed by bob", "actor": "bob@legal"})
    assert r.status_code == 201, r.text

    d1 = c.get(f"/dockets/{d1['id']}").json()
    d2 = c.get(f"/dockets/{d2['id']}").json()
    assert d1["message_count"] == d2["message_count"] == 1
    assert _entry(d1, pk)["note"] == "plaintiff exhibit"
    assert _entry(d2, pk)["note"] == "reviewed by bob"
    assert _entry(d1, pk)["added_by"] == "alice@legal"
    assert _entry(d2, pk)["added_by"] == "bob@legal"

    # editing the note in A does not touch B
    r = c.patch(f"/dockets/{d1['id']}/entries/{pk}/note",
                json={"note": "updated exhibit note", "actor": "carol@legal"})
    assert r.status_code == 200, r.text
    d1, d2 = c.get(f"/dockets/{d1['id']}").json(), c.get(f"/dockets/{d2['id']}").json()
    assert _entry(d1, pk)["note"] == "updated exhibit note"
    assert _entry(d2, pk)["note"] == "reviewed by bob"

    # re-adding an active member is a conflict, not a silent overwrite
    dup = c.put(f"/dockets/{d1['id']}/entries",
                json={"message_pk": pk, "note": "other", "actor": "alice@legal"})
    assert dup.status_code == 409

    # each entry carries the message summary AND its original ingest summary
    e = _entry(d1, pk)
    assert e["message"]["id"] == pk
    assert e["message"]["message_id"] == "multi-01@example.com"
    assert e["ingest"]["status"] == "ok"
    assert e["ingest"]["raw_sha256"] == e["message"]["raw_sha256"]
    assert e["ingest"]["raw_size"] > 0 and e["ingest"]["raw_path"]


# --- acceptance: dockets never affect search / downloads / threads --------

def test_removing_from_docket_preserves_search_attachment_and_thread(any_client):
    c, _ = any_client
    # multibyte mail has real stored attachments; cycle mails prove threads.
    doc = _ingest(c, "01_multibyte.eml")
    pk = doc["message_pk"]
    for n in ["02_cycle_a.eml", "02_cycle_b.eml"]:
        _ingest(c, n)
    a = next(m for m in c.get("/search", params={"q": "cycle-a"}).json()["results"]
             if m["message_id"] == "cycle-a@example.com")
    thread_before = c.get(f"/messages/{pk}").json()["thread_key"]
    atts_before = c.get(f"/messages/{pk}").json()["attachments"]
    assert len(atts_before) == 2

    d = _create_docket(c)
    assert c.put(f"/dockets/{d['id']}/entries",
                 json={"message_pk": pk, "note": "x", "actor": "alice@legal"}).status_code == 201
    # also docket an unrelated mail, then remove the first
    c.put(f"/dockets/{d['id']}/entries",
          json={"message_pk": a["id"], "note": "y", "actor": "alice@legal"})

    r = c.request("DELETE", f"/dockets/{d['id']}/entries/{pk}",
                  json={"actor": "bob@legal"})
    assert r.status_code == 200, r.text
    assert r.json()["removed"] is True
    assert r.json()["removed_by"] == "bob@legal"

    # removed from docket listing...
    d = c.get(f"/dockets/{d['id']}").json()
    assert d["message_count"] == 1
    assert [e["message_pk"] for e in d["entries"]] == [a["id"]]

    # ...but search, thread membership and the raw message are untouched
    assert c.get("/search", params={"q": "multi-01"}).json()["count"] == 1
    assert c.get(f"/messages/{pk}").json()["thread_key"] == thread_before

    # every attachment of the removed mail still downloads with its bytes
    for att in atts_before:
        dl = c.get(f"/messages/{pk}/attachments/{att['id']}/download")
        assert dl.status_code == 200
        assert len(dl.content) == att["byte_size"]

    # same subject never auto-joins anything
    _ingest(c, "05_same_subject_root.eml")
    _ingest(c, "05_same_subject_other.eml")
    assert c.get(f"/dockets/{d['id']}").json()["message_count"] == 1

    # removing again / patching a removed membership is 404
    assert c.request("DELETE", f"/dockets/{d['id']}/entries/{pk}",
                     json={"actor": "bob@legal"}).status_code == 404
    assert c.patch(f"/dockets/{d['id']}/entries/{pk}/note",
                   json={"note": "z", "actor": "bob@legal"}).status_code == 404


def test_docket_operations_require_existing_parents(any_client):
    c, _ = any_client
    pk = _ingest(c, "01_multibyte.eml")["message_pk"]
    assert c.get("/dockets/999").status_code == 404
    r = c.put("/dockets/999/entries",
              json={"message_pk": pk, "note": None, "actor": "alice@legal"})
    assert r.status_code == 404
    d = _create_docket(c)
    r = c.put(f"/dockets/{d['id']}/entries",
              json={"message_pk": 99999, "note": None, "actor": "alice@legal"})
    assert r.status_code == 404
    assert c.get(f"/dockets/{d['id']}/history").json()[0]["action"] == "created"


# --- audit trail -----------------------------------------------------------

def test_history_records_every_change_with_timestamp_and_actor(any_client):
    c, _ = any_client
    pk = _ingest(c, "01_multibyte.eml")["message_pk"]
    d = _create_docket(c, actor="alice@legal")

    c.put(f"/dockets/{d['id']}/entries",
          json={"message_pk": pk, "note": "n1", "actor": "alice@legal"})
    c.patch(f"/dockets/{d['id']}/entries/{pk}/note",
            json={"note": "n2", "actor": "bob@legal"})
    c.request("DELETE", f"/dockets/{d['id']}/entries/{pk}",
              json={"actor": "carol@legal"})
    # re-add: a fresh membership with its own note; history is append-only
    c.put(f"/dockets/{d['id']}/entries",
          json={"message_pk": pk, "note": "n3", "actor": "bob@legal"})

    history = c.get(f"/dockets/{d['id']}/history").json()
    actions = [(h["action"], h["actor"]) for h in history]
    assert actions == [
        ("added", "bob@legal"),
        ("removed", "carol@legal"),
        ("note_updated", "bob@legal"),
        ("added", "alice@legal"),
        ("created", "alice@legal"),
    ]
    # note snapshots: the removed event preserves the note at removal time
    removed = next(h for h in history if h["action"] == "removed")
    assert removed["note"] == "n2"
    assert all(h["occurred_at"] for h in history)
    # events belong to this docket only
    other = _create_docket(c, name="other")
    assert [h["action"] for h in c.get(f"/dockets/{other['id']}/history").json()] == ["created"]

    # re-added membership is active again with the new note and added_by
    docket = c.get(f"/dockets/{d['id']}").json()
    e = _entry(docket, pk)
    assert e["note"] == "n3" and e["added_by"] == "bob@legal"


# --- PostgreSQL durability --------------------------------------------------

@pytest.mark.pg
def test_pg_docket_relations_and_history_survive_restart(pg_dsn, tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    def settings():
        return Settings(
            database_dsn=pg_dsn,
            attachment_dir=(tmp_path / "attachments").resolve(),
            raw_dir=(tmp_path / "raw").resolve(),
            max_upload_bytes=10 * 1024 * 1024,
            file_mode=0o600,
        )

    with TestClient(create_app(settings())) as c:
        pk = None
        r = c.post(
            "/ingest",
            files={"file": ("01.eml", (SAMPLES / "01_multibyte.eml").read_bytes(),
                            "message/rfc822")},
        )
        pk = r.json()["message_pk"]
        d = c.post("/dockets", json={"name": "Durable Case", "actor": "alice@legal"}).json()
        c.put(f"/dockets/{d['id']}/entries",
              json={"message_pk": pk, "note": "survives restart", "actor": "alice@legal"})
        c.patch(f"/dockets/{d['id']}/entries/{pk}/note",
                json={"note": "edited note", "actor": "bob@legal"})
        docket_id = d["id"]

    # simulate service restart: brand-new app/repository over the same DSN.
    # init_schema() is idempotent; no data is migrated or re-seeded.
    with TestClient(create_app(settings())) as c:
        d = c.get(f"/dockets/{docket_id}").json()
        assert d["name"] == "Durable Case"
        e = _entry(d, pk)
        assert e["note"] == "edited note"
        assert e["ingest"]["raw_sha256"] == e["message"]["raw_sha256"]
        actions = [(h["action"], h["actor"]) for h in c.get(f"/dockets/{docket_id}/history").json()]
        assert actions == [
            ("note_updated", "bob@legal"),
            ("added", "alice@legal"),
            ("created", "alice@legal"),
        ]
