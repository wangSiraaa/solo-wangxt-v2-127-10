"""Pydantic response/request schemas (no frontend, JSON API only)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class AddressOut(BaseModel):
    display_name: str
    address: str
    raw: str


class DefectOut(BaseModel):
    stage: str
    level: str
    message: str


class AttachmentSummary(BaseModel):
    mime_path: str
    filename: str | None = None
    content_type: str | None = None
    stored: bool
    storage_path: str | None = None
    byte_size: int
    sha256: str | None = None
    error: str | None = None


class IngestResponse(BaseModel):
    ingest_id: int
    message_pk: int | None
    status: str
    raw_sha256: str
    raw_size: int
    defects_count: int
    fatal_error: str | None = None
    attachments: list[AttachmentSummary]
    threads: dict[str, Any] = Field(default_factory=dict)


class MessageSummary(BaseModel):
    id: int
    ingest_id: int
    message_id: str | None
    subject: str | None
    date: datetime | None
    from_json: list[dict[str, Any]]
    thread_key: str | None
    raw_sha256: str
    missing_id: bool


class BodyOut(BaseModel):
    mime_path: str
    content_type: str
    charset: str | None
    declared_charset: str | None
    disposition: str
    content_id: str | None
    byte_size: int
    text: str | None = None
    safe_html: str | None = None
    escaped_html: str | None = None
    plain_text: str | None = None
    referenced_cids: list[str] = Field(default_factory=list)


class AttachmentOut(BaseModel):
    id: int
    message_pk: int
    mime_path: str
    content_type: str
    filename: str | None
    raw_filename: str | None
    content_id: str | None
    byte_size: int
    checksum_sha256: str
    storage_path: str | None
    stored: bool


class HeaderOut(BaseModel):
    ordinal: int
    name: str
    value: str
    raw_value: str


class MessageDetail(MessageSummary):
    raw_subject: str | None
    to_json: list[dict[str, Any]]
    cc_json: list[dict[str, Any]]
    bcc_json: list[dict[str, Any]]
    reply_to_json: list[dict[str, Any]]
    sender_json: list[dict[str, Any]]
    tree_json: dict[str, Any] | None
    raw_path: str | None
    bodies: list[BodyOut]
    attachments: list[AttachmentOut]
    defects: list[DefectOut]


class SearchResponse(BaseModel):
    query: str
    count: int
    results: list[dict[str, Any]]


class FailureOut(BaseModel):
    ingest_id: int
    received_at: datetime
    source_name: str | None
    status: str
    raw_sha256: str
    raw_size: int
    fatal_error: str | None
    defect_count: int
    defects: list[DefectOut]


class ThreadSummary(BaseModel):
    thread_key: str
    message_count: int
    started_at: datetime | None
    last_at: datetime | None
    has_missing_id: bool
    distinct_message_ids: int


class ThreadDetail(BaseModel):
    thread_key: str
    messages: list[dict[str, Any]]


class IngestDetail(BaseModel):
    id: int
    received_at: datetime
    source_name: str | None
    status: str
    raw_sha256: str
    raw_size: int
    raw_path: str | None
    fatal_error: str | None
    defect_count: int
    defects: list[DefectOut]
    message_pk: int | None


class Health(BaseModel):
    status: str
    backend: str
    storage_roots: dict[str, str]


# ---- case dockets ---------------------------------------------------------
# Dockets are manual curation. Actor names are free-form operator signatures
# (legal staff); they are trimmed and required non-empty on every mutation so
# the audit trail always says who changed a relation.


def _nonempty_actor(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("actor must be a non-empty operator signature")
    return value


class DocketCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    actor: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def _trim_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("docket name must not be empty")
        return v

    @field_validator("actor")
    @classmethod
    def _trim_actor(cls, v: str) -> str:
        return _nonempty_actor(v)


class DocketEntryAdd(BaseModel):
    message_pk: int = Field(gt=0)
    note: str | None = Field(default=None, max_length=4000)
    actor: str = Field(min_length=1, max_length=200)

    @field_validator("actor")
    @classmethod
    def _trim_actor(cls, v: str) -> str:
        return _nonempty_actor(v)


class DocketNotePatch(BaseModel):
    note: str | None = Field(default=None, max_length=4000)
    actor: str = Field(min_length=1, max_length=200)

    @field_validator("actor")
    @classmethod
    def _trim_actor(cls, v: str) -> str:
        return _nonempty_actor(v)


class DocketRemove(BaseModel):
    actor: str = Field(min_length=1, max_length=200)

    @field_validator("actor")
    @classmethod
    def _trim_actor(cls, v: str) -> str:
        return _nonempty_actor(v)


class IngestRef(BaseModel):
    id: int
    received_at: datetime
    source_name: str | None
    status: str
    raw_sha256: str
    raw_size: int
    raw_path: str | None
    defect_count: int


class DocketEntryOut(BaseModel):
    id: int
    docket_id: int
    message_pk: int
    note: str | None
    added_by: str
    added_at: datetime
    message: dict[str, Any]
    ingest: IngestRef


class DocketSummary(BaseModel):
    id: int
    name: str
    description: str | None
    created_by: str
    created_at: datetime
    message_count: int


class DocketDetail(DocketSummary):
    entries: list[DocketEntryOut]


class DocketRemovalOut(BaseModel):
    docket_id: int
    message_pk: int
    removed: bool
    removed_by: str
    removed_at: datetime


class DocketEventOut(BaseModel):
    id: int
    docket_id: int
    message_pk: int | None
    action: str
    note: str | None
    actor: str
    occurred_at: datetime
