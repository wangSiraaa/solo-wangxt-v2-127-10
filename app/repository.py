"""Repository interfaces used by the service layer.

Two implementations exist: :mod:`app.pg_repository` (PostgreSQL) and
:mod:`app.memory_repository` (tests / ephemeral runs). The API only depends on
this interface, so business logic is testable without a database.
"""
from __future__ import annotations

from typing import Any, Protocol, Sequence

from app.parser.models import ParsedMessage


class RepositoryError(Exception):
    """Base class for repository-level domain failures."""


class DossierNotFound(RepositoryError):
    """Raised when a dossier id does not exist."""


class MessageNotFound(RepositoryError):
    """Raised when a message primary key does not exist."""


class DossierConflict(RepositoryError):
    """Raised when a uniqueness/invariant rule is violated.

    E.g. creating a dossier with a name that already exists, or adding a
    message that is already a member of the dossier.
    """


class DossierEntryNotFound(RepositoryError):
    """Raised when a message is not a member of the given dossier."""


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

    # -- case dossiers -----------------------------------------------------
    # Dossiers are *manual* collections of messages for legal case work. They
    # never touch messages.thread_key (derived solely from Message-ID headers)
    # and never copy or delete message/attachment rows. Every membership and
    # note change appends an immutable, signed audit event.
    def create_dossier(self, name: str, *, description: str | None, actor: str) -> dict[str, Any]: ...
    def get_dossier(self, dossier_id: int) -> dict[str, Any] | None: ...
    def list_dossiers(self, limit: int, offset: int) -> list[dict[str, Any]]: ...
    def add_dossier_message(
        self, dossier_id: int, message_pk: int, *, note: str | None, actor: str
    ) -> dict[str, Any]: ...
    def remove_dossier_message(self, dossier_id: int, message_pk: int, *, actor: str) -> dict[str, Any]: ...
    def update_dossier_note(
        self, dossier_id: int, message_pk: int, *, note: str | None, actor: str
    ) -> dict[str, Any]: ...
    def list_dossier_messages(self, dossier_id: int) -> dict[str, Any]: ...
    def list_dossier_events(
        self, dossier_id: int, limit: int, offset: int
    ) -> dict[str, Any]: ...
