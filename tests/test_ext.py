# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""Extension: configuration, services, handlers and ARK computation on InvenioRDM."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from types import SimpleNamespace

import pytest
from flask import Flask, render_template_string
from invenio_access.permissions import system_identity
from invenio_pidstore.models import PersistentIdentifier
from invenio_rdm_records import config as rdm_config
from invenio_rdm_records.proxies import current_rdm_records_service as service
from invenio_rdm_records.records.api import RDMDraft, RDMRecord
from invenio_rdm_records.services.config import RDMRecordServiceConfig
from invenio_records_resources.proxies import current_service_registry

from oarepo_ark.api import ARKHandler
from oarepo_ark.errors import ARKConfigurationError, ARKError, ARKRecordError
from oarepo_ark.ext import OARepoARK, finalize_app
from oarepo_ark.handlers.rdm import RDMRecordHandler
from oarepo_ark.handlers.rdm_pids import ARKPIDProvider, ark_pid_config
from oarepo_ark.proxies import current_ark
from tests.conftest import MINIMAL_RECORD, ThesisHandler, ark_of, expected_ark, use_handler


class ModelRecord(RDMRecord):
    """Record class of another model (no service in this app)."""


@pytest.fixture
def draft(running_app):
    """Create an RDM draft (the provider computes the ARK from it on publish)."""
    return service.create(system_identity, deepcopy(MINIMAL_RECORD))._record


def test_defaults_do_not_override_host_config():
    app = Flask("test")
    app.config["ARK_PUBLIC_RESOLVER"] = "https://resolver.example/"
    OARepoARK(app)
    assert app.config["ARK_PUBLIC_RESOLVER"] == "https://resolver.example/"
    assert app.config["ARK_NAAN"] is None


@pytest.mark.parametrize(
    ("config", "error"),
    [
        ({"ARK_NAAN": "1234"}, "is not a 5 character NAAN"),
        ({"ARK_SHOULDERS": {"records": "r"}}, "records: shoulder 'r' is not primordinal"),
        ({"ARK_SHOULDERS": {"records": "ra1"}}, "is not primordinal"),
        ({"ARK_DEFAULT_SHOULDER": "x"}, "ARK_DEFAULT_SHOULDER: shoulder 'x' is not primordinal"),
        ({"ARK_DEFAULT_SHOULDER": None}, "ARK_DEFAULT_SHOULDER is required when ARK_NAAN is set"),
        ({"ARK_SHOULDERS": {"a": "b2", "b": "b2"}}, "a and b share shoulder 'b2'"),
        ({"ARK_SHOULDERS": {"records": "r1"}}, "ARK_DEFAULT_SHOULDER and records share shoulder 'r1'"),
    ],
)
def test_validate_config(app, config, error):
    app.config.update(config)
    with pytest.raises(ARKConfigurationError, match=error):
        current_ark.validate_config()


def test_valid_config(app):
    app.config["ARK_SHOULDERS"] = {"theses": "b2", "datasets": "d3"}
    current_ark.validate_config()


def test_shoulder(app):
    """The default shoulder, or the service's override."""
    assert current_ark.shoulder("records") == "r1"
    app.config["ARK_SHOULDERS"] = {"theses": "b2"}
    assert current_ark.shoulder("theses") == "b2"
    assert current_ark.shoulder("records") == "r1"
    app.config["ARK_DEFAULT_SHOULDER"] = None  # not validated (finalize_app would refuse it)
    with pytest.raises(ARKConfigurationError, match="ARK_DEFAULT_SHOULDER is not set"):
        current_ark.shoulder("records")


def test_ark_services_of_rdm(app):
    """Only services whose publish runs RDM's PIDsComponent.

    ``record-media-files`` inherits RDM's PID configuration (and shares the record table) but mints
    nothing; file services have no PID configuration at all.
    """
    assert "ark" in current_service_registry.get("record-media-files").config.pids_providers
    assert current_ark.ark_service_ids() == ["records"]


def test_service_id_for_record_exact_type(app):
    """Record and draft belong to ``records``; a subclass (another model) does not."""
    assert current_ark.service_id_for_record(RDMRecord({})) == "records"
    assert current_ark.service_id_for_record(RDMDraft({})) == "records"
    assert current_ark.service_id_for_record(ModelRecord({})) is None


