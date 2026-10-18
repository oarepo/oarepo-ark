# OARepo ARK

[ARK](https://arks.org/) (Archival Resource Key) minting and resolution for
[OARepo](https://github.com/oarepo) / InvenioRDM repositories.

## Overview

- every RDM record published as public (InvenioRDM and every oarepo-rdm model) gets an ARK
  `ark:NAAN/<shoulder><record id without hyphens><check char>` in `pids.ark`, minted by an RDM PID
  provider on publish
- `https://<site>/ark:NAAN/...` (also `ark:/NAAN/...` and `ARK:`) resolves to the landing page, REST API
  (`Accept: application/json`), exports (`.json`, `.datacite-json`, ...) and files (`/files/<key>`);
  deleted records go to RDM's tombstone page, ARKs of other NAANs to N2T
- `?info` returns ERC metadata and the persistence statement (ANVL or JSON)
- per-model handlers customize blades, URLs and `?info` metadata
- `invenio ark check|show|mint` commands

## Installation

```bash
pip install oarepo-ark
```

Requires Python 3.14 and InvenioRDM 14 (`invenio-app-rdm`).

```python
# invenio.cfg
ARK_NAAN = "12345"  # from arks.org; "99999" for tests
ARK_DEFAULT_SHOULDER = "r1"  # required; per-service overrides: ARK_SHOULDERS = {"ccmm-dataset": "d1"}
```

Without `ARK_NAAN` the extension is installed but inert. Register the NAAN at arks.org with
`https://<site>/ark:${content}` as the redirect target so that `https://n2t.net/ark:12345/...`
reaches the resolver.

## How ARKs are minted

At `finalize_app` the extension adds `ARKPIDProvider` to `RDM_PERSISTENT_IDENTIFIER_PROVIDERS` and a
**required** `ark` scheme to `RDM_PERSISTENT_IDENTIFIERS`, so RDM creates the ARK on publish - exactly
like DOIs and OAI ids. Users cannot choose ARKs: a draft carrying any other `pids.ark` than the
record's own fails to publish.
The scheme is global: every service using the RDM PID machinery mints ARKs, so every service needs
a shoulder - hence `ARK_DEFAULT_SHOULDER` is required, and `ARK_SHOULDERS` only overrides it per
service. `invenio ark check` lists the services and their shoulders (with oarepo-rdm also the
global `records` service, which only delegates to the model services and never mints anything).

RDM's DataCite serializer sends every non-DOI PID as an alternate identifier, so DOI registrations and
updates carry the ARK too.

Like DOIs, ARKs are not created for records published as restricted; they get one when published as
public. A new version gets a new ARK; there are no concept ARKs for all versions, so an ARK -
including its `/files/...` - always means the same content. Records published before oarepo-ark was
installed get their ARK with `invenio ark mint` (see below). To wire the provider differently,
define an `ark` provider in `RDM_PERSISTENT_IDENTIFIER_PROVIDERS` and/or an `ark` scheme in
`RDM_PERSISTENT_IDENTIFIERS` yourself; oarepo-ark then keeps yours.

## Handlers

Each service has a handler: `ARK_HANDLERS[service_id]`, else `ARK_DEFAULT_HANDLER`
(`RDMRecordHandler`: URLs from the result item's links, so it works for any model; `?info` metadata
from RDM-shaped `creators`, `title` and `publication_date`, `(:unav)` where a model's metadata differs).
Both take a handler class or its import string; the handler is created with the service id.

```python
from oarepo_ark.api import ERC
from oarepo_ark.handlers.rdm import RDMRecordHandler


class ThesisHandler(RDMRecordHandler):
    def erc(self, item):
        md = item.data["metadata"]
        return ERC(who=md["author"], what=md["title"], when=md["defense_date"][:4])


ARK_HANDLERS = {"theses": ThesisHandler}
```

| Need | Override |
|---|---|
| metadata of `?info` | `erc` |
| record id -> blade | `get_blade` (the check character is always appended) |
| exports / files | `variant_url` / `component_url` |
| landing page / API URLs | `landing_url` / `api_url` |
| ARK's object -> result item (e.g. a record PID type the record class does not declare) | `read` |

oarepo (oarepo-model / oarepo-rdm) repositories: see the [NRP-CZ documentation](https://github.com/NRP-CZ/docs/blob/main/content/customize/model_backend/persistent_identifiers/arks.mdx).

## Landing page

Add the ARK box to the detail page sidebar (InvenioRDM and oarepo-ui),
`APP_RDM_DETAIL_SIDE_BAR_TEMPLATES = [..., "oarepo_ark/ark_identifier.html"]`. It shows

- an "ARK | ark:NAAN/name" badge (served at `/ark-badge/<ark>.svg`) and a copy button, like RDM's DOI;
- a dropdown of the ARK URLs of the export formats (`<ark>.<code>`; oarepo-ui: the model's displayed
  exports, InvenioRDM: `APP_RDM_RECORD_EXPORTERS`) and one of the files (`<ark>/files/<key>`), each
  with a copy button. These are React components of the `oarepo_ark_links` webpack bundle: rebuild
  the repository's assets after installing oarepo-ark (e.g. `invenio webpack buildall`).

For your own templates there are the globals `ark_identifier(record)` (`ark:NAAN/name`) and
`ark_url(record)` (citable URL); they accept a record, its UI dict or a service result item.

The templates, messages and React components are translated into Czech and English.

## Commands

| Command | |
|---|---|
| `invenio ark check` | print the effective configuration (NAAN, shoulders, handlers, resolver, `erc-support`), validate it (exit 1 if invalid) and list the services minting ARKs with their shoulder and handler |
| `invenio ark show <ark>` | what an ARK resolves to: stored ARK, qualifier, status, object, handler, citable URL |
| `invenio ark mint [--service ID]...` | give existing published records their ARK (default: all services minting ARKs); skips records that are restricted, deleted or already have an ARK |
| `invenio ark mint --service ID [RECORD_ID]...` | the same for the given records |
| `invenio ark mint --service ID --pid-type T` | when the PID type of the service's records cannot be read from its record class |

`mint` updates each record in place (PID, `pids.ark`, reindex) in its own transaction - no new
draft, revision via publish or DataCite call; a failed record is reported and retried by the next run.

## Configuration

| Key | Default | |
|---|---|---|
| `ARK_NAAN` | `None` | NAAN: 5 digits or consonants (`bcdfghjkmnpqrstvwxz`) |
| `ARK_DEFAULT_SHOULDER` | `None` | primordinal shoulder of all services; required with `ARK_NAAN` |
| `ARK_SHOULDERS` | `{}` | per-service overrides: service_id -> shoulder |
| `ARK_HANDLERS` | `{}` | service_id -> handler |
| `ARK_DEFAULT_HANDLER` | `RDMRecordHandler` | handler of the other services |
| `ARK_PUBLIC_RESOLVER` | `https://n2t.net/` | NMA of citable URLs |
| `ARK_SUPPORT` | who: `THEME_SITENAME`, what: `Permanent: Stable Content:`, where: `SITE_UI_URL` | `erc-support` of `?info` |

## Development

```bash
./run.sh venv
./run.sh test   # starts PostgreSQL/OpenSearch/... in docker, runs the tests on a real InvenioRDM app, stops them
./run.sh lint
./run.sh jslint        # ESLint / Prettier for the React components
./run.sh translations  # extract and compile translations (oarepo.yaml, babel.ini)
```

To run pytest directly (e.g. with `-k`), keep the services running:

```bash
./run.sh start
set -a; . ./.env-services; set +a
.venv/bin/python -m pytest -k resolver
./run.sh stop
```

## Terminology

A short guide to the ARK vocabulary used above, for readers who know ARKs as persistent identifiers
but not the details. Source: [arks.org](https://arks.org/) and the ARK specification
(IETF draft `draft-kunze-ark`).

An ARK as minted by this library, taken apart:

```
https://n2t.net/ark:12345/r1abcde12345p/files/data.csv?info
└──────┬───────┘└┬─┘└─┬─┘ └─────┬─────┘└──────┬──────┘└─┬─┘
      NMA        │  NAAN      name        qualifier     │
               label                               inflection

r1abcde12345p
│ │         └ check character
│ └ blade (record id abcde-12345 without hyphens)
└ shoulder
```

| Term | Meaning | In this library |
|---|---|---|
| **ARK** | Archival Resource Key: a persistent identifier that, unlike a DOI, needs no paid registration agency - an organization gets a NAAN once and mints ARKs itself. | One ARK per record version published as public, stored in the record's `pids.ark`. |
| **NMA** (Name Mapping Authority) | The host part in front of `ark:` (`https://n2t.net/`, `https://repo.example.org/`). It only says *where* the ARK is resolved, it is not part of the identity: the same ARK with another NMA is the same identifier. | Citable links use `ARK_PUBLIC_RESOLVER` (N2T); the repository itself resolves `https://<site>/ark:...`. |
| **`ark:` label** | Marks the start of the ARK. The older form `ark:/` (with a slash) must still be accepted, in any letter case. | Both forms are resolved, also as `ARK:`; ARKs are written as `ark:`. |
| **NAAN** (Name Assigning Authority Number) | The organization's number, assigned for free by arks.org, usually 5 digits. Everything after it is managed by the organization. `99999` is reserved for tests and examples. | `ARK_NAAN`; ARKs of other NAANs are redirected to N2T. |
| **Name** | The part after `NAAN/` that identifies the object: shoulder + blade (+ check character). | `r1abcde12345p`. |
| **Shoulder** | A prefix of the name that splits a NAAN into sub-namespaces (per department, collection, record type). A *primordinal* shoulder is zero or more consonants followed by one digit, e.g. `r1`, `x6`, `bc2`: it ends at its first digit, so no shoulder is a prefix of another. | `ARK_DEFAULT_SHOULDER` for all services, overrides per service in `ARK_SHOULDERS`. |
| **Blade** | The rest of the name after the shoulder; ideally opaque (no meaning that could become wrong later). | The record id without hyphens (`abcde-12345` → `abcde12345`). |
| **Check character** | One character computed from `NAAN/shoulder+blade` with the NOID algorithm. NOID works over 29 characters (`0-9` and the consonants `bcdfghjkmnpqrstvwxz`); any other character counts as 0. For names made only of those 29 it detects every single wrong character and every swap of two neighbouring characters. | Always appended; a wrong one gets a "probably a typo" 404. Blades are RDM record ids, which may also contain `a`, `e` and `y` (outside the 29): typos that only involve these go unnoticed - measured on RDM ids, about 1 % of single wrong characters and of neighbour swaps. Such an ARK then simply gets a plain "Not found". |
| **Qualifier** | Text after the name addressing a part or form of the object: `/xyz` for a component (hierarchy), `.xyz` for a variant (format, language, version). | `/files/<key>` → the file, `.<format>` (e.g. `.json`) → an export. |
| **Inflection** | A query appended to the ARK asking *about* the object instead of for the object itself. `?info` is the standard one; `?` and `??` are older forms. | All three return the ERC metadata and the persistence statement; for other NAANs they are passed on to N2T as `?info`. |
| **ERC** (Electronic Resource Citation) | Minimal "kernel" metadata returned by `?info`: **who** (creator), **what** (title), **when** (date), **where** (the ARK). | Taken from the record by the handler's `erc()`; restricted/withdrawn records reveal nothing. |
| **Persistence statement** (`erc-support`) | What the organization commits to for the object, e.g. `Permanent: Stable Content:`, plus who supports it and since when. | `ARK_SUPPORT`. |
| **ANVL** | "A Name-Value Language": the simple `name: value` text format of `?info` responses. | Default `?info` format; JSON on `Accept: application/json`. |
| **N2T** (Name-to-Thing) | The global ARK resolver at `https://n2t.net/`. It knows every NAAN's target and forwards `https://n2t.net/ark:NAAN/...` (with the rest of the ARK) to the organization. | Register `https://<site>/ark:${content}` as your NAAN's target at arks.org. |
| **Normalization** | ARKs that differ only in the NMA, `ark:` vs `ark:/`, the letter case of the label and the NAAN, hyphens, or a trailing `/` or `.` are the same ARK. Everything else is case sensitive. | Applied before storing and looking up ARKs; `r1abcde-12345p` finds `r1abcde12345p`. |

## License

MIT, see [LICENSE](LICENSE).
