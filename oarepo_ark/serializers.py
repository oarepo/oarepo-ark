# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""Serializers of the `?info` inflection: ANVL (the ARK specification's format) and JSON.

The resolver picks one by `Accept` (`text/plain` - the default - or `application/json`); both
serialize two records, the object's [`ERC`][oarepo_ark.api.ERC] (`who`, `what`, `when`,
`where` = the citable ARK URL, then `ERC.extra`) and the persistence statement `erc-support`
(see `ARK_SUPPORT`).

ANVL, `text/plain; charset=utf-8` - one `name: value` line per element, whitespace in values
collapsed so that no value starts an ANVL continuation line:

```
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

JSON, `application/json`:

```json
{
  "erc": {"who": "Brown, Troy; Troy Inc.", "what": "A Romans story", "when": "2020-06-01",
          "where": "https://n2t.net/ark:12345/r1abcde12345p"},
  "erc-support": {"who": "Example Repository", "what": "Permanent: Stable Content:",
                  "when": "(:unav)", "where": "https://repo.example.org/"}
}
```

To add a format, add a serializer with `mimetype` and `serialize(erc, where, support)` to
[`INFO_SERIALIZERS`][oarepo_ark.resolver.INFO_SERIALIZERS].
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api import ERC


def erc_dict(erc: ERC, where: str) -> dict[str, str]:
    """ERC kernel elements followed by the extra ones."""
    return {"who": erc.who, "what": erc.what, "when": erc.when, "where": where, **erc.extra}


class ANVLSerializer:
    """ERC as ANVL (``text/plain``), the format defined by the ARK specification."""

    mimetype = "text/plain; charset=utf-8"

    def serialize(self, erc: ERC, where: str, support: dict[str, str]) -> str:
        """Serialize the ``erc`` and ``erc-support`` records."""
        return self._record("erc", erc_dict(erc, where)) + "\n" + self._record("erc-support", support)

    @staticmethod
    def _record(label: str, elements: dict[str, str]) -> str:
        # one line per element: newlines in values would start an ANVL continuation
        lines = [f"{label}:"] + [f"{k + ':':<6} {' '.join(str(v).split())}" for k, v in elements.items()]
        return "\n".join(lines) + "\n"


class JSONSerializer:
    """ERC as JSON."""

    mimetype = "application/json"

    def serialize(self, erc: ERC, where: str, support: dict[str, str]) -> str:
        """Serialize the ``erc`` and ``erc-support`` records."""
        return json.dumps({"erc": erc_dict(erc, where), "erc-support": support}, ensure_ascii=False)
