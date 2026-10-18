# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""The `/ark:NAAN/...` resolver blueprint (UI app): redirects, `?info` and the ARK badge.

## URL rules

| Rule | View |
|---|---|
| `/ark:<path>`, `/ark:/<path>` | [`resolve`][oarepo_ark.resolver.resolve] |
| `/ARK:<path>`, `/ARK:/<path>` | [`resolve`][oarepo_ark.resolver.resolve] (rules are case sensitive) |
| `/ark-badge/<ark>.svg` | [`badge`][oarepo_ark.resolver.badge]: the badge "ARK", "ark:NAAN/name" |

## Resolution

```mermaid
flowchart TD
    A["/ark:NAAN/rest"] --> B{"ARK_NAAN set,<br/>valid ARK?"}
    B -- no --> E404["404 text"]
    B -- "other NAAN" --> N2T["302 https://n2t.net/ark:NAAN/rest[?info]"]
    B -- yes --> C{"stored in PIDStore<br/>(normalized name)?"}
    C -- no --> E404b["404: Not found / check character typo"]
    C -- yes --> D["handler_for_pid -> handler.read(caller)"]
    D --> S{"state"}
    S -- "?info" --> I["200 / 410 ERC (ANVL or JSON)"]
    S -- ok / restricted --> T["302 landing page, API, variant, component<br/>(404 if the handler has none)"]
    S -- withdrawn --> W["302 tombstone landing page, or 410"]
    S -- missing --> M["404 text"]
```

The object is read **with the caller's identity**, which decides the state:

- `ok` - `service.read` succeeds: 302 to the target; `?info` 200 with the handler's ERC.
- `restricted` - `PermissionDenied`: 302 to the target anyway (its URL is built from a read
  with the system identity, the target page enforces access); `?info` 200 with
  `what: (:unav) restricted` and nothing else revealed.
- `withdrawn` - the ARK PID is deleted, or RDM raises `RecordDeletedException`: 302 to the
  landing page, where RDM / oarepo-ui render the tombstone; 410 for qualifiers and when there
  is no item; `?info` 410 with `what: (:unav) withdrawn`.
- `missing` - no record PID or row (`NoResultFound`, `PIDDoesNotExistError`), an
  inconsistency: 404, also for `?info`.

Other exceptions propagate (500) instead of being reported as withdrawn objects.

## Targets

| Request | Target (handler method) |
|---|---|
| `/ark:12345/r1abcde12345p` | landing page (`landing_url`) |
| same, `Accept: application/json` | REST API (`api_url`) |
| `/ark:12345/r1abcde12345p.datacite-json` | export (`variant_url(item, "datacite-json")`) |
| `/ark:12345/r1abcde12345p/files/data.csv` | file content (`component_url(item, "/files/data.csv")`) |
| `...?info`, `...??`, `...?` | ERC + persistence statement |

Hyphens and trailing `/` `.` in the name are insignificant (`r1abcde-12345p` works); other
query strings are ignored. Every answer about a stored ARK carries
`Link: <https://n2t.net/ark:...>; rel="cite-as"` (RFC 8574); `?info` adds `rel="describes"`.

## Example

```
$ curl -i https://repo.example.org/ark:12345/r1abcde12345p
HTTP/1.1 302 FOUND
Location: https://repo.example.org/records/abcde-12345
Link: <https://n2t.net/ark:12345/r1abcde12345p>; rel="cite-as"

$ curl https://repo.example.org/ark:12345/r1abcde12345p?info
erc:
who:   Brown, Troy; Troy Inc.
what:  A Romans story
when:  2020-06-01
where: https://n2t.net/ark:12345/r1abcde12345p

erc-support:
who:   Example Repository
what:  Permanent: Stable Content:
when:  (:unav)
where: https://repo.example.org/
```
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flask import Blueprint, Response, g, redirect, request
from flask_principal import AnonymousIdentity, PermissionDenied
from invenio_access.permissions import system_identity
from invenio_pidstore.errors import PIDDoesNotExistError
from invenio_rdm_records.services.errors import RecordDeletedException
from sqlalchemy.orm.exc import NoResultFound
from werkzeug.wrappers import Response as BaseResponse

