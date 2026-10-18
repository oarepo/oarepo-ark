# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""Pytest configuration.

The tests run against a real InvenioRDM app (pytest-invenio): PostgreSQL, OpenSearch and the
``records`` service of invenio-rdm-records. Start the services first: ``./run.sh start``, or run
``./run.sh test`` which starts and stops them.
"""

from __future__ import annotations

import json
from copy import copy, deepcopy
from typing import TYPE_CHECKING, Any

import pytest
from flask import Blueprint
from invenio_access.permissions import system_identity
from invenio_app.factory import create_app as _create_app
from invenio_cache import current_cache
from invenio_db import db
from invenio_pidstore.models import PersistentIdentifier
from invenio_rdm_records.proxies import current_rdm_records_service
from invenio_rdm_records.records.api import RDMDraft, RDMRecord
from invenio_records.models import RecordMetadataBase
from invenio_records_resources.proxies import current_service_registry
from invenio_search import current_search, current_search_client
from invenio_search.utils import prefix_index
from invenio_vocabularies.proxies import current_service as vocabulary_service

from oarepo_ark.ark import noid_check_char
from oarepo_ark.handlers.rdm import RDMRecordHandler

if TYPE_CHECKING:
    from collections.abc import Callable

    from invenio_records_resources.services.records.results import RecordItem

    from oarepo_ark.api import ERC

NAAN = "12345"

MINIMAL_RECORD = {
    "pids": {},
    "access": {"record": "public", "files": "public"},
    "files": {"enabled": False},
    "metadata": {
        "creators": [
            {"person_or_org": {"family_name": "Brown", "given_name": "Troy", "type": "personal"}},
            {"person_or_org": {"name": "Troy Inc.", "type": "organizational"}},
        ],
        "publication_date": "2020-06-01",
        "publisher": "Acme Inc",
        "resource_type": {"id": "dataset"},
        "title": "A Romans story",
    },
}


class OtherMetadata(db.Model, RecordMetadataBase):
    """A second record table (another model); created by pytest-invenio's ``database`` fixture."""

    __tablename__ = "other_metadata"


class ThesisHandler(RDMRecordHandler):
    """Custom handler: extra ERC element, echoes components."""

    def erc(self, item: Any) -> ERC:
        """RDM's ERC plus an extra element."""
        erc = super().erc(item)
        erc.extra["how"] = "thesis"
        return erc

    def component_url(self, item: Any, path: str) -> str:
        """Echo the component path."""
        return f"{item.links['self_html']}/c{path}"


@pytest.fixture(scope="module")
def app_config(app_config, tmp_path_factory):
    """InvenioRDM configured with an ARK NAAN and the default shoulder ``r1``.

    A minimal webpack manifest stands in for the built assets the ARK box template includes.
    """
    manifest = tmp_path_factory.mktemp("webpack") / "manifest.json"
    manifest.write_text(json.dumps({"oarepo_ark_links.js": "/static/dist/js/oarepo_ark_links.js"}))
    app_config.update(
        WEBPACKEXT_MANIFEST_PATH=str(manifest),
        ARK_NAAN=NAAN,
        ARK_DEFAULT_SHOULDER="r1",
        SITE_UI_URL="https://repo.example.org",
        SITE_API_URL="https://repo.example.org/api",
        THEME_FRONTPAGE=False,
        THEME_SITENAME="Example Repository",
        RECORDS_REFRESOLVER_CLS="invenio_records.resolver.InvenioRefResolver",
        RECORDS_REFRESOLVER_STORE="invenio_jsonschemas.proxies.current_refresolver_store",
    )
    return app_config


def create_rdm_ui_blueprint(app: Any) -> Blueprint:
    """UI routes RDM's links point to (``self_html``, ...), as in invenio-rdm-records' own tests.

    invenio-rdm-records does not depend on invenio-app-rdm, which normally provides them.
    """
    blueprint = Blueprint("invenio_app_rdm_records", __name__)
    for rule, endpoint in [
        ("/records/<pid_value>", "record_detail"),
        ("/records/<pid_value>/latest", "record_latest"),
        ("/records/<pid_value>/files/<path:filename>", "record_file_download"),
        ("/records/<pid_value>/preview/<path:filename>", "record_file_preview"),
        ("/uploads/<pid_value>", "deposit_edit"),
    ]:
        blueprint.add_url_rule(rule, endpoint, lambda **kwargs: "")
    return blueprint


