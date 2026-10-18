# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""Webpack bundle `oarepo_ark_links`: the ARK box dropdowns of the record detail page.

Registered through the `invenio_assets.webpack` entry point; the template includes it only when
there is a dropdown to mount. Aliases: `@js/oarepo_ark`, `@translations/oarepo_ark`.

No own npm dependencies: React, semantic-ui-react, react-overridable, the i18next packages and
the ``CopyButton`` all come with invenio-app-rdm's theme.
"""

from __future__ import annotations

from invenio_assets.webpack import WebpackThemeBundle

theme = WebpackThemeBundle(
    __name__,
    "assets",
    default="semantic-ui",
    themes={
        "semantic-ui": {
            "entry": {"oarepo_ark_links": "./js/oarepo_ark/links.js"},
            "dependencies": {},
            "aliases": {
                "@js/oarepo_ark": "js/oarepo_ark",
                "@translations/oarepo_ark": "translations/oarepo_ark",
            },
        },
    },
)
