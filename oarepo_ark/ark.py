# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""ARK syntax: parsing, normalization and NOID check characters (pure functions, no Flask).

Everything else in oarepo-ark builds on these functions; they know nothing about records,
services or configuration. Specification: <https://arks.org/> and the IETF draft
`draft-kunze-ark`.

## Anatomy

```
https://n2t.net/ark:12345/r1abcde12345p/files/data.csv?info
└──── NMA ─────┘└─┬┘└NAAN┘└─── name ──┘└─ qualifier ──┘└inflection
                label      shoulder r1, blade abcde12345, check character p
```

- [`split_ark`][oarepo_ark.ark.split_ark] drops the NMA, the label, query and fragment and
  returns the NAAN and the raw *rest* (name + qualifier).
- [`split_name`][oarepo_ark.ark.split_name] splits the rest into the name and the qualifier.
- [`normalize_ark`][oarepo_ark.ark.normalize_ark] gives the canonical `ark:NAAN/name` form
  under which ARKs are stored in PIDStore (`pid_type="ark"`).

>>> split_ark(
...     "https://n2t.net/ark:/12345/r1abcde12345p/files/data.csv?info"
... )
('12345', 'r1abcde12345p/files/data.csv')
>>> split_name(
...     "r1abcde12345p/files/data.csv"
... )
('r1abcde12345p', '/files/data.csv')

## Normalization

ARKs that differ only in these respects are the same ARK:

| Difference | Example | Normalized |
|---|---|---|
| NMA (host) | `https://repo.example.org/ark:12345/x` | `ark:12345/x` |
| label form and letter case | `ark:/12345/x`, `ARK:12345/x` | `ark:12345/x` |
| NAAN letter case | `ark:B1234/x` | `ark:b1234/x` |
| hyphens (insignificant) | `ark:12345/r1-abcde-12345p` | `ark:12345/r1abcde12345p` |
| trailing `/` or `.`, repeated `/` and `.` | `ark:12345/x//y..z/` | `ark:12345/x/y.z` |
| %-escape hex case | `ark:12345/x%2f` | `ark:12345/x%2F` |

The name itself stays case sensitive. Lookups normalize only the *name*: qualifiers are kept raw,
so file keys keep their hyphens.

>>> normalize_ark(
...     "https://n2t.net/ARK:/12345/r1-abcde-12345p/"
... )
'ark:12345/r1abcde12345p'

## Check character

The last character of every minted name is a NOID check character over `NAAN/shoulder+blade`:
each character's value in [`XDIGITS`][oarepo_ark.ark.XDIGITS] (`0-9` = 0-9, `b` = 10, ...,
`z` = 28; others 0) is multiplied by its 1-based position, and the sum modulo 29 indexes
`XDIGITS`. For `13030/xf93gt2`:
`1·1 + 3·2 + 0·3 + 3·4 + 0·5 + 0·6 + 27·7 + 13·8 + 9·9 + 3·10 + 14·11 + 24·12 + 2·13 = 891`,
`891 mod 29 = 21` → `q`. Since 29 is prime, any single wrong character and any swap of two
neighbouring characters (both from `XDIGITS`) changes the result; the resolver uses it to tell a
probable typo from an unknown ARK.

>>> noid_check_char("13030/xf93gt2")
'q'
>>> noid_check_char("12345/r1abcde12345")
'p'

## Shoulders and blades

- A *primordinal* shoulder ([`SHOULDER_RE`][oarepo_ark.ark.SHOULDER_RE]) ends at its first
  digit (`r1`, `bc2`), so no shoulder is a prefix of another and the shoulder of any name is
  unambiguous.
- A blade ([`BLADE_RE`][oarepo_ark.ark.BLADE_RE]) must not contain the structural characters
  `/` and `.` (they start qualifiers) nor `%`; hyphens are allowed but dropped by normalization.
