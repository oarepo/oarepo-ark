# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""``invenio ark`` commands on records published through RDM's records service."""

from __future__ import annotations

from copy import copy, deepcopy
from importlib.metadata import entry_points
from uuid import uuid4

import pytest
from invenio_access.permissions import system_identity
from invenio_db import db
from invenio_pidstore.models import PersistentIdentifier
from invenio_rdm_records.proxies import current_rdm_records_service as service
from invenio_rdm_records.records.api import RDMRecord
from invenio_records_resources.proxies import current_service_registry
from invenio_records_resources.records.systemfields import PIDField

from oarepo_ark.cli import ark, published_record_ids
from oarepo_ark.handlers.rdm_pids import ARKPIDProvider
from tests.conftest import MINIMAL_RECORD, OtherMetadata, ThesisHandler, ark_of, expected_ark, strip_ark


@pytest.fixture
def runner(app):
    return app.test_cli_runner()


@pytest.fixture
def unminted(publish):
    """Publish a record and remove its ARK, as if published before oarepo-ark was installed."""

    def _unminted(**kwargs):
        item = publish(**kwargs)
        strip_ark(item.id)
        return item.id

    return _unminted


def test_registered_as_invenio_command():
    assert [ep.load() for ep in entry_points(group="flask.commands", name="ark")] == [ark]


def test_check(runner, running_app):
    """The effective configuration, then the services."""
    result = runner.invoke(ark, ["check"])
    assert result.exit_code == 0, result.output
    assert result.output == (
        "NAAN:               12345\n"
        "Default shoulder:   r1\n"
        "Shoulder overrides: -\n"
        "Default handler:    oarepo_ark.handlers.rdm:RDMRecordHandler\n"
        "Handler overrides:  -\n"
        "Public resolver:    https://n2t.net/\n"
        "erc-support:        who=Example Repository; what=Permanent: Stable Content:; when=(:unav); "
        "where=https://repo.example.org/\n"
        "\n"
        "Services minting ARKs (shoulder, service, handler):\n"
        "r1\trecords\tRDMRecordHandler('records')\n"
    )


def services_part(output):
    return output.split("Services minting ARKs (shoulder, service, handler):\n")[1]


def test_check_lists_every_service_with_its_shoulder(runner, app, running_app):
    """E.g. oarepo-rdm's global ``records`` service (a facade) simply gets the default shoulder."""
    current_service_registry.register(service, "facade")
    app.config["ARK_SHOULDERS"] = {"facade": "b2"}
    app.config["ARK_HANDLERS"] = {"facade": ThesisHandler}
    result = runner.invoke(ark, ["check"])
    assert result.exit_code == 0, result.output
    assert "Shoulder overrides: facade=b2\n" in result.output
    assert "Handler overrides:  facade=tests.conftest:ThesisHandler\n" in result.output
    assert services_part(result.output).splitlines() == [
        "r1\trecords\tRDMRecordHandler('records')",
        "b2\tfacade\tThesisHandler('facade')",
    ]


def test_check_requires_default_shoulder(runner, app, running_app):
    app.config["ARK_DEFAULT_SHOULDER"] = None
    result = runner.invoke(ark, ["check"])
    assert result.exit_code == 1
    assert "Default shoulder:   -\n" in result.output  # the configuration is printed before the error
    assert "ARK_DEFAULT_SHOULDER is required when ARK_NAAN is set" in result.output
    assert "Services minting ARKs" not in result.output


def test_check_invalid_config(runner, app, running_app):
    app.config["ARK_SHOULDERS"] = {"records": "r"}
    result = runner.invoke(ark, ["check"])
    assert result.exit_code == 1
    assert "is not primordinal" in result.output


def test_check_disabled(runner, app, running_app):
    app.config["ARK_NAAN"] = None
    assert runner.invoke(ark, ["check"]).output == "ARK_NAAN is not set, ARKs are disabled.\n"


