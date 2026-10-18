# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""ARK (Archival Resource Key) minting and resolution for InvenioRDM / OARepo repositories.

Every record version published as public gets an ARK such as `ark:12345/r1abcde12345p`
(NAAN `12345`, shoulder `r1`, record id `abcde-12345` without hyphens, check character `p`),
stored in the record's `pids.ark` like a DOI or the OAI id. The repository resolves
`https://<site>/ark:12345/r1abcde12345p[qualifier][?info]` itself; the citable form is
`https://n2t.net/ark:12345/r1abcde12345p`, which N2T forwards to the repository.

## Minting (on publish)

oarepo-ark does not hook into publishing itself: it registers a required `ark` PID scheme, and
RDM's `PIDsComponent` creates the PID like any other required PID.

```mermaid
sequenceDiagram
    participant S as RDM service.publish
    participant C as PIDsComponent
    participant P as ARKPIDProvider
    participant X as current_ark
    participant H as ARKHandler
    S->>C: publish(draft, record)
    C->>P: validate(draft, pids.ark?)
    C->>P: create(draft)  [scheme required, record public]
    P->>X: compute_ark(draft)
    X->>H: get_blade(draft)  (service found by the draft's class)
    X-->>P: ark:NAAN/shoulder+blade+check
    P-->>C: PID (REGISTERED)
    C-->>S: record.pids["ark"] = {identifier, provider: "ark"}
```

## Resolving

```mermaid
flowchart LR
    U["GET /ark:NAAN/name.json"] --> F{"our NAAN?"}
    F -- no --> N2T["302 to N2T"]
    F -- yes --> L["PIDStore lookup (normalized name)"]
    L --> H["handler of the service holding the object"]
    H --> R["service.read with the caller's identity"]
    R --> T["302 to landing page / API / export / file, or ?info"]
```

## Extending

- Per-service behaviour (blade, URLs, `?info` metadata): subclass
  [`ARKHandler`][oarepo_ark.api.ARKHandler] or
  [`RDMRecordHandler`][oarepo_ark.handlers.rdm.RDMRecordHandler] and register it in
  `ARK_HANDLERS`.
- Different PID wiring (e.g. not required, another provider class): define the `ark` provider
  and/or scheme in `RDM_PERSISTENT_IDENTIFIER_PROVIDERS` / `RDM_PERSISTENT_IDENTIFIERS`;
  `finalize_app` keeps them.
- Landing page: the `oarepo_ark/ark_identifier.html` template, the template globals
  `ark_identifier` / `ark_url` and the overridable React component `OarepoArk.ArkLinkDropdown`.

## Entry points (pyproject.toml)

| Group | Target |
|---|---|
| `invenio_base.apps`, `invenio_base.api_apps` | `oarepo_ark.ext:OARepoARK` |
| `invenio_base.finalize_app`, `invenio_base.api_finalize_app` | `oarepo_ark.ext:finalize_app` |
| `invenio_base.blueprints` | `oarepo_ark.resolver:create_blueprint` (UI app only) |
| `invenio_assets.webpack` | `oarepo_ark.theme.webpack:theme` |
| `invenio_i18n.translations` | `oarepo_ark` (`translations/`) |
| `flask.commands` | `ark = oarepo_ark.cli:ark` |
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("oarepo-ark")
except PackageNotFoundError:
    __version__ = "0.0.0dev0+unknown"

__all__ = ("__version__",)