from .api import ERC, UNAV
from .ark import InvalidARKError, noid_check_char, normalize_rest, split_ark
from .proxies import current_ark
from .serializers import ANVLSerializer, JSONSerializer

if TYPE_CHECKING:
    from flask import Flask
    from invenio_pidstore.models import PersistentIdentifier

    from .api import ARKHandler

INFO_SERIALIZERS = {"text/plain": ANVLSerializer(), "application/json": JSONSerializer()}
"""``?info`` formats by ``Accept``: ANVL (defined by the ARK specification, the default) and JSON."""

MISSING_ERRORS = (NoResultFound, PIDDoesNotExistError)
"""The ARK's object cannot be found: an inconsistency, answered 404 - not "withdrawn"."""


def create_blueprint(app: Flask) -> Blueprint:  # noqa: ARG001 - invenio_base.blueprints signature
    """Blueprint with the resolver, the badge and the ARK box template.

    Rules are case sensitive, so the ``ARK:`` label gets its own; and Werkzeug's ``path``
    converter cannot start with ``/``, so the legacy ``ark:/NAAN/...`` form does too.
    """
    blueprint = Blueprint("oarepo_ark", __name__, template_folder="templates/semantic-ui")
    blueprint.add_url_rule("/ark:<path:ark>", view_func=resolve)
    blueprint.add_url_rule("/ark:/<path:ark>", view_func=resolve, endpoint="resolve_legacy")
    blueprint.add_url_rule("/ARK:<path:ark>", view_func=resolve, endpoint="resolve_upper")
    blueprint.add_url_rule("/ARK:/<path:ark>", view_func=resolve, endpoint="resolve_upper_legacy")
    blueprint.add_url_rule("/ark-badge/<path:ark>.svg", view_func=badge)
    return blueprint


def badge(ark: str) -> Response:
    """Serve the ``ARK | ark:NAAN/name`` badge like RDM's DOI badge, drawn by invenio-formatter's generator.

    Served here because invenio-formatter's badge route only accepts the titles in
    ``FORMATTER_BADGES_ALLOWED_TITLES``, fixed when that extension is initialized.
    """
    from invenio_formatter.context_processors.badges import generate_badge_svg

    response = Response(generate_badge_svg("ARK", ark), mimetype="image/svg+xml")
    response.cache_control.max_age = 86400  # an ARK never changes
    return response


def is_info_request() -> bool:
    """``?info``, legacy ``??`` (query ``?``) or bare ``?`` (dropped by Werkzeug, look at the raw URI)."""
    if request.query_string:
        return request.query_string in (b"info", b"?")
    raw_uri = request.environ.get("RAW_URI") or request.environ.get("REQUEST_URI") or ""
    return raw_uri.endswith("?")


def text_response(message: str, status: int) -> Response:
    """Plain-text error."""
    return Response(message + "\n", status=status, mimetype="text/plain")


def resolve(ark: str) -> Any:
    """Resolve an ARK to the landing page, its metadata, an export or a file."""
    found = find(ark)
    if isinstance(found, BaseResponse):
        return found
    pid, qualifier, handler = found
    link = f'<{current_ark.public_url(pid.pid_value)}>; rel="cite-as"'

    item, state = read_item(handler, pid)
    if state == "missing":
        return text_response("Not found: the object of this ARK does not exist.", 404)
    if is_info_request():
        return info_response(handler, item if state == "ok" else None, state, pid.pid_value, link)
    if state == "withdrawn":
        # the landing page renders RDM's / oarepo-ui's tombstone (410, citation, removal reason);
        # a withdrawn object has no exports or files
        url = handler.landing_url(item) if item is not None and not qualifier else None
        if url is None:
            return text_response("This object has been withdrawn.", 410), {"Link": link}
    else:
        if item is None:  # restricted: URLs only, the target pages enforce access
            item = handler.read(system_identity, pid.object_uuid)
        url = target_url(handler, item, qualifier)
        if url is None:
            return text_response("This ARK has no such variant or component.", 404), {"Link": link}
    response = redirect(url, 302)
    response.headers["Link"] = link
    return response