def test_show(runner, publish):
    ark_ = ark_of(publish())
    result = runner.invoke(ark, ["show", f"https://n2t.net/{ark_}/files/a-b.csv"])
    assert result.exit_code == 0, result.output
    assert f"ark:\t{ark_}\nqualifier:\t/files/a-b.csv\nstatus:\tREGISTERED\n" in result.output
    assert "handler:\tRDMRecordHandler('records')\n" in result.output
    assert runner.invoke(ark, ["show", "ark:12345/nothing"]).exit_code == 1
    assert runner.invoke(ark, ["show", "nothing"]).exit_code == 1


def test_mint_one(runner, unminted):
    record_id = unminted()
    out = runner.invoke(ark, ["mint", "--service", "records", record_id])
    assert out.exit_code == 0, out.output
    ark_ = expected_ark(record_id)
    assert out.output == f"records\t{record_id}\t{ark_}\tminted\n"
    record = RDMRecord.pid.resolve(record_id)
    assert record["pids"]["ark"] == {"identifier": ark_, "provider": "ark"}
    assert not record.has_draft  # updated in place: no edit / publish
    pid = PersistentIdentifier.get("ark", ark_)
    assert (pid.status.name, pid.pid_provider, pid.object_uuid) == ("REGISTERED", "ark", record.id)
    RDMRecord.index.refresh()
    hits = service.search(system_identity, params={"q": f'pids.ark.identifier:"{ark_}"'})
    assert [hit["id"] for hit in hits] == [record_id]  # reindexed


@pytest.mark.parametrize(
    "args",
    [["mint", "abcde-12345"], ["mint", "--pid-type", "recid"], ["mint", "--service", "x", "--service", "y", "id"]],
)
def test_mint_ids_and_pid_type_need_one_service(runner, running_app, args):
    out = runner.invoke(ark, args)
    assert out.exit_code == 1
    assert "RECORD_IDS and --pid-type need exactly one --service" in out.output


def test_mint_record_with_open_draft(runner, unminted):
    """Only the published record changes; RDM's publish of the draft restores the required ARK."""
    record_id = unminted()
    service.edit(system_identity, record_id)
    out = runner.invoke(ark, ["mint", "--service", "records", record_id])
    assert out.output == f"records\t{record_id}\t{expected_ark(record_id)}\tminted\n"
    assert ark_of(service.publish(system_identity, record_id)) == expected_ark(record_id)


def test_mint_uses_the_configured_provider(runner, app, unminted):
    class CustomProvider(ARKPIDProvider):
        pass

    record_id = unminted()
    app.config["RDM_PERSISTENT_IDENTIFIER_PROVIDERS"] = [
        *app.config["RDM_PERSISTENT_IDENTIFIER_PROVIDERS"],
        CustomProvider("my-ark"),
    ]
    pids = app.config["RDM_PERSISTENT_IDENTIFIERS"]
    app.config["RDM_PERSISTENT_IDENTIFIERS"] = {**pids, "ark": {**pids["ark"], "providers": ["my-ark"]}}
    assert runner.invoke(ark, ["mint", "--service", "records", record_id]).exit_code == 0
    assert RDMRecord.pid.resolve(record_id)["pids"]["ark"]["provider"] == "my-ark"
    assert PersistentIdentifier.get("ark", expected_ark(record_id)).pid_provider == "my-ark"


def test_mint_skips(runner, publish, unminted):
    minted = publish()
    restricted = publish(access="restricted").id  # RDM's publish gave it no ARK either
    deleted = unminted()
    service.delete_record(system_identity, deleted, {})
    out = runner.invoke(ark, ["mint", "--service", "records", minted.id, restricted, deleted])
    assert out.exit_code == 0, out.output
    assert out.output.splitlines() == [
        f"records\t{minted.id}\t{ark_of(minted)}\tskipped: has an ARK",
        f"records\t{restricted}\t\tskipped: restricted",
        f"records\t{deleted}\t\tskipped: deleted",
    ]


def test_mint_all(runner, publish, unminted):
    """Default: all services minting ARKs; records with an ARK are not even listed."""
    record_id = unminted()
    publish()
    out = runner.invoke(ark, ["mint"])
    assert out.exit_code == 0, out.output
    assert out.output == f"records\t{record_id}\t{expected_ark(record_id)}\tminted\n"
    assert runner.invoke(ark, ["mint"]).output == ""


