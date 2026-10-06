"""Storage layer.

`TableRepository` keeps links in Azure Table Storage (production).
`InMemoryRepository` keeps them in a dict (unit tests and quick local demos).
Both expose the same small interface, so the service layer never knows which
one it is talking to.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol

PARTITION_KEY = "link"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Link:
    code: str
    url: str
    created_at: str = field(default_factory=utc_now)
    clicks: int = 0

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "url": self.url,
            "created_at": self.created_at,
            "clicks": self.clicks,
        }


class LinkExistsError(Exception):
    """Raised when a short code is already taken."""


class Repository(Protocol):
    def add(self, link: Link) -> None: ...
    def get(self, code: str) -> Optional[Link]: ...
    def increment_clicks(self, code: str) -> Optional[Link]: ...
    def delete(self, code: str) -> bool: ...


class InMemoryRepository:
    def __init__(self) -> None:
        self._links: dict[str, Link] = {}
        self._lock = threading.Lock()

    def add(self, link: Link) -> None:
        with self._lock:
            if link.code in self._links:
                raise LinkExistsError(link.code)
            self._links[link.code] = link

    def get(self, code: str) -> Optional[Link]:
        return self._links.get(code)

    def increment_clicks(self, code: str) -> Optional[Link]:
        with self._lock:
            link = self._links.get(code)
            if link:
                link.clicks += 1
            return link

    def delete(self, code: str) -> bool:
        with self._lock:
            return self._links.pop(code, None) is not None


class TableRepository:
    """Azure Table Storage implementation.

    Each link is one entity: PartitionKey="link", RowKey=<short code>.
    Click counts use optimistic concurrency (ETag) so two simultaneous
    redirects never overwrite each other's increment.
    """

    MAX_RETRIES = 5

    def __init__(self, connection_string: str, table_name: str = "links") -> None:
        from azure.data.tables import TableServiceClient

        service = TableServiceClient.from_connection_string(connection_string)
        self._table = service.create_table_if_not_exists(table_name)

    @staticmethod
    def _to_link(entity) -> Link:
        return Link(
            code=entity["RowKey"],
            url=entity["url"],
            created_at=entity.get("created_at", ""),
            clicks=int(entity.get("clicks", 0)),
        )

    def add(self, link: Link) -> None:
        from azure.core.exceptions import ResourceExistsError

        try:
            self._table.create_entity(
                {
                    "PartitionKey": PARTITION_KEY,
                    "RowKey": link.code,
                    "url": link.url,
                    "created_at": link.created_at,
                    "clicks": link.clicks,
                }
            )
        except ResourceExistsError as exc:
            raise LinkExistsError(link.code) from exc

    def get(self, code: str) -> Optional[Link]:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._to_link(self._table.get_entity(PARTITION_KEY, code))
        except ResourceNotFoundError:
            return None

    def increment_clicks(self, code: str) -> Optional[Link]:
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
        from azure.data.tables import UpdateMode

        for _ in range(self.MAX_RETRIES):
            try:
                entity = self._table.get_entity(PARTITION_KEY, code)
            except ResourceNotFoundError:
                return None
            entity["clicks"] = int(entity.get("clicks", 0)) + 1
            try:
                self._table.update_entity(
                    entity,
                    mode=UpdateMode.MERGE,
                    etag=entity.metadata["etag"],
                    match_condition=MatchConditions.IfNotModified,
                )
                return self._to_link(entity)
            except ResourceModifiedError:
                continue  # someone else updated it first; re-read and retry
        # Still contended after retries: serve the redirect anyway.
        return self.get(code)

    def delete(self, code: str) -> bool:
        if self.get(code) is None:
            return False
        self._table.delete_entity(PARTITION_KEY, code)
        return True