@pytest.fixture(scope="module")
def extra_entry_points():
    """Register the stand-in RDM UI blueprint and RDM's search mappings.

    The mappings entry point is declared by upstream invenio-rdm-records but missing from the
    CESNET builds installed by the ``oarepo14`` extra; without it RDM records are not searchable.
    """
    return {
        "invenio_base.blueprints": ["invenio_app_rdm_records = tests.conftest:create_rdm_ui_blueprint"],
        "invenio_search.mappings": ["rdmrecords = invenio_rdm_records.records.mappings"],
    }


@pytest.fixture(scope="module")
def create_app(instance_path, entry_points):
    """invenio-app's factory: UI app with the API mounted, all installed entry points (incl. oarepo-ark)."""
    return _create_app


@pytest.fixture(scope="module")
def resource_type_v(app, database, search):
    """Create the one resource type the test records use."""
    vocabulary_service.create_type(system_identity, "resourcetypes", "rsrct")
    item = vocabulary_service.create(
        system_identity,
        {
            "id": "dataset",
            "props": {"type": "dataset", "subtype": ""},
            "tags": ["depositable"],
            "title": {"en": "Dataset"},
            "type": "resourcetypes",
        },
    )
    current_search_client.indices.refresh()  # serializers (e.g. on record deletion) look it up in search
    return item


@pytest.fixture(scope="module")
def search(appctx):
    """All search indices; recovers from stale indices left by an aborted run (as invenio-rdm-records' tests)."""
    try:
        list(current_search.create())
    except Exception:  # noqa: BLE001 - e.g. an index left without its alias
        current_search_client.indices.delete(index="*", ignore=[400, 404])
        list(current_search.create())
    current_search_client.indices.refresh()
    yield current_search_client
    list(current_search.delete(ignore=[404]))


@pytest.fixture
def search_clear(search):
    """Remove the records and drafts from search after the test; keep the vocabularies (RDM looks them up)."""
    yield search
    indices = [prefix_index(cls.index._name) for cls in (RDMRecord, RDMDraft)]
    current_search_client.delete_by_query(
        index=indices, body={"query": {"match_all": {}}}, conflicts="proceed", refresh=True
    )


@pytest.fixture
def cache(app):
    """Empty Invenio cache: RDM caches vocabulary lookups (``read_all``) there, across test runs too."""
    current_cache.clear()
    yield current_cache
    current_cache.clear()


@pytest.fixture
def running_app(app, db, location, cache, search_clear, resource_type_v):
    """App with database, file location, empty cache, clean search indices and vocabularies."""
    return app


@pytest.fixture(autouse=True)
def restore_ark_state(request):
    """Undo per-test changes of ARK/PID config, registered services and the extension's caches."""
    if "app" not in request.fixturenames:
        yield
        return
    app = request.getfixturevalue("app")
    config = {k: copy(v) for k, v in app.config.items() if k.startswith(("ARK_", "RDM_PERSISTENT_IDENTIFIER"))}
    services = dict(current_service_registry._services)
    yield
    app.config.update(config)
    current_service_registry._services.clear()
    current_service_registry._services.update(services)
    ext = app.extensions["oarepo-ark"]
    ext._handlers.clear()
    ext._service_ids.clear()
    ext._ark_services = (-1, {})


@pytest.fixture
def publish(running_app) -> Callable[..., RecordItem]:
    """Publish a record through RDM's records service (the ARK is minted on publish)."""

    def _publish(access: str = "public", **metadata: Any) -> RecordItem:
        data = deepcopy(MINIMAL_RECORD)
        data["access"]["record"] = access
        data["metadata"].update(metadata)
        draft = current_rdm_records_service.create(system_identity, data)
        return current_rdm_records_service.publish(system_identity, draft.id)

    return _publish


def use_handler(app: Any, handler: Any) -> None:
    """Configure the handler of ``records`` (handlers are cached per service)."""
    app.config["ARK_HANDLERS"] = {"records": handler}
    app.extensions["oarepo-ark"]._handlers.clear()


def expected_ark(record_id: str, shoulder: str = "r1") -> str:
    """Return the ARK oarepo-ark computes for a record id."""
    base = f"{NAAN}/{shoulder}{record_id.replace('-', '')}"
    return f"ark:{base}{noid_check_char(base)}"


def ark_of(item: RecordItem) -> str:
    """ARK in a service result item."""
    return item.data["pids"]["ark"]["identifier"]


def strip_ark(record_id: str) -> RDMRecord:
    """Make a published record look like one published before oarepo-ark was installed."""
    record = RDMRecord.pid.resolve(record_id)
    pids = dict(record["pids"])
    ark = pids.pop("ark")["identifier"]
    record["pids"] = pids
    record.commit()
    db.session.delete(PersistentIdentifier.get("ark", ark))
    db.session.commit()
    return record
