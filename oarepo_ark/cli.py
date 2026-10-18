# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""`invenio ark` commands: inspect the configuration and ARKs, mint ARKs for existing records.

## `invenio ark check`

Prints the effective configuration, validates it (exit 1 with the error if invalid) and lists
the services minting ARKs with their shoulder and handler:

```
$ invenio ark check
NAAN:               12345
Default shoulder:   r1
Shoulder overrides: -
Default handler:    oarepo_ark.handlers.rdm:RDMRecordHandler
Handler overrides:  -
Public resolver:    https://n2t.net/
erc-support:        who=Example Repository; what=Permanent: Stable Content:; when=(:unav); where=https://repo.example.org/

Services minting ARKs (shoulder, service, handler):
r1      records RDMRecordHandler('records')
```

(The service lines and the output of `show` and `mint` are tab separated.)

## `invenio ark show ARK`

What an ARK (any form: with NMA, `ark:/`, hyphens, qualifier) resolves to, without permission
checks; exit 1 if it is not stored here:

```
$ invenio ark show https://n2t.net/ark:12345/r1abcde12345p/files/a-b.csv
ark:        ark:12345/r1abcde12345p
qualifier:  /files/a-b.csv
status:     REGISTERED
object:     3f0c...  (record UUID)
handler:    RDMRecordHandler('records')
url:        https://n2t.net/ark:12345/r1abcde12345p
```

## `invenio ark mint`

Gives records published before oarepo-ark was installed (or while `ARK_NAAN` was unset) their
ARK. Output: one tab-separated line per record - service, record id, ARK, result (`minted`,
`skipped: has an ARK`, `skipped: restricted`, `skipped: deleted`, or `ERROR: ...` on stderr).

| Invocation | Records |
|---|---|
| `invenio ark mint` | all services minting ARKs |
| `invenio ark mint --service ccmm-dataset [--service ...]` | the given services |
| `invenio ark mint --service records abcde-12345 fghij-67890` | the given record ids |
| `invenio ark mint --service records --pid-type recid` | when the record class's `pid` field declares no PID type |

- Candidates come from PIDStore and the record table, not from search (which may be stale):
  registered record PIDs of the service's type whose object is in the service's table, not
  deleted and without an ARK PID - in keyset-paginated batches (see
  [`published_record_ids`][oarepo_ark.cli.published_record_ids]).
- Each record is minted in its own unit of work: ARK PID, `pids.ark`, commit, reindex - no
  draft, no publish, no DataCite update (the next DOI update carries the ARK). A failed record
  is rolled back, reported, and picked up again by the next run; the exit code is then 1.
- An open draft of the record is left alone; publishing it keeps the ARK (RDM restores
  required PIDs removed from the draft).