def test_mint_failure_leaves_nothing_behind(runner, unminted, monkeypatch):
    """The PID and the record change are one unit of work: a failed record is retried next run."""
    failing, ok = unminted(), unminted()
    original_commit = RDMRecord.commit

    def commit(self, *args, **kwargs):
        if self.pid.pid_value == failing:
            raise ValueError("cannot commit")
        return original_commit(self, *args, **kwargs)

    monkeypatch.setattr(RDMRecord, "commit", commit)
    out = runner.invoke(ark, ["mint"])
    assert out.exit_code == 1
    assert f"records\t{failing}\t\tERROR: cannot commit" in out.output
    assert f"records\t{ok}\t{expected_ark(ok)}\tminted" in out.output
    assert PersistentIdentifier.query.filter_by(pid_value=expected_ark(failing)).count() == 0
    assert list(published_record_ids(RDMRecord.model_cls, "recid")) == [failing]


def test_mint_unknown_service(runner, running_app):
    out = runner.invoke(ark, ["mint", "--service", "nope"])
    assert out.exit_code == 1
    assert "Unknown service nope" in out.output


def test_published_record_ids(publish, unminted):
    """Registered PIDs of the type, in the table, without an ARK, across batch boundaries."""
    ids = [unminted() for _ in range(4)]
    publish()  # has an ARK
    service.create(system_identity, deepcopy(MINIMAL_RECORD))  # unpublished draft: recid PID not registered
    removed = unminted()
    RDMRecord.pid.resolve(removed).model.is_deleted = True  # removed from the table
    other = uuid4()  # registered recid PID of a record in another model's table
    db.session.add(OtherMetadata(id=other, json={}))
    PersistentIdentifier.create("recid", "other0-00000", object_type="rec", object_uuid=other, status="R")
    PersistentIdentifier.create("recid", "orphan-00000", object_type="rec", object_uuid=uuid4(), status="R")
    db.session.commit()
    assert list(published_record_ids(RDMRecord.model_cls, "recid", batch_size=2)) == ids
    assert list(published_record_ids(OtherMetadata, "recid")) == ["other0-00000"]
    assert list(published_record_ids(RDMRecord.model_cls, "other")) == []


def test_mint_pid_type_option(runner, unminted):
    record_id = unminted()
    record = RDMRecord.pid.resolve(record_id)
    PersistentIdentifier.create("other", "zz", object_type="rec", object_uuid=record.id, status="R")
    db.session.commit()
    out = runner.invoke(ark, ["mint", "--service", "records", "--pid-type", "other"])
    assert out.output.startswith("records\tzz\t\tERROR:")  # "zz" is not a record id of the service


def test_mint_pid_type_not_determinable(runner, running_app, monkeypatch):
    monkeypatch.setattr(RDMRecord.pid.field, "_pid_type", None)
    out = runner.invoke(ark, ["mint", "--service", "records"])
    assert out.exit_code == 1
    assert "Cannot determine the PID type of records, use --pid-type" in out.output


class FacadeRecord(RDMRecord):
    """Like oarepo-rdm's patched RDMRecord: a pid field without a PID type."""

    pid = PIDField()


def test_mint_all_skips_services_without_pid_type(runner, unminted):
    """E.g. oarepo-rdm's global ``records`` service: it owns no records; the run goes on."""
    facade = copy(service)
    facade.config = copy(service.config)
    # record_cls is a descriptor on RDM's config class: override it in a subclass
    facade.config.__class__ = type("FacadeConfig", (type(service.config),), {"record_cls": FacadeRecord})
    current_service_registry.register(facade, "facade")
    record_id = unminted()
    out = runner.invoke(ark, ["mint"])
    assert out.exit_code == 0, out.output
    assert out.output.splitlines() == [
        f"records\t{record_id}\t{expected_ark(record_id)}\tminted",
        "facade: no PID type, skipped (use --service facade --pid-type T)",
    ]
    explicit = runner.invoke(ark, ["mint", "--service", "facade"])
    assert explicit.exit_code == 1
    assert "Cannot determine the PID type of facade, use --pid-type" in explicit.output
