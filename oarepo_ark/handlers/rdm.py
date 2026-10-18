# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""Default handler for RDM-like records (InvenioRDM and oarepo-rdm models).

[`RDMRecordHandler`][oarepo_ark.handlers.rdm.RDMRecordHandler] derives every URL from the
service result item's `links`, so it works for any model whose UI serves exports at
`<self_html>/export/<format>` (invenio-app-rdm and oarepo-ui both do) and whose files service
serves content at `<files>/<key>/content` (invenio-records-resources).

## Mapping

| ARK | Target |
|---|---|
| `ark:12345/r1abcde12345p` | `links.self_html` (landing page) / `links.self` (API, on `Accept: application/json`) |
| `ark:12345/r1abcde12345p.json` | `<links.self_html>/export/json` - an unknown format is the UI's 404 |
| `ark:12345/r1abcde12345p/files/dir/a b.csv` | `<links.files>/dir/a%20b.csv/content` - the files API enforces access |
| any other component (`/versions`, ...) | 404 |

| ERC element | Metadata |
|---|---|
| who | `creators[].person_or_org.name`, joined by `; ` |
| what | `title` |
| when | `publication_date` |

Elements whose metadata is missing or not in this shape (e.g. a multilingual title dict) are
`(:unav)` - `?info` never fails on another model's metadata. Override `erc` to map it; see
[`oarepo_ark.api`][] for examples.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..api import ERC, UNAV, ARKHandler


class RDMRecordHandler(ARKHandler):
    """Exports as variants, files as components, DataCite-like ERC.

    URLs are derived from the item's links, so the handler works for any model: both
    invenio-app-rdm and oarepo-ui serve exports at ``<self_html>/export/<format>``, and
    records-resources serves file content at ``<files>/<key>/content``.
    """

    def variant_url(self, item: Any, variant: str) -> str | None:
        """``.json``, ``.datacite-json``, ... -> UI export (an unknown format is a 404 there)."""
        return f"{item.links['self_html']}/export/{quote(variant)}"

    def component_url(self, item: Any, path: str) -> str | None:
        """``/files/<key>`` -> file content (the files API enforces access)."""
        key = path.removeprefix("/files/")
        if key == path or not key or "files" not in item.links:
            return None
        return f"{item.links['files']}/{quote(key)}/content"

    def erc(self, item: Any) -> ERC:
        """Creators, title and publication date; elements not in RDM's shape are ``(:unav)``."""
        creators = _get(item.data, "metadata", "creators")
        names = [_get(c, "person_or_org", "name") for c in creators] if isinstance(creators, list) else []
        return ERC(
            who="; ".join(n for n in names if _is_text(n)) or UNAV,
            what=_text(_get(item.data, "metadata", "title")),
            when=_text(_get(item.data, "metadata", "publication_date")),
        )


def _get(data: Any, *path: str) -> Any:
    """``data[path[0]][path[1]]...`` or None if the structure differs."""
    for key in path:
        try:
            data = data[key]
        except KeyError, IndexError, TypeError:
            return None
    return data


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _text(value: Any) -> str:
    return value if _is_text(value) else UNAV
