# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""The Flask extension `OARepoARK` (available as `current_ark`) and its `finalize_app` hook.

The extension is the single place that combines configuration, the service registry and
PIDStore; the PID provider, the resolver, the CLI and the templates all go through it.

## Responsibilities

- configuration: `naan`, `shoulder`, `validate_config`, `support`, `public_url`;
- services minting ARKs: `ark_services`, `ark_service_ids`, `service_id_for_record`;
- handlers: `handler`, `handler_for_pid`;
- ARKs: `compute_ark`, `ark_for_record`, `is_own_ark`, `lookup`.

## Which services mint ARKs

The `ark` scheme is global (`RDM_PERSISTENT_IDENTIFIERS`), so a service mints ARKs when its
config has the `ark` scheme in `pids_providers` **and** RDM's `PIDsComponent` (or a subclass)
among its components. The second condition excludes services that inherit RDM's PID config
without publishing, e.g. `record-media-files`. In oarepo-rdm, the global `records` facade
service qualifies too but owns no records; it is harmless (see `invenio ark check`).

## Records, services and handlers

- *record -> service* (minting): the service whose `record_cls` or `draft_cls` is **exactly**
  the record's class. Exact, because RDM passes the draft, and oarepo-rdm's facade service has
  base classes of all model classes.
- *ARK PID -> service* (resolving): the service whose record table contains the PID's
  `object_uuid`; services whose shoulder starts the name are probed first, so this is normally
  one primary-key query.
- *service -> handler*: `ARK_HANDLERS[service_id]`, else `ARK_DEFAULT_HANDLER`; one instance
  per service id.

## Caches

All caches live on the extension instance (one per app) and only grow:

- `ark_services()` - rebuilt when the number of registered services changes (services are only
  added, some at finalize time); evaluating `pids_providers` is not free.
- `service_id_for_record()` - hits per record class; misses are not cached, so a service
  registered later is still found.
- `handler()` - one handler per service id; configuration changes at runtime are not picked up.

## finalize_app

Runs at `invenio_base.(api_)finalize_app`, after all extensions set their defaults. If
`ARK_NAAN` is set it validates the configuration (raising
[`ARKConfigurationError`][oarepo_ark.errors.ARKConfigurationError] at startup) and adds,
unless already present:

```{.python}
RDM_PERSISTENT_IDENTIFIER_PROVIDERS += [ARKPIDProvider("ark")]
RDM_PERSISTENT_IDENTIFIERS["ark"] = {
    "providers": ["ark"],
    "required": True,  # RDM creates it on publish
    "label": _("ARK"),
    "validator": current_ark.is_own_ark,
    "normalizer": normalize_ark,
    "is_enabled": ARKPIDProvider.is_enabled,
}
```

New objects are assigned instead of mutating the lists/dicts, which may be invenio-rdm-records'
module-level defaults shared by every app in the process.

## Examples

```{.python}
from oarepo_ark.proxies import current_ark

# 'ark:12345/r1abcde12345p' - computed only, nothing is stored
current_ark.compute_ark(draft)
# pids.ark of a record, its UI dict or a service result item
current_ark.ark_for_record(item)
# 'https://n2t.net/ark:12345/r1abcde12345p'
current_ark.public_url("ark:12345/r1abcde12345p")

hit = current_ark.lookup("12345", "r1abcde-12345p/files/a-b.csv")
if hit:
    pid, qualifier = hit  # PersistentIdentifier, '/files/a-b.csv'
    handler = current_ark.handler_for_pid(pid)
```

Template globals registered by the extension:

```jinja
{% if ark_identifier(record_ui) %}
  <a href="{{ ark_url(record_ui) }}">{{ ark_identifier(record_ui) }}</a>
{% endif %}
```
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from invenio_base.utils import obj_or_import_string
from invenio_pidstore.models import PersistentIdentifier
from invenio_rdm_records.services.components import PIDsComponent
from invenio_records_resources.proxies import current_service_registry

