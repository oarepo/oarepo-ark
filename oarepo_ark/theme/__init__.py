# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""UI of oarepo-ark: the landing-page ARK box.

- template `templates/semantic-ui/oarepo_ark/ark_identifier.html` - the sidebar box: badge,
  copy button and the `data-options` of the dropdowns;
- badge `/ark-badge/<ark>.svg` ([`badge`][oarepo_ark.resolver.badge]) - "ARK" and the ARK,
  drawn like RDM's DOI badge;
- copy button - mounted on `.copy-doi-button` by invenio-app-rdm's `theme.js`, as for the DOI;
- bundle `oarepo_ark_links` (`theme/assets/semantic-ui/js/oarepo_ark/links.js`) - mounts a
  dropdown on every `.oarepo-ark-links` element;
- component `ArkLinkDropdown.js` - export formats or files with a copy button, overridable as
  `OarepoArk.ArkLinkDropdown`;
- translations - `translations/` (templates, messages) and
  `theme/assets/semantic-ui/translations/oarepo_ark` (React), Czech and English; regenerate
  with `./run.sh translations`.

Enable the box in the record detail sidebar (InvenioRDM and oarepo-ui) and rebuild the assets:

```{.python}
APP_RDM_DETAIL_SIDE_BAR_TEMPLATES = [*APP_RDM_DETAIL_SIDE_BAR_TEMPLATES, "oarepo_ark/ark_identifier.html"]
```

The template reads `record_ui` (else `record`), `files` and the export formats - oarepo-ui's
`model.exports` (only `display`ed ones), else `APP_RDM_RECORD_EXPORTERS` - and renders nothing
for records without an ARK. Replace the dropdown through react-overridable:

```javascript
import { overrideStore } from "react-overridable";
overrideStore.add("OarepoArk.ArkLinkDropdown", MyDropdown);  // props: options [{name, url}], kind
```
"""

from __future__ import annotations