- Services without a determinable PID type (oarepo-rdm's facade `records` service) are skipped
  with a note when minting all services, and fail when named explicitly.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import click
from flask import current_app
from flask.cli import with_appcontext
from invenio_db import db
from invenio_pidstore.models import PersistentIdentifier, PIDStatus
from invenio_records_resources.proxies import current_service_registry
from sqlalchemy import exists
from sqlalchemy.orm import aliased

from .ark import InvalidARKError, split_ark
from .errors import ARKConfigurationError
from .handlers.rdm_pids import assign_ark
from .proxies import current_ark

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


@click.group()
def ark() -> None:
    """ARK identifiers."""


@ark.command()
@with_appcontext
def check() -> None:
    """Print the effective configuration, validate it and list the services minting ARKs."""
    if not current_ark.naan:
        click.echo("ARK_NAAN is not set, ARKs are disabled.")
        return
    config = current_app.config
    support = "; ".join(f"{key}={value}" for key, value in current_ark.support().items())
    for label, value in [
        ("NAAN", current_ark.naan),
        ("Default shoulder", config["ARK_DEFAULT_SHOULDER"] or "-"),
        ("Shoulder overrides", _mapping(config["ARK_SHOULDERS"])),
        ("Default handler", _import_path(config["ARK_DEFAULT_HANDLER"])),
        ("Handler overrides", _mapping({k: _import_path(v) for k, v in config["ARK_HANDLERS"].items()})),
        ("Public resolver", config["ARK_PUBLIC_RESOLVER"]),
        ("erc-support", support),
    ]:
        click.echo(f"{label + ':':<20}{value}")
    try:
        current_ark.validate_config()
    except ARKConfigurationError as e:
        raise click.ClickException(str(e)) from e
    click.echo("\nServices minting ARKs (shoulder, service, handler):")
    for service_id in current_ark.ark_service_ids():
        click.echo(f"{current_ark.shoulder(service_id)}\t{service_id}\t{current_ark.handler(service_id)!r}")


def _mapping(values: dict[str, str]) -> str:
    return ", ".join(f"{key}={value}" for key, value in values.items()) or "-"


def _import_path(handler: Any) -> str:
    return f"{handler.__module__}:{handler.__qualname__}" if isinstance(handler, type) else str(handler)


@ark.command()
@click.argument("value")
@with_appcontext
def show(value: str) -> None:
    """Show what an ARK resolves to."""
    try:
        naan, rest = split_ark(value)
    except InvalidARKError as e:
        raise click.ClickException(str(e)) from e
    hit = current_ark.lookup(naan, rest)
    if hit is None:
        raise click.ClickException(f"{value} is not stored here")
    pid, qualifier = hit
    click.echo(f"ark:\t{pid.pid_value}")
    click.echo(f"qualifier:\t{qualifier}")
    click.echo(f"status:\t{pid.status.name}")
    click.echo(f"object:\t{pid.object_uuid}")
    click.echo(f"handler:\t{current_ark.handler_for_pid(pid)!r}")
    click.echo(f"url:\t{current_ark.public_url(pid.pid_value)}")


@ark.command()
@click.option(
    "--service", "service_ids", multiple=True, help="Service of the records (repeatable; default: all minting ARKs)"
)
@click.option("--pid-type", help="PID type of the records (default: from the record class's pid field)")
@click.argument("record_ids", nargs=-1)
@with_appcontext
def mint(service_ids: tuple[str, ...], pid_type: str | None, record_ids: tuple[str, ...]) -> None:
    """Give existing published records their ARK; all records of the service(s) if no RECORD_IDS are given.

    Without --service: every service minting ARKs. Records that are deleted, restricted or already
    have an ARK are skipped. Output: service, record id, ARK, result.
    """
    if (record_ids or pid_type) and len(service_ids) != 1:
        raise click.ClickException("RECORD_IDS and --pid-type need exactly one --service")
    failed = False
    for service_id, service in _services_to_mint(service_ids).items():
        ids: Iterable[str] = record_ids
        if not record_ids:
            service_pid_type = pid_type or getattr(service.record_cls.pid.field, "_pid_type", None)
            if not service_pid_type and service_ids:
                raise click.ClickException(f"Cannot determine the PID type of {service_id}, use --pid-type")
            if not service_pid_type:
                # e.g. oarepo-rdm's global ``records`` service: owns no records, its RDMRecord.pid has no type
                click.echo(f"{service_id}: no PID type, skipped (use --service {service_id} --pid-type T)", err=True)
                continue
            ids = published_record_ids(service.record_cls.model_cls, service_pid_type)
        for record_id in ids:
            try:
                ark, result = mint_record(service, record_id)
            except Exception as e:  # noqa: BLE001 - report and continue with the next record
                db.session.rollback()
                failed = True
                click.echo(f"{service_id}\t{record_id}\t\tERROR: {e}", err=True)
                continue
            click.echo(f"{service_id}\t{record_id}\t{ark}\t{result}")
    if failed:
        sys.exit(1)


def _services_to_mint(service_ids: tuple[str, ...]) -> dict[str, Any]:
    """Given services, or every service minting ARKs."""
    if not service_ids:
        return current_ark.ark_services()
    try:
        return {service_id: current_service_registry.get(service_id) for service_id in service_ids}
    except KeyError as e:
        raise click.ClickException(f"Unknown service {e.args[0]}") from None


def published_record_ids(model_cls: Any, pid_type: str, batch_size: int = 1000) -> Iterator[str]:
    """Values of registered record PIDs of ``pid_type`` whose object is in ``model_cls`` and has no ARK.

    Read from PIDStore and the record table without loading records (search may be incomplete), in
    keyset-paginated batches: the caller commits between records, which would end a server-side
    cursor. The record-table condition keeps each service to its own records - e.g. oarepo-rdm's
    delegating ``records`` service has none, and model services may share a PID type.
    """
    pid, ark = PersistentIdentifier, aliased(PersistentIdentifier)
    has_ark = exists().where(ark.pid_type == "ark", ark.object_uuid == pid.object_uuid)
    in_table = exists().where(model_cls.id == pid.object_uuid, model_cls.is_deleted.is_(False))
    query = db.session.query(pid.id, pid.pid_value).filter(
        pid.pid_type == pid_type, pid.object_type == "rec", pid.status == PIDStatus.REGISTERED, in_table, ~has_ark
    )
    last_id = 0
    while batch := query.filter(pid.id > last_id).order_by(pid.id).limit(batch_size).all():
        last_id = batch[-1].id
        yield from (row.pid_value for row in batch)


def mint_record(service: Any, record_id: str) -> tuple[str, str]:
    """Mint the ARK of a published record with `assign_ark`; returns ``(ark, what happened)``.

    Skips what RDM's publish would not give a required PID: deleted and restricted records.
    """
    record = service.record_cls.pid.resolve(record_id)
    if ark := current_ark.ark_for_record(record):
        return ark, "skipped: has an ARK"
    deletion_status = getattr(record, "deletion_status", None)
    if deletion_status is not None and deletion_status.is_deleted:
        return "", "skipped: deleted"
    if (record.get("access") or {}).get("record") == "restricted":
        return "", "skipped: restricted"
    return assign_ark(service, record), "minted"
