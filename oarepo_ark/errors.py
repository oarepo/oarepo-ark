# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""Exceptions of oarepo-ark.

```
ARKError
├── ARKConfigurationError (also RuntimeError)
└── ARKRecordError (also ValueError)
```

- `ARKConfigurationError` - raised by `finalize_app`, `validate_config`, `shoulder`, `handler`
  and `invenio ark check`: a malformed NAAN or shoulder, shared shoulders, no default shoulder,
  a handler that is not a class.
- `ARKRecordError` - raised by `compute_ark`: the record's class belongs to no service minting
  ARKs, or the handler's blade contains `/`, `.` or `%`.
- [`InvalidARKError`][oarepo_ark.ark.InvalidARKError] (in `oarepo_ark.ark`, a `ValueError`) -
  raised by the parsing functions: the string is not an ARK.

On publish, [`ARKPIDProvider.validate`][oarepo_ark.handlers.rdm_pids.ARKPIDProvider.validate]
turns any `ARKError` into a `pids.ark` validation error, so a broken configuration fails the
publish with a readable message instead of an exception deep in PID creation.
"""

from __future__ import annotations


class ARKError(Exception):
    """Base class: an ARK cannot be configured or computed."""


class ARKConfigurationError(ARKError, RuntimeError):
    """Invalid NAAN, shoulder or handler configuration, or a service without a shoulder."""


class ARKRecordError(ARKError, ValueError):
    """No ARK can be computed for this record: not a record of an ARK service, or an unusable blade."""