def test_service_lookup_misses_are_not_cached(app):
    services = current_service_registry._services
    records = services.pop("records")
    assert current_ark.service_id_for_record(RDMRecord({})) is None
    services["records"] = records  # registered later, e.g. at finalize time
    assert current_ark.service_id_for_record(RDMRecord({})) == "records"


@pytest.fixture
def counted_pids_providers():
    """Count evaluations of RDM's ``pids_providers`` descriptor (monkeypatch cannot read it from the class)."""
    descriptor = RDMRecordServiceConfig.__dict__["pids_providers"]
    evaluated = []

    class Counting:
        def __get__(self, obj, objtype=None):
            evaluated.append(obj)
            return descriptor.__get__(obj, objtype)

    RDMRecordServiceConfig.pids_providers = Counting()
    yield evaluated
    RDMRecordServiceConfig.pids_providers = descriptor


def test_ark_services_are_cached_until_a_service_is_registered(app, counted_pids_providers):
    """``config.pids_providers`` (rebuilt on every access) is evaluated once per registry size."""
    for _ in range(3):
        current_ark.ark_service_ids()
    first = len(counted_pids_providers)
    assert first > 0
    for _ in range(3):
        current_ark.ark_service_ids()
    assert len(counted_pids_providers) == first

    current_service_registry.register(service, "late")  # e.g. at finalize time
    assert current_ark.ark_service_ids() == ["records", "late"]
    assert len(counted_pids_providers) > first


def test_handler_for_pid_probes_the_own_shoulder_first(app, publish):
    """The service whose shoulder starts the ARK is probed before others holding the same table."""
    ark = ark_of(publish())
    services = current_service_registry._services
    records = services.pop("records")
    services["alias"] = records  # registered first now
    services["records"] = records
    app.config["ARK_SHOULDERS"] = {"alias": "b2"}  # records keeps the default r1
    assert current_ark.handler_for_pid(PersistentIdentifier.get("ark", ark)).service_id == "records"


def test_handler_default_and_configured(app):
    """A handler class or its import string, created per service with the service id."""
    assert type(current_ark.handler("records")) is RDMRecordHandler
    assert current_ark.handler("records") is current_ark.handler("records")  # cached
    app.config["ARK_HANDLERS"] = {"theses": ThesisHandler, "datasets": "tests.conftest:ThesisHandler"}
    for service_id in ("theses", "datasets"):
        handler = current_ark.handler(service_id)
        assert (type(handler), handler.service_id) == (ThesisHandler, service_id)
    app.config["ARK_DEFAULT_HANDLER"] = "tests.conftest:ThesisHandler"
    assert type(current_ark.handler("others")) is ThesisHandler


def test_handler_instances_are_refused(app):
    """An instance would be bound to whichever service it was created for."""
    app.config["ARK_HANDLERS"] = {"theses": ThesisHandler("records")}
    with pytest.raises(ARKConfigurationError, match="must be a handler class or its import string"):
        current_ark.handler("theses")


def test_compute_ark(draft):
    """From the draft (as on publish): the record id without hyphens + check character."""
    assert current_ark.compute_ark(draft) == expected_ark(draft.pid.pid_value)


def test_compute_ark_shoulder_override(app, draft):
    app.config["ARK_SHOULDERS"] = {"records": "b2"}
    assert current_ark.compute_ark(draft) == expected_ark(draft.pid.pid_value, "b2")


def test_compute_ark_errors(app, draft):
    with pytest.raises(ARKRecordError, match="does not belong to a service using ARKs"):
        current_ark.compute_ark(ModelRecord({}))

    class StructuralBlade(ARKHandler):
        def get_blade(self, record):
            return "a/b"

    use_handler(app, StructuralBlade)
    with pytest.raises(ARKRecordError, match="contains structural characters"):
        current_ark.compute_ark(draft)


def test_custom_blade(app, draft):
    class UUIDBlade(ARKHandler):
        def get_blade(self, record):
            return str(record.id)

    use_handler(app, UUIDBlade)
    assert current_ark.compute_ark(draft).startswith(f"ark:12345/r1{str(draft.id).replace('-', '')}")


def test_ark_for_record(publish):
    item = publish()
    assert current_ark.ark_for_record(item._record) == ark_of(item)
    assert current_ark.ark_for_record(item.data) == ark_of(item)  # UI dict
    assert current_ark.ark_for_record(publish(access="restricted")._record) is None


