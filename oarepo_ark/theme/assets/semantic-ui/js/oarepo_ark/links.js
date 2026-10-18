// SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
// SPDX-License-Identifier: MIT

// Mounts the ARK box dropdowns rendered by the template oarepo_ark/ark_identifier.html.
import React from "react";
import ReactDOM from "react-dom";
import { OverridableContext, overrideStore } from "react-overridable";
import ArkLinkDropdown from "@js/oarepo_ark/ArkLinkDropdown";

const mount = () => {
  document.querySelectorAll(".oarepo-ark-links").forEach((element) => {
    ReactDOM.render(
      <OverridableContext.Provider value={overrideStore.getAll()}>
        <ArkLinkDropdown options={JSON.parse(element.dataset.options)} kind={element.dataset.kind} />
      </OverridableContext.Provider>,
      element
    );
  });
};

// This script is included from the sidebar, i.e. before the page's base scripts. invenio-theme's
// theme.js initializes every ".ui.dropdown" present at that moment with the Semantic UI jQuery plugin,
// whose handlers would fight React's (the menu would not close on selection). Mounting on
// DOMContentLoaded - after all synchronous scripts ran - keeps the React dropdowns out of its reach.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount);
} else {
  mount();
}
