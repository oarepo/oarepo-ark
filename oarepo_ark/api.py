# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""Extension point: per-service ARK behaviour (`ARKHandler`) and `?info` metadata (`ERC`).

A handler knows how records of *one service* map to ARKs and back: which blade a record gets,
how the service reads the ARK's object, and where the landing page, the API, the variants
(`.json`) and the components (`/files/...`) of a result item are. Everything ARK-generic -
shoulders, check characters, normalization, PIDStore, permission states, HTTP - is done by
the extension and the resolver, so handlers stay small.

## Choosing the handler

[`OARepoARK.handler`][oarepo_ark.ext.OARepoARK.handler] takes `ARK_HANDLERS[service_id]`, else
`ARK_DEFAULT_HANDLER` (default [`RDMRecordHandler`][oarepo_ark.handlers.rdm.RDMRecordHandler]).
Both are a handler *class* or its import string; the extension creates and caches one instance
per service id (`handler_cls(service_id)`), so a handler must not keep request state.

```{.python}
# invenio.cfg
ARK_HANDLERS = {
    "theses": "my_repo.ark:ThesisHandler",  # import string
    "datasets": DatasetHandler,  # or the class itself
}
```

## When each method is called

- `get_blade(record)` - by `OARepoARK.compute_ark` on publish (with the *draft*) and by
  `invenio ark mint`. Returns a blade matching [`BLADE_RE`][oarepo_ark.ark.BLADE_RE]; the
  shoulder and the check character are added around it.
- `read(identity, object_uuid)` - by the resolver on every resolution. Returns the service
  result item; permission and deletion errors propagate (the resolver maps them to states).
- `landing_url(item)` - `/ark:...` with `Accept: text/html` (the default), and tombstones.
- `api_url(item)` - `/ark:...` with `Accept: application/json`.
- `variant_url(item, variant)` - `/ark:....json` gives `variant="json"`; `None` means 404.
- `component_url(item, path)` - `/ark:.../files/a.csv` gives the raw `path="/files/a.csv"`;
  `None` means 404.
- `erc(item)` - `?info` of a record the caller may read; returns an `ERC`.

The blade must never change for a published record: the ARK is computed from it once, stored in
PIDStore and then only looked up. Changing `get_blade` affects only records published later.

## Examples

`?info` metadata for a model whose metadata is not RDM-shaped:

```{.python}
from oarepo_ark.api import ERC, UNAV
from oarepo_ark.handlers.rdm import RDMRecordHandler


class ThesisHandler(RDMRecordHandler):
    def erc(self, item):
        md = item.data.get("metadata", {})
        return ERC(
            who=md.get("author") or UNAV,
            what=md.get("title") or UNAV,
            when=(md.get("defense_date") or "")[:4] or UNAV,
            extra={"how": "thesis"},  # additional ANVL element
        )
```

Variants that are not the UI exports, and a component other than files:

```{.python}
class DatasetHandler(RDMRecordHandler):
    def variant_url(self, item, variant):
        if variant == "csv":
            return f"{item.links['self']}/export/csv"
        return super().variant_url(item, variant)

    def component_url(self, item, path):
        if path == "/versions":
            return item.links.get("versions")
        return super().component_url(item, path)
```

A blade other than the record id (must be stable and unique within the shoulder):

```{.python}
class UUIDHandler(RDMRecordHandler):
    def get_blade(self, record):
        return str(record.id)  # hyphens are dropped: 32 hex characters
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from invenio_pidstore.models import PersistentIdentifier
from invenio_records_resources.proxies import current_service_registry

if TYPE_CHECKING:
    from uuid import UUID

    from flask_principal import Identity
    from invenio_records.api import Record

UNAV = "(:unav)"
"""ERC code for "value unavailable", used for every element the handler cannot fill."""


@dataclass
class ERC:
    """Kernel metadata of ``?info`` (Electronic Resource Citation); ``where`` is added by the resolver.

    Values are plain strings; the ANVL serializer collapses whitespace, so multi-line values are safe.

    ```{.python}
    ERC(who="Brown, Troy", what="A Romans story", when="2020", extra={"how": "dataset"})
    ```
    """

    who: str = UNAV
    """Creator(s), e.g. ``"Brown, Troy; Troy Inc."``."""
    what: str = UNAV
    """Title."""
    when: str = UNAV
    """Date, e.g. ``"2020-06-01"`` or ``"2020"``."""
    extra: dict[str, str] = field(default_factory=dict)
    """Additional ANVL elements, serialized after the kernel ones."""


class ARKHandler:
    """Per-service behaviour; one instance per service id.

    Defaults for a ``RecordService`` whose result items carry ``links``: the record id as blade,
    landing page and API from the links, no variants or components, an ERC of ``(:unav)``.
    Subclasses override only what differs.
    """

    def __init__(self, service_id: str):
        """Create the handler of a service; the service is looked up in the registry when used."""
        self.service_id = service_id

    def __repr__(self) -> str:
        """Readable representation for CLI output and errors."""
        return f"{type(self).__name__}({self.service_id!r})"

    @property
    def service(self) -> Any:
        """The service from the registry."""
        return current_service_registry.get(self.service_id)

    def get_blade(self, record: Record) -> str:
        """Blade of the ARK; must match [`BLADE_RE`][oarepo_ark.ark.BLADE_RE], hyphens are stripped later."""
        return record.pid.pid_value  # ty: ignore[unresolved-attribute]

    def read(self, identity: Identity, object_uuid: UUID) -> Any:
        """object_uuid (from the ARK PID) -> service result item; the service checks permissions.

        The service reads by record id, so the record id is taken from the record's own PID (same
        object, the record class's PID type) - one small PIDStore query instead of loading the
        record twice.

        Raises:
            NoResultFound: the object has no record PID.
            PermissionDenied: from ``service.read``; the resolver answers "restricted".
            RecordDeletedException: from ``service.read``; the resolver answers "withdrawn".

        """
        pid_type = self.service.record_cls.pid.field._pid_type  # noqa: SLF001
        record_pid = PersistentIdentifier.query.filter_by(
            pid_type=pid_type, object_type="rec", object_uuid=object_uuid
        ).one()
        return self.service.read(identity, record_pid.pid_value)

    def landing_url(self, item: Any) -> str:
        """URL of the landing page."""
        return item.links["self_html"]

    def api_url(self, item: Any) -> str:
        """URL of the REST API representation."""
        return item.links["self"]

    def variant_url(self, item: Any, variant: str) -> str | None:  # noqa: ARG002
        """``json``, ``datacite-json``, ... -> URL or None (404)."""
        return None

    def component_url(self, item: Any, path: str) -> str | None:  # noqa: ARG002
        """``/files/data.csv`` -> URL or None (404)."""
        return None

    def erc(self, item: Any) -> ERC:  # noqa: ARG002
        """who/what/when of ``?info``; ``(:unav)`` unless overridden for the schema."""
        return ERC()