def read_item(handler: ARKHandler, pid: PersistentIdentifier) -> tuple[Any, str]:
    """Service result item with the caller's identity, and ``ok`` / ``restricted`` / ``withdrawn`` / ``missing``.

    Withdrawn means deleted on purpose - the ARK PID is deleted, or the service raises RDM's
    ``RecordDeletedException`` (the item is then the tombstone item, links only). Any other error
    propagates instead of being reported as withdrawn.
    """
    if pid.is_deleted():
        return None, "withdrawn"
    identity = getattr(g, "identity", None) or AnonymousIdentity()
    try:
        return handler.read(identity, pid.object_uuid), "ok"
    except PermissionDenied:  # PermissionDeniedError, or RecordPermissionDeniedError from service.read
        return None, "restricted"
    except RecordDeletedException as e:
        return e.result_item, "withdrawn"
    except MISSING_ERRORS:
        return None, "missing"


def find(ark: str) -> tuple[PersistentIdentifier, str, ARKHandler] | BaseResponse:
    """Find the stored ARK, raw qualifier and handler; or the response: 404, or a redirect to N2T for other NAANs."""
    if not current_ark.naan:
        return text_response("ARKs are not configured.", 404)
    try:
        naan, rest = split_ark(f"ark:{ark}")
    except InvalidARKError:
        return text_response("Not an ARK.", 404)
    if naan != current_ark.naan:  # keep the inflection, N2T forwards it to the NAAN's resolver
        return redirect(current_ark.public_url(f"ark:{naan}/{rest}") + ("?info" if is_info_request() else ""), 302)
    hit = current_ark.lookup(naan, rest)
    if hit is None:
        return text_response(not_found_message(naan, rest), 404)
    handler = current_ark.handler_for_pid(hit[0])
    if handler is None:
        return text_response("No handler for this ARK.", 404)
    return *hit, handler


def target_url(handler: ARKHandler, item: Any, qualifier: str) -> str | None:
    """Landing page / API (by ``Accept``), ``.variant`` or ``/component``."""
    if not qualifier:
        if request.accept_mimetypes.best_match(["text/html", "application/json"]) == "application/json":
            return handler.api_url(item)
        return handler.landing_url(item)
    if qualifier.startswith("."):
        return handler.variant_url(item, qualifier[1:])
    return handler.component_url(item, qualifier)


def not_found_message(naan: str, rest: str) -> str:
    """404 text; points out a wrong check character on our own shoulders."""
    name = normalize_rest(rest).split("/")[0].split(".")[0]
    own = naan == current_ark.naan and len(name) > 1 and current_ark.is_own_shoulder(name)
    if own and noid_check_char(f"{naan}/{name[:-1]}") != name[-1]:
        return "Not found: the check character does not match, the ARK probably contains a typo."
    return "Not found."


def info_response(handler: ARKHandler, item: Any, state: str, ark: str, link: str) -> Response:
    """``?info``: ERC metadata and the persistence statement."""
    erc = handler.erc(item) if item is not None else ERC(what=f"{UNAV} {state}")
    support = current_ark.support()
    mimetype = request.accept_mimetypes.best_match(list(INFO_SERIALIZERS)) or "text/plain"
    serializer = INFO_SERIALIZERS[mimetype]
    where = current_ark.public_url(ark)
    return Response(
        serializer.serialize(erc, where, support),
        status=410 if state == "withdrawn" else 200,
        mimetype=serializer.mimetype,
        headers={"Link": f'{link}, <{where}>; rel="describes"'},
    )