def test_template_globals(app, publish):
    item = publish()
    ark = ark_of(item)
    assert render_template_string("{{ ark_identifier(record) }}", record=item.data) == ark
    assert render_template_string("{{ ark_url(record) }}", record=item.data) == f"https://n2t.net/{ark}"
    assert render_template_string("{{ ark_identifier(record) }}|{{ ark_url(record) }}", record={}) == "None|None"
    app.config["ARK_NAAN"] = None
    assert render_template_string("{{ ark_identifier(record) }}", record=item.data) == "None"


def test_landing_page_snippet(app, publish):
    """The badge shows the ARK itself, the link goes to the public resolver (also one with a path)."""
    item = publish()
    ark = ark_of(item)
    app.config["ARK_PUBLIC_RESOLVER"] = "https://resolver.example.org/resolve/"
    html = render_template_string('{% include "oarepo_ark/ark_identifier.html" %}', record=item.data)
    url = f"https://resolver.example.org/resolve/{ark}"
    assert f'<a href="{url}" title="{url}">' in html
    assert f'src="/ark-badge/{ark}.svg" alt="{ark}"' in html
    assert render_template_string('{% include "oarepo_ark/ark_identifier.html" %}', record={}).strip() == ""


def test_error_classes():
    """Callers can tell misconfiguration from records ARKs cannot be computed for."""
    assert issubclass(ARKConfigurationError, ARKError)
    assert issubclass(ARKRecordError, ARKError)
    assert not issubclass(ARKRecordError, ARKConfigurationError)


def test_is_own_ark(app):
    assert current_ark.is_own_ark("ark:/12345/x")
    assert current_ark.is_own_ark("https://n2t.net/ark:12345/x")
    assert not current_ark.is_own_ark("ark:54321/x")
    assert not current_ark.is_own_ark("garbage")


def test_finalize_app_registered_the_provider(app):
    """invenio-app ran oarepo-ark's finalize_app entry point."""
    assert any(p.name == "ark" for p in app.config["RDM_PERSISTENT_IDENTIFIER_PROVIDERS"])
    assert app.config["RDM_PERSISTENT_IDENTIFIERS"]["ark"]["required"] is True
    assert "ark" in service.config.pids_required


@pytest.fixture
def rdm_app():
    """Config as left by invenio-rdm-records: values are its module-level objects."""
    app = Flask("rdm")
    app.config.update(
        ARK_NAAN="12345",
        ARK_DEFAULT_SHOULDER="r1",
        RDM_PERSISTENT_IDENTIFIER_PROVIDERS=rdm_config.RDM_PERSISTENT_IDENTIFIER_PROVIDERS,
        RDM_PERSISTENT_IDENTIFIERS=rdm_config.RDM_PERSISTENT_IDENTIFIERS,
    )
    OARepoARK(app)
    return app


def test_finalize_app_does_not_mutate_rdm_defaults(rdm_app):
    finalize_app(rdm_app)
    finalize_app(rdm_app)  # idempotent
    names = [p.name for p in rdm_app.config["RDM_PERSISTENT_IDENTIFIER_PROVIDERS"]]
    assert names == ["datacite", "crossref", "external", "oai", "ark"]
    assert list(rdm_app.config["RDM_PERSISTENT_IDENTIFIERS"]) == ["doi", "oai", "ark"]
    # RDM's module defaults, shared by all apps in the process, are untouched
    assert "ark" not in rdm_config.RDM_PERSISTENT_IDENTIFIERS
    assert all(p.name != "ark" for p in rdm_config.RDM_PERSISTENT_IDENTIFIER_PROVIDERS)


def test_finalize_app_validates(rdm_app):
    rdm_app.config["ARK_NAAN"] = "1"
    with pytest.raises(ARKConfigurationError):
        finalize_app(rdm_app)


def test_finalize_app_inert_without_naan(rdm_app):
    rdm_app.config["ARK_NAAN"] = None
    finalize_app(rdm_app)
    assert "ark" not in rdm_app.config["RDM_PERSISTENT_IDENTIFIERS"]


def test_finalize_app_keeps_own_ark_wiring(rdm_app):
    """A deployment that defines the ``ark`` provider / scheme itself keeps them."""
    own_provider = ARKPIDProvider("ark", label="My ARK")
    own_scheme = {**ark_pid_config(rdm_app.extensions["oarepo-ark"]), "required": False}
    rdm_app.config.update(
        RDM_PERSISTENT_IDENTIFIER_PROVIDERS=[own_provider],
        RDM_PERSISTENT_IDENTIFIERS={"ark": own_scheme},
    )
    finalize_app(rdm_app)
    assert rdm_app.config["RDM_PERSISTENT_IDENTIFIER_PROVIDERS"] == [own_provider]
    assert rdm_app.config["RDM_PERSISTENT_IDENTIFIERS"] == {"ark": own_scheme}