from . import config
from .api import UNAV
from .ark import BLADE_RE, NAAN_RE, SHOULDER_RE, noid_check_char, normalize_ark, normalize_rest, split_ark, split_name
from .errors import ARKConfigurationError, ARKRecordError
from .handlers.rdm_pids import ark_pid_config, ark_pid_providers
from .proxies import current_ark

if TYPE_CHECKING:
    from flask import Flask
    from invenio_records.api import Record

    from .api import ARKHandler


class OARepoARK:
    """ARK minting (via the RDM PID provider) and resolution."""

    def __init__(self, app: Flask | None = None):
        """Extension initialization."""
        self._handlers: dict[str, ARKHandler] = {}
        self._service_ids: dict[type, str] = {}
        self._ark_services: tuple[int, dict[str, Any]] = (-1, {})
        if app:
            self.init_app(app)

    def init_app(self, app: Flask) -> None:
        """Flask application initialization."""
        self.app = app
        self.init_config(app)
        app.extensions["oarepo-ark"] = self
        app.add_template_global(ark_identifier, "ark_identifier")
        app.add_template_global(ark_url, "ark_url")

    def init_config(self, app: Flask) -> None:
        """Set default configuration."""
        for k in dir(config):
            if k.startswith("ARK_"):
                app.config.setdefault(k, getattr(config, k))

    # -- configuration ---------------------------------------------------------------------

    @property
    def naan(self) -> str | None:
        """Configured NAAN; ``None`` means the extension is inert.

        Always a string: invenio-config parses ``INVENIO_ARK_NAAN=12345`` into the integer 12345.
        """
        naan = self.app.config["ARK_NAAN"]
        return None if naan is None or naan == "" else str(naan)

    def support(self) -> dict[str, str]:
        """``erc-support`` of ``?info``: ``ARK_SUPPORT``, defaults from ``THEME_SITENAME`` and ``SITE_UI_URL``."""
        config = self.app.config["ARK_SUPPORT"]
        site_url = f"{self.app.config.get('SITE_UI_URL', '').rstrip('/')}/"
        return {
            "who": config.get("who") or self.app.config.get("THEME_SITENAME") or UNAV,
            "what": config.get("what") or UNAV,
            "when": config.get("when") or UNAV,
            "where": config.get("where") or site_url,
        }

    def public_url(self, ark: str) -> str:
        """Citable URL of an ARK."""
        return f"{self.app.config['ARK_PUBLIC_RESOLVER'].rstrip('/')}/{ark}"

    def validate_config(self) -> None:
        """NAAN shape; default shoulder set; all shoulders primordinal and unique (prefix-free)."""
        if not NAAN_RE.match(self.naan or ""):
            raise ARKConfigurationError(f"ARK_NAAN {self.naan!r} is not a 5 character NAAN")
        default = self.app.config["ARK_DEFAULT_SHOULDER"]
        if not default:
            raise ARKConfigurationError("ARK_DEFAULT_SHOULDER is required when ARK_NAAN is set")
        seen: dict[str, str] = {}
        for service_id, shoulder in [("ARK_DEFAULT_SHOULDER", default), *self.app.config["ARK_SHOULDERS"].items()]:
            if not SHOULDER_RE.match(shoulder or ""):
                raise ARKConfigurationError(f"{service_id}: shoulder {shoulder!r} is not primordinal (e.g. 'r1')")
            if shoulder in seen:
                raise ARKConfigurationError(f"{seen[shoulder]} and {service_id} share shoulder {shoulder!r}")
            seen[shoulder] = service_id

    def shoulder(self, service_id: str) -> str:
        """Shoulder of a service: the override in ``ARK_SHOULDERS``, else ``ARK_DEFAULT_SHOULDER``."""
        shoulder = self.app.config["ARK_SHOULDERS"].get(service_id) or self.app.config["ARK_DEFAULT_SHOULDER"]
        if not shoulder:  # only if the configuration was not validated (finalize_app)
            raise ARKConfigurationError("ARK_DEFAULT_SHOULDER is not set")
        return shoulder

    def is_own_shoulder(self, name: str) -> bool:
        """Return whether the (normalized) name starts with one of our shoulders; shoulders are prefix-free."""
        shoulders = [*self.app.config["ARK_SHOULDERS"].values(), self.app.config["ARK_DEFAULT_SHOULDER"]]
        return any(s and name.startswith(s) for s in shoulders)

    # -- services and handlers -------------------------------------------------------------

    def ark_services(self) -> dict[str, Any]:
        """Services minting ARKs: the ``ark`` scheme is configured and RDM's ``PIDsComponent`` runs on publish.

        The component check matters: other services inherit RDM's PID configuration without minting
        anything - e.g. ``record-media-files`` shares the record class's table; file services have none.

        Cached: evaluating ``config.pids_providers`` rebuilds the provider dict and calls every
        scheme's ``is_enabled``. Services are only ever added to the registry (some at finalize
        time), so the cache is rebuilt when the number of registered services changes.
        """
        services = current_service_registry._services  # noqa: SLF001
        if self._ark_services[0] != len(services):
            self._ark_services = (
                len(services),
                {service_id: service for service_id, service in services.items() if _mints_arks(service)},
            )
        return self._ark_services[1]

    def ark_service_ids(self) -> list[str]:
        """Ids of [`ark_services`][oarepo_ark.ext.OARepoARK.ark_services]."""
        return list(self.ark_services())

    def service_id_for_record(self, record: Record) -> str | None:
        """Service whose ``record_cls`` / ``draft_cls`` is exactly the record's type.

        Exact match: the provider gets the draft, and in oarepo-rdm the global ``records``
        service's classes are base classes of the model classes. Only hits are cached, so a
        service registered later is still found.
        """
        record_type = type(record)
        if record_type not in self._service_ids:
            service_id = next(
                (sid for sid, service in self.ark_services().items() if record_type in _record_classes(service)),
                None,
            )
            if service_id is None:
                return None
            self._service_ids[record_type] = service_id
        return self._service_ids[record_type]

    def handler(self, service_id: str) -> ARKHandler:
        """Return the handler of a service: ``ARK_HANDLERS[service_id]``, else ``ARK_DEFAULT_HANDLER``.

        Both name a handler class (or its import string); it is created with the service id.
        """
        if service_id not in self._handlers:
            configured = self.app.config["ARK_HANDLERS"].get(service_id) or self.app.config["ARK_DEFAULT_HANDLER"]
            handler_cls = obj_or_import_string(configured)
            if not isinstance(handler_cls, type):
                raise ARKConfigurationError(
                    f"ARK handler of {service_id!r} must be a handler class or its import string, not {configured!r}"
                )
            self._handlers[service_id] = handler_cls(service_id)
        return self._handlers[service_id]

    def handler_for_pid(self, pid: PersistentIdentifier) -> ARKHandler | None:
        """Return the handler of an ARK: the service whose record table holds the PID's object.

        One primary-key lookup per probed service, in this order: the service whose own shoulder
        (``ARK_SHOULDERS``) starts the name - normally the only probe -, then services sharing
        ``ARK_DEFAULT_SHOULDER``, then the rest (records minted before a shoulder change).
        """
        naan, name = split_ark(pid.pid_value)
        shoulders, default = self.app.config["ARK_SHOULDERS"], self.app.config["ARK_DEFAULT_SHOULDER"]

        def probe_order(service_id: str) -> int:
            if naan == self.naan and (shoulder := shoulders.get(service_id)):
                return 0 if name.startswith(shoulder) else 2
            return 1 if naan == self.naan and default and name.startswith(default) else 2

        for service_id in sorted(self.ark_services(), key=probe_order):
            model_cls = self.ark_services()[service_id].record_cls.model_cls
            if model_cls.query.with_entities(model_cls.id).filter_by(id=pid.object_uuid).first() is not None:
                return self.handler(service_id)
        return None

    # -- ARKs ------------------------------------------------------------------------------

    def compute_ark(self, record: Record) -> str:
        """Compute the ARK of a record (or its draft), with the NOID check character; stores nothing."""
        service_id = self.service_id_for_record(record)
        if service_id is None:
            raise ARKRecordError(f"{type(record).__name__} does not belong to a service using ARKs")
        blade = self.handler(service_id).get_blade(record)
        if not BLADE_RE.match(blade):
            raise ARKRecordError(f"Blade {blade!r} of service {service_id!r} contains structural characters")
        base = normalize_ark(f"ark:{self.naan}/{self.shoulder(service_id)}{blade}")
        return base + noid_check_char(base.removeprefix("ark:"))

    def ark_for_record(self, record: Any) -> str | None:
        """ARK of a record, its UI dict or a service result item, from ``pids``."""
        data = record if isinstance(record, dict) else record.data  # RecordItem: ``data`` is the dict
        return ((data.get("pids") or {}).get("ark") or {}).get("identifier")

    def is_own_ark(self, value: str) -> bool:
        """Return whether ``value`` is a valid ARK of ``ARK_NAAN`` (RDM ``pids.ark`` validator)."""
        try:
            return split_ark(normalize_ark(value))[0] == self.naan
        except ValueError:
            return False

    def lookup(self, naan: str, rest: str) -> tuple[PersistentIdentifier, str] | None:
        """Find the stored ARK of the name in ``rest``; return it with the raw qualifier after the name."""
        name, qualifier = split_name(rest)
        if not (normalized := normalize_rest(name)):
            return None
        pid = PersistentIdentifier.query.filter_by(pid_type="ark", pid_value=f"ark:{naan}/{normalized}").one_or_none()
        return None if pid is None else (pid, qualifier)


