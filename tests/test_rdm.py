# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""ARK minting through invenio-rdm-records' PID machinery and the default RDM handler."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from invenio_access.permissions import system_identity
from invenio_pidstore.models import PersistentIdentifier, PIDStatus
from invenio_rdm_records.proxies import current_rdm_records_service as service
from invenio_rdm_records.services.errors import ValidationErrorWithMessageAsList

from oarepo_ark.handlers.rdm import RDMRecordHandler
from oarepo_ark.handlers.rdm_pids import ark_pid_config, ark_pid_providers
from tests.conftest import MINIMAL_RECORD, ark_of, expected_ark


def ark_errors(error: ValidationErrorWithMessageAsList) -> list[str]:
    return [str(m) for e in error.messages if e["field"] == "pids.ark" for m in e["messages"]]


def with_ark(draft, identifier):
    """Draft data carrying a user-supplied ARK."""
    return {**draft.data, "pids": {"ark": {"identifier": identifier, "provider": "ark"}}}


def test_publish_mints_ark(publish):
    item = publish()
    ark = ark_of(item)
    assert ark == expected_ark(item.id)
    assert item.data["pids"]["ark"] == {"identifier": ark, "provider": "ark"}
    pid = PersistentIdentifier.get("ark", ark)
    assert (pid.status, pid.pid_provider, pid.object_uuid) == (PIDStatus.REGISTERED, "ark", item._record.id)


def test_restricted_record_gets_no_ark(publish):
    """RDM does not create required PIDs for records published as restricted."""
    assert "ark" not in publish(access="restricted").data["pids"]


def test_new_version_gets_new_ark(publish):
    first = publish()
    draft = service.new_version(system_identity, first.id)
    data = {**draft.data, "metadata": {**draft.data["metadata"], "publication_date": "2021-01-01"}}
    service.update_draft(system_identity, draft.id, data)
    second = service.publish(system_identity, draft.id)
    assert ark_of(second) == expected_ark(second.id)
    assert ark_of(second) != ark_of(first)


def test_edit_and_republish_keeps_ark(publish):
    item = publish()
    service.edit(system_identity, item.id)
    assert ark_of(service.publish(system_identity, item.id)) == ark_of(item)


def test_record_minted_under_previous_shoulder_stays_editable(app, publish):
    item = publish()
    app.config["ARK_SHOULDERS"] = {"records": "b2"}
    service.edit(system_identity, item.id)
    assert ark_of(service.publish(system_identity, item.id)) == ark_of(item)


@pytest.mark.parametrize(
    "identifier",
    [
        "ark:12345/r1whatever",  # our NAAN and shoulder, not this record's ARK
        "ark:12345/b9abcde12345",  # our NAAN, other name
    ],
)
def test_supplied_ark_is_rejected(running_app, identifier):
    """Users cannot choose ARKs: the draft's ``pids`` come from user input."""
    draft = service.create(system_identity, deepcopy(MINIMAL_RECORD))
    service.update_draft(system_identity, draft.id, with_ark(draft, identifier))
    with pytest.raises(ValidationErrorWithMessageAsList) as e:
        service.publish(system_identity, draft.id)
    assert ark_errors(e.value) == ["ARKs are assigned by the repository."]
    assert PersistentIdentifier.query.filter_by(pid_type="ark").count() == 0


def test_supplied_computed_ark_is_accepted(running_app):
    """E.g. the hyphenated / ``ark:/`` form of the ARK the record gets anyway."""
    draft = service.create(system_identity, deepcopy(MINIMAL_RECORD))
    ark = expected_ark(draft.id)
    service.update_draft(
        system_identity, draft.id, with_ark(draft, f"https://n2t.net/ark:/12345/r1{draft.id}{ark[-1]}")
    )
    assert ark_of(service.publish(system_identity, draft.id)) == ark


def test_ark_of_another_record_is_rejected(publish):
    other = publish()
    draft = service.create(system_identity, deepcopy(MINIMAL_RECORD))
    service.update_draft(system_identity, draft.id, with_ark(draft, ark_of(other)))
    with pytest.raises(ValidationErrorWithMessageAsList) as e:
        service.publish(system_identity, draft.id)
    assert ark_errors(e.value) == ["ARKs are assigned by the repository."]


def test_configuration_errors_become_pids_errors(app, running_app):
    """A broken configuration fails the publish with a ``pids.ark`` error, not deep in ``generate_id``."""
    app.config["ARK_DEFAULT_SHOULDER"] = None  # changed after finalize_app validated it
    draft = service.create(system_identity, deepcopy(MINIMAL_RECORD))
    with pytest.raises(ValidationErrorWithMessageAsList) as e:
        service.publish(system_identity, draft.id)
    assert ark_errors(e.value) == ["ARK_DEFAULT_SHOULDER is not set"]


def test_handler_urls_and_erc(publish):
    item = service.read(system_identity, publish().id)
    handler = RDMRecordHandler("records")
    base = f"https://repo.example.org/records/{item.id}"
    assert handler.landing_url(item) == base
    assert handler.api_url(item) == f"https://repo.example.org/api/records/{item.id}"
    assert handler.variant_url(item, "datacite-json") == f"{base}/export/datacite-json"
    files = f"https://repo.example.org/api/records/{item.id}/files"
    assert handler.component_url(item, "/files/my data.csv") == f"{files}/my%20data.csv/content"
    assert handler.component_url(item, "/files/dir/a.txt") == f"{files}/dir/a.txt/content"
    assert handler.component_url(item, "/files/") is None
    assert handler.component_url(item, "/versions") is None
    erc = handler.erc(item)
    assert (erc.who, erc.what, erc.when) == ("Brown, Troy; Troy Inc.", "A Romans story", "2020-06-01")


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"metadata": None},
        {"metadata": {"creators": "x", "title": {"en": "T"}, "publication_date": 2020}},
        {"metadata": {"creators": [{"name": "A"}, "B", {"person_or_org": {"name": ["C"]}}]}},
    ],
)
def test_erc_of_other_metadata_structures(data):
    """Models whose metadata is not RDM-shaped get ``(:unav)`` instead of an error."""
    erc = RDMRecordHandler("records").erc(SimpleNamespace(data=data))
    assert (erc.who, erc.what, erc.when) == ("(:unav)", "(:unav)", "(:unav)")


def test_pid_config(app):
    assert [p.name for p in ark_pid_providers()] == ["ark"]
    config = ark_pid_config(app.extensions["oarepo-ark"])
    assert config["providers"] == ["ark"]
    assert config["required"] is True
    assert config["validator"]("ark:/12345/x")
    assert not config["validator"]("ark:99999/x")
    assert config["normalizer"]("ark:/12345/a-b") == "ark:12345/ab"
