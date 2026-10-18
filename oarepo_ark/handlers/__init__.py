# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""Built-in ARK handlers and the RDM PID provider.

- [`oarepo_ark.handlers.rdm`][] - `RDMRecordHandler`, the default `ARK_DEFAULT_HANDLER`: URLs
  from the result item's links, exports as variants, files as components, ERC from RDM metadata.
- [`oarepo_ark.handlers.rdm_pids`][] - `ARKPIDProvider` and the `ark` scheme for
  invenio-rdm-records, plus `assign_ark` used by `invenio ark mint`.

Write your own handler by subclassing `RDMRecordHandler` (most cases) or the bare
[`ARKHandler`][oarepo_ark.api.ARKHandler]; see [`oarepo_ark.api`][] for the contract and examples.
"""

from __future__ import annotations