def _mints_arks(service: Any) -> bool:
    config = service.config
    if "ark" not in (getattr(config, "pids_providers", None) or {}):
        return False
    components = (getattr(c, "func", c) for c in getattr(config, "components", None) or [])  # classes or partials
    return any(isinstance(c, type) and issubclass(c, PIDsComponent) for c in components)


def _record_classes(service: Any) -> set[type | None]:
    return {service.config.record_cls, getattr(service.config, "draft_cls", None)}


def ark_identifier(record: Any) -> str | None:
    """Template global: the record's ARK (``ark:NAAN/name``) or None."""
    if record is None or not current_ark.naan:
        return None
    return current_ark.ark_for_record(record)


def ark_url(record: Any) -> str | None:
    """Template global: citable URL of the record's ARK or None."""
    ark = ark_identifier(record)
    return current_ark.public_url(ark) if ark else None


def finalize_app(app: Flask) -> None:
    """Validate the configuration and register the ARK PID provider with invenio-rdm-records.

    Runs at finalize time: setting ``RDM_PERSISTENT_IDENTIFIER_PROVIDERS`` in ``init_config``
    could run before invenio-rdm-records' own defaults and suppress them. A deployment that defines
    an ``ark`` provider / ``ark`` scheme itself keeps its own definitions.
    """
    if not app.config.get("ARK_NAAN"):
        return
    app.extensions["oarepo-ark"].validate_config()
    providers = app.config["RDM_PERSISTENT_IDENTIFIER_PROVIDERS"]
    # assign new objects: the values may be module-level defaults shared by every app in the process
    if not any(p.name == "ark" for p in providers):
        app.config["RDM_PERSISTENT_IDENTIFIER_PROVIDERS"] = [*providers, *ark_pid_providers()]
    pids = app.config.get("RDM_PERSISTENT_IDENTIFIERS", {})
    if "ark" not in pids:
        app.config["RDM_PERSISTENT_IDENTIFIERS"] = {**pids, "ark": ark_pid_config(app.extensions["oarepo-ark"])}