"""

from __future__ import annotations

import re

XDIGITS = "0123456789bcdfghjkmnpqrstvwxz"
"""NOID extended digits; 29 is a prime, which makes the check character detect transpositions."""

NAAN_RE = re.compile(r"^[0-9bcdfghjkmnpqrstvwxz]{5}$")
"""Configured NAAN (``ARK_NAAN``); parsing accepts any alphanumeric NAAN."""

SHOULDER_RE = re.compile(r"^[bcdfghjkmnpqrstvwxz]*[0-9]$")
"""Primordinal shoulder: zero or more consonants and one digit; it ends at its first digit, so no
shoulder is a prefix of another."""

BLADE_RE = re.compile(r"^[0-9A-Za-z_~*+=@$-]+$")
"""Blade returned by a handler: no structural characters ``/`` ``.`` and no ``%``."""

_LABEL_RE = re.compile(r"ark:", re.IGNORECASE)
_ARK_RE = re.compile(r"(?i:ark):/?(?P<naan>[0-9A-Za-z]+)/(?P<rest>.+)")


class InvalidARKError(ValueError):
    """The string is not an ARK."""


def noid_check_char(value: str) -> str:
    """Return the NOID check character of ``NAAN/shoulder+blade``.

    Characters outside [`XDIGITS`][oarepo_ark.ark.XDIGITS] weigh 0.

    >>> noid_check_char("13030/xf93gt2")
    'q'
    """
    total = sum(pos * XDIGITS.find(c) for pos, c in enumerate(value, 1) if c in XDIGITS)
    return XDIGITS[total % len(XDIGITS)]


def split_ark(value: str) -> tuple[str, str]:
    """Split an ARK into a lowercased NAAN and the raw rest (name + qualifiers).

    Strips the NMA (anything before ``ark:``), the label in any letter case or legacy ``ark:/`` form,
    the query and the fragment.

    >>> split_ark(
    ...     "https://n2t.net/ARK:/12345/r1x/files/a-b.csv?info"
    ... )
    ('12345', 'r1x/files/a-b.csv')
    """
    label = _LABEL_RE.search(value)
    match = _ARK_RE.fullmatch(re.split(r"[?#]", value[label.start() :], maxsplit=1)[0]) if label else None
    if match is None:
        raise InvalidARKError(f"Not an ARK: {value!r}")
    return match["naan"].lower(), match["rest"]


def normalize_rest(rest: str) -> str:
    """Normalize the part after ``NAAN/``; the result is compared octet by octet.

    Upper-cases %-escapes, drops hyphens, collapses runs of ``/`` and ``.`` and strips trailing ones.
    """
    rest = re.sub(r"%[0-9a-fA-F]{2}", lambda m: m.group().upper(), rest).replace("-", "")
    return re.sub(r"([/.])[/.]+", r"\1", rest).rstrip("/.")


def normalize_ark(value: str) -> str:
    """Return the normalized ``ark:NAAN/name`` form used as PID value.

    >>> normalize_ark(
    ...     "https://example.org/ark:/12345/r1abcde-12345p/"
    ... )
    'ark:12345/r1abcde12345p'
    """
    naan, rest = split_ark(value)
    if not (rest := normalize_rest(rest)):
        raise InvalidARKError(f"ARK without a name: {value!r}")
    return f"ark:{naan}/{rest}"


def is_ark(value: str) -> bool:
    """Return whether ``value`` is a syntactically valid ARK."""
    try:
        normalize_ark(value)
    except InvalidARKError:
        return False
    return True


def split_name(rest: str) -> tuple[str, str]:
    """Split the raw rest into the name and the qualifier (from the first ``/`` or ``.``).

    Minted names contain no structural characters. The qualifier is kept raw (normalization would
    strip hyphens from file names); trailing ``/`` and ``.`` are insignificant.

    >>> split_name("r1x/files/my-data.csv")
    ('r1x', '/files/my-data.csv')
    >>> split_name("r1x.json/")
    ('r1x', '.json')
    """
    rest = rest.rstrip("/.")
    match = re.search(r"[/.]", rest)
    return (rest, "") if match is None else (rest[: match.start()], rest[match.start() :])
