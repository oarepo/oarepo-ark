# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""Default configuration of oarepo-ark; every key can be overridden in `invenio.cfg` or the environment.

The defaults are applied with `setdefault` when the extension is initialized; they are read at
use time, so tests may change them on the app. Without `ARK_NAAN` the extension is inert: no
`ark` PID scheme, the resolver answers 404 and the template globals return `None`.

## Minimal configuration

```{.python}
# invenio.cfg
ARK_NAAN = "12345"  # assigned by arks.org; "99999" is reserved for tests and examples
ARK_DEFAULT_SHOULDER = "r1"
```

or `INVENIO_ARK_NAAN=12345 INVENIO_ARK_DEFAULT_SHOULDER=r1` (invenio-config parses `12345`
into an integer; the extension turns it back into a string).

## Full example

```{.python}
ARK_NAAN = "12345"
ARK_DEFAULT_SHOULDER = "r1"  # every service minting ARKs that has no override
ARK_SHOULDERS = {"ccmm-dataset": "d1", "theses": "t2"}  # unique, primordinal
ARK_HANDLERS = {"theses": "my_repo.ark:ThesisHandler"}
ARK_DEFAULT_HANDLER = "oarepo_ark.handlers.rdm:RDMRecordHandler"
ARK_PUBLIC_RESOLVER = "https://n2t.net/"  # NMA of citable URLs
ARK_SUPPORT = {
    "who": "Example University Library",  # default: THEME_SITENAME
    "what": "Permanent: Stable Content:",
    "when": "20260101",  # since when the commitment holds
    "where": None,  # default: SITE_UI_URL
}
```

## Validation

At `finalize_app` (startup) and by `invenio ark check`; an invalid configuration raises
[`ARKConfigurationError`][oarepo_ark.errors.ARKConfigurationError]:

- `ARK_NAAN` has 5 characters from `0-9` and `bcdfghjkmnpqrstvwxz`;
- `ARK_DEFAULT_SHOULDER` is set;
- all shoulders are primordinal (`r1`, `bc2`: consonants, then one digit) and pairwise distinct.

Changing a shoulder later only affects records published afterwards; existing ARKs are kept
(PIDStore is the source of truth, the resolver finds them under any shoulder).
"""

from __future__ import annotations

from typing import Any

ARK_NAAN: str | None = None
"""Name Assigning Authority Number from arks.org (``99999`` for tests); without it the extension is inert."""

ARK_DEFAULT_SHOULDER: str | None = None
"""Primordinal shoulder (``r1``, ``c2``, ...) of all services; required when ``ARK_NAAN`` is set."""

ARK_SHOULDERS: dict[str, str] = {}
"""Per-service overrides of ``ARK_DEFAULT_SHOULDER``: service_id -> primordinal shoulder."""

ARK_HANDLERS: dict[str, Any] = {}
"""Per-service handlers: service_id -> ``ARKHandler`` subclass or its import string."""

ARK_DEFAULT_HANDLER: Any = "oarepo_ark.handlers.rdm:RDMRecordHandler"
"""Handler class (or import string) of services not in ``ARK_HANDLERS``."""

ARK_PUBLIC_RESOLVER = "https://n2t.net/"
"""NMA of citable ARK URLs."""

ARK_SUPPORT: dict[str, str | None] = {
    "who": None,
    "what": "Permanent: Stable Content:",
    "when": None,
    "where": None,
}
"""``erc-support`` (persistence statement) of ``?info``.

``who`` defaults to ``THEME_SITENAME``, ``where`` to ``SITE_UI_URL``; usually left unset.
"""
