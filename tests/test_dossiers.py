"""Case dossier acceptance tests (memory backend).

Dossiers are manual case files: the same message may live in several dossiers
with independent notes, removal never disturbs search/attachments/threads, and
every change carries a timestamp and the acting operator.
"""
from conftest import SAMPLES


def _ingest(c, name="01_multibyte.eml"):
    data = (SAMPLES / name).read_bytes()
    r = c.post(
        "/ingest",
        files={"file": (name, data, "message/rfc822")},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _make_dossier(c, name, actor="legal@example.com", description=None):
    r = c.post(
        "/dossiers",
        json={"name": name, "description": description, "actor": actor},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_create_and_list_dossiers(client):
    c, _ = client
    d1 = _make_dossier(c, "CASE-2026-001", actor="alice")
    d2 = _make_dossier(c, "CASE-2026-002", actor="bob", description="second")
    assert d1["message_count"] == 0
    assert d1["created_by"] == "alice"
    assert d2["description"] == "second"

    listing = c.get("/dossiers").json()
    assert {d["name"] for d in listing} == {"CASE-2026-001", "CASE-2026-002"}

    assert c.get(f"/dossiers/{d1['id']}").json()["name"] == "CASE-2026-001"
    assert c.get("/dossiers/9999").status_code == 404


def test_dossier_names_are_unique(client):
    c, _ = client
    _make_dossier(c, "DUP")
    r = c.post("/dossiers", json={"name": "DUP", "actor": "x"})
    assert r.status_code == 409


def test_same_message_in_two_dossiers_with_independent_notes(client):
    c, _ = client
    msg = _ingest(c)
    pk = msg["message_pk"]
    d1 = _make_dossier(c, "Case-A")
    d2 = _make_dossier(c, "Case-B")

    r1 = c.post(
        f"/dossiers/{d1['id']}/messages/{pk}",
        json={"note": "exhibit for A", "actor": "alice"},
    )
    assert r1.status_code == 201
    r2 = c.post(
        f"/dossiers/{d2['id']}/messages/{pk}",
        json={"note": "exhibit for B", "actor": "bob"},
    )
    assert r2.status_code == 201

    listing_a = c.get(f"/dossiers/{d1['id']}/messages").json()
    listing_b = c.get(f"/dossiers/{d2['id']}/messages").json()
    assert len(listing_a["messages"]) == len(listing_b["messages"]) == 1
    entry_a = listing_a["messages"][0]
    entry_b = listing_b["messages"][0]
    # same underlying message, two independent membership rows/notes
    assert entry_a["message"]["id"] == entry_b["message"]["id"] == pk
    assert entry_a["note"] == "exhibit for A"
    assert entry_b["note"] == "exhibit for B"
    assert entry_a["added_by"] == "alice"
    assert entry_b["added_by"] == "bob"

    # updating the note in one dossier must not touch the other
    upd = c.put(
        f"/dossiers/{d1['id']}/messages/{pk}/note",
        json={"note": "updated note for A", "actor": "carol"},
    )
    assert upd.status_code == 200
    assert upd.json()["messages"][0]["note"] == "updated note for A"
    still_b = c.get(f"/dossiers/{d2['id']}/messages").json()["messages"][0]
    assert still_b["note"] == "exhibit for B"


def test_duplicate_add_conflicts(client):
    c, _ = client
    pk = _ingest(c)["message_pk"]
    d = _make_dossier(c, "Case")
    c.post(f"/dossiers/{d['id']}/messages/{pk}", json={"actor": "a"})
    r = c.post(f"/dossiers/{d['id']}/messages/{pk}", json={"actor": "b"})
    assert r.status_code == 409
    # adding a non-existent message or to a missing dossier is 404
    assert c.post(f"/dossiers/{d['id']}/messages/9999", json={"actor": "a"}).status_code == 404
    assert c.post(f"/dossiers/9999/messages/{pk}", json={"actor": "a"}).status_code == 404


def test_listing_includes_message_summary_and_ingest_summary(client):
    c, _ = client
    ingest = _ingest(c)
    pk = ingest["message_pk"]
    d = _make_dossier(c, "Case")
    c.post(f"/dossiers/{d['id']}/messages/{pk}", json={"note": "n", "actor": "alice"})

    body = c.get(f"/dossiers/{d['id']}/messages").json()
    assert body["dossier"]["message_count"] == 1
    entry = body["messages"][0]
    # message summary
    assert entry["message"]["id"] == pk
    assert entry["message"]["message_id"] == "multi-01@example.com"
    assert entry["message"]["raw_sha256"] == ingest["raw_sha256"]
    # the *original ingest* summary: provenance facts of that exact ingestion
    summ = entry["ingest"]
    assert summ["ingest_id"] == ingest["ingest_id"]
    assert summ["raw_sha256"] == ingest["raw_sha256"]
    assert summ["raw_size"] == ingest["raw_size"]
    assert summ["status"] == ingest["status"]
    assert summ["raw_path"]
    assert summ["source_name"] == "01_multibyte.eml"
    assert isinstance(summ["defects"], list)


def test_removal_leaves_search_attachment_download_and_thread_intact(client):
    c, _ = client
    ingest = _ingest(c)
    pk = ingest["message_pk"]
    d = _make_dossier(c, "Case")
    c.post(f"/dossiers/{d['id']}/messages/{pk}", json={"note": "x", "actor": "alice"})

    # thread assignment before removal
    before = c.get(f"/messages/{pk}").json()
    thread_before = before["thread_key"]
    assert thread_before is not None
    att_id = before["attachments"][0]["id"]

    removed = c.delete(f"/dossiers/{d['id']}/messages/{pk}?actor=bob")
    assert removed.status_code == 200
    assert removed.json()["removed"] is True

    # dossier no longer lists the message
    after_list = c.get(f"/dossiers/{d['id']}/messages").json()
    assert after_list["messages"] == []
    assert after_list["dossier"]["message_count"] == 0

    # original search still finds it
    assert c.get("/search", params={"q": "multi-01@example.com"}).json()["count"] == 1
    # attachment download still serves the bytes
    dl = c.get(f"/messages/{pk}/attachments/{att_id}/download")
    assert dl.status_code == 200
    assert dl.content
    # thread assignment derived from Message-ID is untouched
    assert c.get(f"/messages/{pk}").json()["thread_key"] == thread_before
    # the message itself is still fully readable
    assert c.get(f"/messages/{pk}").status_code == 200
    assert c.get(f"/ingests/{ingest['ingest_id']}").status_code == 200

    # removing again is a 404 (entry absent)
    assert c.delete(f"/dossiers/{d['id']}/messages/{pk}?actor=bob").status_code == 404
    # note edits on a removed membership are 404 too
    assert c.put(
        f"/dossiers/{d['id']}/messages/{pk}/note", json={"note": "y", "actor": "z"}
    ).status_code == 404


def test_events_record_type_timestamp_and_actor(client):
    c, _ = client
    pk = _ingest(c)["message_pk"]
    d = _make_dossier(c, "Case", actor="alice")
    c.post(f"/dossiers/{d['id']}/messages/{pk}", json={"note": "v1", "actor": "alice"})
    c.put(f"/dossiers/{d['id']}/messages/{pk}/note", json={"note": "v2", "actor": "bob"})
    c.put(f"/dossiers/{d['id']}/messages/{pk}/note", json={"note": "v2", "actor": "bob"})
    c.delete(f"/dossiers/{d['id']}/messages/{pk}?actor=carol")

    events = c.get(f"/dossiers/{d['id']}/events").json()
    # newest first; idempotent second note update must NOT create an event
    types = [(e["event_type"], e["actor"], e["message_pk"]) for e in events["events"]]
    assert types == [
        ("removed", "carol", pk),
        ("note_updated", "bob", pk),
        ("added", "alice", pk),
        ("created", "alice", None),
    ]
    for e in events["events"]:
        assert e["occurred_at"]
    note_event = next(e for e in events["events"] if e["event_type"] == "note_updated")
    assert note_event["note"] == "v2"
    # the added event keeps the note as it was when the link was made
    added = next(e for e in events["events"] if e["event_type"] == "added")
    assert added["note"] == "v1"
    # count is the full history length, not just the page
    assert events["count"] == 4

    # events endpoint requires an existing dossier
    assert c.get("/dossiers/4242/events").status_code == 404


def test_dossier_is_not_a_thread_same_subject_not_auto_added(client):
    c, _ = client
    # two unrelated mails that only share a normalized subject
    _ingest(c, "05_same_subject_root.eml")
    other = _ingest(c, "05_same_subject_other.eml")
    other_pk = other["message_pk"]
    d = _make_dossier(c, "Subjects-Happen-To-Match")

    # explicitly add only the first; the second must not appear
    first = c.get("/search", params={"q": "Quarterly"}).json()["results"]
    first_pk = next(m["id"] for m in first if m["id"] != other_pk)
    c.post(f"/dossiers/{d['id']}/messages/{first_pk}", json={"actor": "a"})
    members = {e["message"]["id"] for e in c.get(f"/dossiers/{d['id']}/messages").json()["messages"]}
    assert members == {first_pk}
    assert other_pk not in members
    # and dossier membership does not alter thread separation
    keys = {c.get(f"/messages/{m['id']}").json()["thread_key"] for m in first}
    assert len(keys) == 2


def test_actor_is_required(client):
    c, _ = client
    d = _make_dossier(c, "Case")
    r = c.post("/dossiers", json={"name": "NoActor"})
    assert r.status_code == 422
    pk = _ingest(c)["message_pk"]
    assert c.post(f"/dossiers/{d['id']}/messages/{pk}", json={"note": "n"}).status_code == 422
    assert c.delete(f"/dossiers/{d['id']}/messages/{pk}").status_code == 422
