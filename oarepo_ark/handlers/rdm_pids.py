# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""ARK provider for invenio-rdm-records' PID machinery; it creates every ARK PID (on publish, `invenio ark mint`).

ARKs are an ordinary *managed, required* RDM PID scheme, like OAI ids: no remote registry, PIDs
are `REGISTERED` on creation, nothing is reserved. `finalize_app` registers
[`ark_pid_providers`][oarepo_ark.handlers.rdm_pids.ark_pid_providers] and
[`ark_pid_config`][oarepo_ark.handlers.rdm_pids.ark_pid_config] unless the deployment defines
its own `ark` provider / scheme.

## Record lifecycle (RDM's `PIDsComponent`)

| Action | ARK |
|---|---|
| publish, record public | created by `generate_id` -> `compute_ark(draft)`, stored in `pids.ark` |
| publish, record restricted | not created (RDM skips required PIDs); created on a later public publish |
| edit + publish | kept (taken from the published record; restored if the draft dropped it) |
| new version | new ARK (the draft's pids are cleared; no concept ARK) |
| delete (tombstone) | PID stays `REGISTERED` (soft delete); the resolver shows the tombstone |
| restore | unchanged |

RDM also queues its `register_or_update_pid` task for `ark` on publish; with this provider it
has nothing to do (`update` is a no-op). RDM's DataCite serializer sends every non-DOI PID as
an alternate identifier, so DOI registrations carry the ARK.

## Validation

[`ARKPIDProvider.validate`][oarepo_ark.handlers.rdm_pids.ARKPIDProvider.validate] runs on draft
create/update and publish. The draft's `pids` come from user input, so a supplied `pids.ark` is
accepted only if it is the ARK the repository computes for the record, or one PIDStore already
assigns to it (records minted under a previous shoulder); otherwise publishing fails with
`pids.ark: ARKs are assigned by the repository.` The scheme's `validator` additionally rejects
ARKs of other NAANs, and the `normalizer` stores the canonical form.

## Minting outside publish

[`assign_ark`][oarepo_ark.handlers.rdm_pids.assign_ark] (used by `invenio ark mint`) creates the
PID with the provider configured for the service, sets `pids.ark`, commits and reindexes in one
unit of work - no draft, no publish components, no tasks.

## Custom wiring

```{.python}
# invenio.cfg - ARKs only for drafts asking for one (pids.ark = {"provider": "ark"}),
# minted by a subclassed provider; finalize_app keeps both definitions
from invenio_rdm_records.config import RDM_PERSISTENT_IDENTIFIER_PROVIDERS, RDM_PERSISTENT_IDENTIFIERS
from my_repo.ark import MyARKProvider  # subclass of ARKPIDProvider
from oarepo_ark.ark import is_ark, normalize_ark

RDM_PERSISTENT_IDENTIFIER_PROVIDERS = [*RDM_PERSISTENT_IDENTIFIER_PROVIDERS, MyARKProvider("ark")]
RDM_PERSISTENT_IDENTIFIERS = {
    **RDM_PERSISTENT_IDENTIFIERS,
    "ark": {
        "providers": ["ark"],
        "required": False,
        "label": "ARK",
        "validator": is_ark,
        "normalizer": normalize_ark,
        "is_enabled": MyARKProvider.is_enabled,
    },
}
```
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from invenio_i18n import lazy_gettext as _
from invenio_pidstore.errors import PIDDoesNotExistError
from invenio_pidstore.models import PersistentIdentifier, PIDStatus
from invenio_rdm_records.services.pids.providers import PIDProvider
from invenio_records_resources.services.uow import RecordCommitOp, UnitOfWork

from ..ark import InvalidARKError, normalize_ark
from ..errors import ARKError
from ..proxies import current_ark

if TYPE_CHECKING:
    from flask import Flask

    from ..ext import OARepoARK


class ARKPIDProvider(PIDProvider):
    """Managed ARKs; RDM's ``PIDsComponent`` creates the PID on publish.

    There is no remote registry: PIDs are registered right away and ``reserve`` is a no-op
    (like RDM's OAI provider).
    """

    def __init__(self, name: str = "ark", **kwargs: Any):
        """Create the provider."""
        kwargs.setdefault("label", _("ARK"))
        super().__init__(name, pid_type="ark", default_status=PIDStatus.REGISTERED, managed=True, **kwargs)

    def generate_id(self, record: Any, **kwargs: Any) -> str:  # noqa: ARG002
        """Compute the ARK; RDM passes the draft, which has the same id and pid as the published record."""
        return current_ark.compute_ark(record)

    @classmethod
    def is_enabled(cls, app: Flask) -> bool:  # ty: ignore[invalid-method-override] - RDM calls it with the app
        """Return whether a NAAN is configured."""
        return bool(app.config.get("ARK_NAAN"))

    def reserve(self, pid: Any, **kwargs: Any) -> bool:  # noqa: ARG002
        """Nothing to reserve."""
        return True

    def validate(self, record: Any, identifier: str | None = None, provider: str | None = None, **kwargs: Any):  # noqa: ANN201
        """Validate a minted (or to be minted) ARK of the record.

        Without an identifier, report a missing shoulder etc. as a ``pids.ark`` error instead of
        failing in ``generate_id``. With one (the draft carries ``pids`` from user input), accept
        only the ARK the repository computes for this record, or one PIDStore already assigns to
        it (records minted before a shoulder change) - otherwise anyone could publish an arbitrary
        ARK of our NAAN through this managed provider.
        """
        try:
            computed = current_ark.compute_ark(record)
        except ARKError as e:
            if identifier is None or not _assigned_to(identifier, record):
                return False, [{"field": "pids.ark", "messages": [str(e)]}]
            computed = None
        if identifier is not None and _normalized(identifier) != computed and not _assigned_to(identifier, record):
            return False, [{"field": "pids.ark", "messages": [_("ARKs are assigned by the repository.")]}]
        return super().validate(record, identifier=identifier, provider=provider, **kwargs)


def _normalized(identifier: str) -> str | None:
    try:
        return normalize_ark(identifier)
    except InvalidARKError:
        return None


def _assigned_to(identifier: str, record: Any) -> bool:
    """Return whether PIDStore already has this ARK for this record."""
    if (ark := _normalized(identifier)) is None:
        return False
    try:
        return PersistentIdentifier.get("ark", ark).object_uuid == record.id
    except PIDDoesNotExistError:
        return False


def assign_ark(service: Any, record: Any) -> str:
    """Mint the ARK of a published record: create the PID, set ``pids.ark``, commit and reindex.

    One unit of work, the way RDM's own PID operations change records - no edit/publish, so no
    draft, no publish components and no ``register_or_update`` tasks for the other PIDs. The PID is
    created by the scheme's default provider configured for the service.
    """
    providers = service.config.pids_providers["ark"]
    provider = providers[providers["default"]]
    with UnitOfWork() as uow:
        pid = provider.create(record)
        record["pids"] = {
            **(record.get("pids") or {}),
            "ark": {"identifier": pid.pid_value, "provider": provider.name},
        }
        uow.register(RecordCommitOp(record, indexer=getattr(service, "indexer", None)))
        uow.commit()
    return pid.pid_value


def ark_pid_providers() -> list[PIDProvider]:
    """Return the providers of the ``ark`` scheme."""
    return [ARKPIDProvider("ark")]


def ark_pid_config(ext: OARepoARK) -> dict[str, Any]:
    """``RDM_PERSISTENT_IDENTIFIERS["ark"]``; ``required`` makes RDM create it on publish."""
    return {
        "providers": ["ark"],
        "required": True,
        "label": _("ARK"),
        "validator": ext.is_own_ark,
        "normalizer": normalize_ark,
        "is_enabled": ARKPIDProvider.is_enabled,
    }
