"""Repository interfaces used by the service layer.

Two implementations exist: :mod:`app.pg_repository` (PostgreSQL) and
:mod:`app.memory_repository` (tests / ephemeral runs). The API only depends on
this interface, so business logic is testable without a database.
"""
from __future__ import annotations

from typing import Any, Protocol, Sequence

from app.parser.models import ParsedMessage


class RepositoryError(Exception):
    """Base class for repository-level lookups the API maps to HTTP codes."""


class DocketNotFound(RepositoryError):
    def __init__(self, docket_id: int) -> None:
        self.docket_id = docket_id
        super().__init__(f"docket {docket_id} not found")


class MessageNotFound(RepositoryError):
    def __init__(self, message_pk: int) -> None:
        self.message_pk = message_pk
        super().__init__(f"message {message_pk} not found")


class DocketEntryConflict(RepositoryError):
    """Raised when adding a message that already belongs to the docket."""


class DocketEntryNotFound(RepositoryError):
    """Raised when patching/removing a membership that is not active."""


class Repository(Protocol):
    def init_schema(self) -> None: ...

    def save_ingest(
        self,
        parsed: ParsedMessage,
        *,
        raw_relpath: str | None,
        stored_attachments: Sequence[tuple[Any, str]],
        source_name: str | None,
        status: str,
        fatal_error: str | None,
    ) -> dict[str, Any]:
        """Persist one EML (headers, parts, attachments metadata, defects).

        Returns ``{"ingest_id": int, "message_id": int | None}``. Failed parses
        get an ingest row with message_id=None.
        """
        ...

    def rebuild_threads(self) -> dict[str, Any]:
        """Recompute all thread assignments from stored headers."""
        ...

    def get_message(self, message_pk: int) -> dict[str, Any] | None: ...
    def get_ingest(self, ingest_id: int) -> dict[str, Any] | None: ...
    def list_messages(self, limit: int, offset: int) -> list[dict[str, Any]]: ...
    def search_messages(self, query: str, limit: int, offset: int) -> dict[str, Any]: ...
    def get_thread(self, thread_key: str) -> dict[str, Any] | None: ...
    def list_threads(self, limit: int, offset: int) -> list[dict[str, Any]]: ...
    def list_failures(self, limit: int, offset: int) -> list[dict[str, Any]]: ...
    def get_attachment(self, attachment_id: int) -> dict[str, Any] | None: ...
    def get_attachment_by_message(self, message_pk: int, attachment_id: int) -> dict[str, Any] | None: ...

    # -- case dockets (manual curation; never touches threads/raw data) ----
    def create_docket(self, name: str, description: str | None, actor: str) -> dict[str, Any]:
        """Create a manual case docket; logs a ``created`` history event."""
        ...

    def get_docket(self, docket_id: int) -> dict[str, Any] | None:
        """Docket with active entries, each joined to its message summary and
        the ingest summary of the message's original ingest."""
        ...

    def list_dockets(self, limit: int, offset: int) -> list[dict[str, Any]]: ...

    def add_docket_entry(
        self, docket_id: int, message_pk: int, note: str | None, actor: str
    ) -> dict[str, Any]:
        """Add a message to a docket (idempotency is *not* automatic:
        re-adding an active member raises :class:`DocketEntryConflict`).
        Logs an ``added`` event."""
        ...

    def update_docket_entry_note(
        self, docket_id: int, message_pk: int, note: str | None, actor: str
    ) -> dict[str, Any]:
        """Replace the per-membership note; logs ``note_updated``."""
        ...

    def remove_docket_entry(self, docket_id: int, message_pk: int, actor: str) -> dict[str, Any]:
        """End a membership (soft delete: row and history are retained).
        Logs a ``removed`` event. Does not delete the message or its bytes."""
        ...

    def list_docket_events(self, docket_id: int, limit: int, offset: int) -> list[dict[str, Any]]:
        """Audit trail of membership changes with timestamps and actors."""
        ...