def test_naan_from_environment_is_a_string(app, draft):
    """invenio-config parses ``INVENIO_ARK_NAAN=12345`` into an integer."""
    app.config["ARK_NAAN"] = 12345
    assert current_ark.naan == "12345"
    current_ark.validate_config()
    assert current_ark.compute_ark(draft) == expected_ark(draft.pid.pid_value)


def test_ark_for_result_item(publish):
    item = publish()
    assert current_ark.ark_for_record(service.read(system_identity, item.id)) == ark_of(item)


def test_sidebar_snippet_in_detail_page_context(publish):
    """Detail pages (RDM, oarepo-ui) have ``record_ui`` (dict) and ``record`` (a RecordItem)."""
    item = service.read(system_identity, publish().id)
    ark = ark_of(item)
    html = render_template_string(
        '{% include "oarepo_ark/ark_identifier.html" %}', record_ui=item.to_dict(), record=item
    )
    assert '<div class="sidebar-container">' in html
    assert '<h2 class="ui medium top attached header mt-0">ARK</h2>' in html
    assert '<dt class="ui tiny header">Landing page</dt>' in html
    assert f'<img id="record-ark-badge" src="/ark-badge/{ark}.svg" alt="{ark}" />' in html
    # placeholder invenio-app-rdm's theme.js turns into its CopyButton (as for the DOI)
    assert f'class="copy-doi-button" data-value="https://n2t.net/{ark}" data-size="mini"' in html


def ark_link_options(html, kind):
    """Return the options of an ARK box dropdown (``data-options`` of its ``.oarepo-ark-links`` element)."""
    match = re.search(rf"data-kind=\"{kind}\" data-options='([^']*)'", html)
    return None if match is None else json.loads(match.group(1))


def test_sidebar_dropdowns_of_exports_and_files(app, publish):
    """InvenioRDM: export formats from APP_RDM_RECORD_EXPORTERS, files from the page's ``files``."""
    item = service.read(system_identity, publish().id)
    url = f"https://n2t.net/{ark_of(item)}"
    app.config["APP_RDM_RECORD_EXPORTERS"] = {"json": {"name": "JSON"}, "datacite-json": {"name": "DataCite JSON"}}
    html = render_template_string(
        '{% include "oarepo_ark/ark_identifier.html" %}',
        record_ui=item.to_dict(),
        files={"entries": [{"key": "my data.csv"}, {"key": "dir/a.txt"}]},
    )
    assert ark_link_options(html, "exports") == [
        {"name": "JSON", "url": f"{url}.json"},
        {"name": "DataCite JSON", "url": f"{url}.datacite-json"},
    ]
    assert ark_link_options(html, "files") == [
        {"name": "my data.csv", "url": f"{url}/files/my%20data.csv"},
        {"name": "dir/a.txt", "url": f"{url}/files/dir/a.txt"},
    ]
    assert '<script src="/static/dist/js/oarepo_ark_links.js"></script>' in html


def test_sidebar_dropdowns_of_oarepo_model_exports(app, publish):
    """oarepo-ui: export formats from ``model.exports`` (only those displayed); no files row without files."""
    item = service.read(system_identity, publish().id)
    url = f"https://n2t.net/{ark_of(item)}"
    model = SimpleNamespace(
        exports=[
            SimpleNamespace(code="ccmm-xml", name="CCMM XML", display=True),
            SimpleNamespace(code="ui_json", name="UI JSON", display=False),
        ]
    )
    html = render_template_string(
        '{% include "oarepo_ark/ark_identifier.html" %}', record_ui=item.to_dict(), model=model, files=None
    )
    assert ark_link_options(html, "exports") == [{"name": "CCMM XML", "url": f"{url}.ccmm-xml"}]
    assert ark_link_options(html, "files") is None


def test_sidebar_without_dropdowns_loads_no_script(app, publish):
    item = service.read(system_identity, publish().id)
    app.config["APP_RDM_RECORD_EXPORTERS"] = {}
    html = render_template_string('{% include "oarepo_ark/ark_identifier.html" %}', record_ui=item.to_dict())
    assert "oarepo-ark-links" not in html
    assert "<script" not in html
