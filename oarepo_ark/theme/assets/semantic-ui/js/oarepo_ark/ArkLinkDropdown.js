// SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
// SPDX-License-Identifier: MIT

import React, { Component } from "react";
import PropTypes from "prop-types";
import { Dropdown } from "semantic-ui-react";
import Overridable from "react-overridable";
import { CopyButton } from "@js/invenio_app_rdm/components/CopyButton";
import { i18next } from "@translations/oarepo_ark/i18next";

/**
 * Dropdown of ARK URLs (export formats or files of a record) with a button copying the
 * selected one - laid out like invenio-app-rdm's ExportDropdown.
 */
export class ArkLinkDropdown extends Component {
  constructor(props) {
    super(props);
    const { options } = this.props;
    this.state = { selectedUrl: options[0]?.url };
  }

  render() {
    const { options, kind } = this.props;
    const { selectedUrl } = this.state;
    const ariaLabel =
      kind === "files"
        ? i18next.t("Select a file of this ARK")
        : i18next.t("Select an export format of this ARK");
    return (
      <div className="auto-column-grid no-wrap">
        <Dropdown
          aria-label={ariaLabel}
          selection
          fluid
          selectOnNavigation={false}
          options={options.map((option, index) => ({
            key: `option-${index}`,
            text: option.name,
            value: option.url,
          }))}
          defaultValue={selectedUrl}
          onChange={(event, data) => this.setState({ selectedUrl: data.value })}
        />
        {/* text: copy the ARK URL itself (url= would fetch it and copy the content) */}
        <CopyButton text={selectedUrl} />
      </div>
    );
  }
}

ArkLinkDropdown.propTypes = {
  options: PropTypes.arrayOf(
    PropTypes.shape({ name: PropTypes.string.isRequired, url: PropTypes.string.isRequired })
  ).isRequired,
  kind: PropTypes.oneOf(["exports", "files"]).isRequired,
};

export default Overridable.component("OarepoArk.ArkLinkDropdown", ArkLinkDropdown);
