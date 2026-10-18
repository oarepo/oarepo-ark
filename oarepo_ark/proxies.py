# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""`current_ark`: the [`OARepoARK`][oarepo_ark.ext.OARepoARK] extension of the current Flask app.

```{.python}
from oarepo_ark.proxies import current_ark

if current_ark.naan:  # None: ARKs are disabled
    ark = current_ark.ark_for_record(record)
```

Works in the UI and the API app (the extension is registered in both); requires an app context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from flask import current_app
from werkzeug.local import LocalProxy

if TYPE_CHECKING:
    from oarepo_ark.ext import OARepoARK

current_ark = cast("OARepoARK", LocalProxy(lambda: current_app.extensions["oarepo-ark"]))
