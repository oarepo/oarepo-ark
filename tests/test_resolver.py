# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

"""The ``/ark:NAAN/...`` resolver on records published through RDM's records service."""

from __future__ import annotations

import re

import pytest
from invenio_access.permissions import system_identity
from invenio_db import db
from invenio_pidstore.errors import PIDDoesNotExistError
from invenio_pidstore.models import PersistentIdentifier
from invenio_rdm_records.proxies import current_rdm_records_service as service
from invenio_rdm_records.records.api import RDMRecord
from PIL import ImageFont

from oarepo_ark.api import ARKHandler
from tests.conftest import ThesisHandler, ark_of, use_handler

ERC_RECORD = {"who": "Brown, Troy; Troy Inc.", "what": "A Romans story", "when": "2020-06-01"}


@pytest.fixture
def record(app, publish):
    """Publish a record with an ARK, handled by ThesisHandler (RDM handler + extra ERC element)."""
    app.config["ARK_HANDLERS"] = {"records": ThesisHandler}
    item = publish()
    item.ark = ark_of(item)
    item.landing = f"https://repo.example.org/records/{item.id}"
    item.cite_as = f'<https://n2t.net/{item.ark}>; rel="cite-as"'
    return item


@pytest.mark.parametrize(
    "variant",
    [
        lambda ark: f"/{ark}",
        lambda ark: f"/{ark.replace('ark:', 'ark:/')}",  # `ark:/` label, needs its own URL rule
        lambda ark: f"/{ark.replace('ark:', 'ARK:')}",  # upper-case label, too
        lambda ark: f"/{ark.replace('ark:', 'ARK:/')}",
        lambda ark: f"/{ark[:-6]}-{ark[-6:]}",  # hyphens are insignificant
        lambda ark: f"/{ark}/",
        lambda ark: f"/{ark}?utm=x",  # other query strings are ignored
    ],
)
def test_landing_page(client, record, variant):
    response = client.get(variant(record.ark))
    assert response.status_code == 302
    assert response.location == record.landing
    assert response.headers["Link"] == record.cite_as


def test_api_by_accept(client, record):
    response = client.get(f"/{record.ark}", headers={"Accept": "application/json"})
    assert response.location == f"https://repo.example.org/api/records/{record.id}"


def test_component_keeps_raw_qualifier(client, record):
    response = client.get(f"/{record.ark}/files/my-data.csv")
    assert response.status_code == 302
    assert response.location == f"{record.landing}/c/files/my-data.csv"


def test_variant(client, app, record):
    response = client.get(f"/{record.ark}.datacite-json")
    assert response.status_code == 302
    assert response.location == f"{record.landing}/export/datacite-json"
    use_handler(app, ARKHandler)  # the base handler knows no variants
    response = client.get(f"/{record.ark}.json")
    assert response.status_code == 404
    assert response.headers["Link"] == record.cite_as


@pytest.mark.parametrize(("query", "raw_uri"), [("?info", None), ("??", None), ("", "?")])
def test_info_anvl(client, record, query, raw_uri):
    environ = {"RAW_URI": f"/{record.ark}{raw_uri}"} if raw_uri else {}  # Werkzeug drops a bare "?"
    response = client.get(f"/{record.ark}{query}", environ_overrides=environ)
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert response.headers["Link"] == f'{record.cite_as}, <https://n2t.net/{record.ark}>; rel="describes"'
    assert response.text == (
        "erc:\n"
        f"who:   {ERC_RECORD['who']}\n"
        f"what:  {ERC_RECORD['what']}\n"
        f"when:  {ERC_RECORD['when']}\n"
        f"where: https://n2t.net/{record.ark}\n"
        "how:   thesis\n"
        "\n"
        "erc-support:\n"
        "who:   Example Repository\n"  # THEME_SITENAME
        "what:  Permanent: Stable Content:\n"
        "when:  (:unav)\n"
        "where: https://repo.example.org/\n"  # SITE_UI_URL, not the request's host
    )


def test_info_json(client, app, record):
    app.config["ARK_SUPPORT"] = {"who": "Example Library", "what": "Permanent:", "when": "20260101", "where": None}
    response = client.get(f"/{record.ark}?info", headers={"Accept": "application/json"})
    assert response.json == {
        "erc": {**ERC_RECORD, "where": f"https://n2t.net/{record.ark}", "how": "thesis"},
        "erc-support": {
            "who": "Example Library",
            "what": "Permanent:",
            "when": "20260101",
            "where": "https://repo.example.org/",
        },
    }


def test_restricted(client, record):
    """A record restricted after it got its ARK: redirects work, ``?info`` reveals nothing."""
    draft = service.edit(system_identity, record.id)
    restricted = {"record": "restricted", "files": "restricted"}
    service.update_draft(system_identity, record.id, {**draft.data, "access": restricted})
    service.publish(system_identity, record.id)
    # anonymous request: RDM's read raises PermissionDeniedError; the landing page enforces access
    assert client.get(f"/{record.ark}").location == record.landing
    info = client.get(f"/{record.ark}?info", headers={"Accept": "application/json"}).json
    assert info["erc"] == {
        "who": "(:unav)",
        "what": "(:unav) restricted",
        "when": "(:unav)",
        "where": f"https://n2t.net/{record.ark}",
    }


def test_deleted_record_goes_to_tombstone(client, record):
    """RDM's landing page renders the tombstone (410, citation, removal reason)."""
    service.delete_record(system_identity, record.id, {"note": "spam"})
    response = client.get(f"/{record.ark}")
    assert response.status_code == 302
    assert response.location == record.landing
    assert response.headers["Link"] == record.cite_as
    # no metadata, exports or files of a withdrawn object
    info = client.get(f"/{record.ark}?info", headers={"Accept": "application/json"})
    assert info.status_code == 410
    assert info.json["erc"] == {
        "who": "(:unav)",
        "what": "(:unav) withdrawn",
        "when": "(:unav)",
        "where": f"https://n2t.net/{record.ark}",
    }
    for qualifier in (".json", "/files/a.csv"):
        response = client.get(f"/{record.ark}{qualifier}")
        assert response.status_code == 410
        assert response.headers["Link"] == record.cite_as


def test_deleted_ark_pid(client, record):
    """A deleted ARK PID has no record to show: plain 410."""
    PersistentIdentifier.get("ark", record.ark).delete()
    db.session.commit()
    response = client.get(f"/{record.ark}")
    assert response.status_code == 410
    assert response.text == "This object has been withdrawn.\n"


@pytest.mark.parametrize(("query", "suffix"), [("", ""), ("?info", "?info"), ("??", "?info"), ("?utm=x", "")])
def test_foreign_naan_goes_to_n2t(client, running_app, query, suffix):
    """The ``?info`` inflection (in any form) is kept, other query strings are dropped."""
    response = client.get(f"/ark:/13030/xf93gt2q/x-y{query}")
    assert response.status_code == 302
    assert response.location == f"https://n2t.net/ark:13030/xf93gt2q/x-y{suffix}"


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("r1abcde12345x", "Not found: the check character does not match"),  # "p" would be right
        ("r1zzzzz", "Not found: the check character does not match"),
        ("b9abc", "Not found.\n"),  # not our shoulder, cannot tell
    ],
)
def test_not_found(client, running_app, name, message):
    response = client.get(f"/ark:12345/{name}")
    assert response.status_code == 404
    assert response.text.startswith(message)


def test_not_an_ark(client, running_app):
    assert client.get("/ark:12345").text == "Not an ARK.\n"


def test_inert_without_naan(client, app, record):
    app.config["ARK_NAAN"] = None
    assert client.get(f"/{record.ark}").status_code == 404


@pytest.mark.parametrize("query", ["", "?info"])
def test_missing_object_is_not_withdrawn(client, record, query):
    """A record row removed outside RDM's deletion (no tombstone) is a 404, not a 410."""
    record._record.model.is_deleted = True  # RDMRecord.get_record now raises NoResultFound
    db.session.commit()
    response = client.get(f"/{record.ark}{query}")
    assert response.status_code == 404
    assert response.text == "Not found: the object of this ARK does not exist.\n"


def test_missing_record_pid_is_not_withdrawn(client, app, record):
    class NoRecordPID(ThesisHandler):
        def read(self, identity, object_uuid):
            raise PIDDoesNotExistError("recid", record.id)

    use_handler(app, NoRecordPID)
    assert client.get(f"/{record.ark}").status_code == 404


def test_unexpected_errors_propagate(client, app, record):
    """Bugs surface as errors instead of being reported as withdrawn objects."""

    class Broken(ThesisHandler):
        def read(self, identity, object_uuid):
            raise RuntimeError("bug")

    use_handler(app, Broken)
    with pytest.raises(RuntimeError, match="bug"):  # TESTING: Flask re-raises instead of answering 500
        client.get(f"/{record.ark}")


def test_resolver_loads_the_record_only_through_the_service(client, record, monkeypatch):
    """The handler finds the record id in PIDStore; the record is loaded only by ``service.read``."""
    loaded = []
    get_record = RDMRecord.get_record.__func__

    def counting(cls, *args, **kwargs):
        loaded.append(args[0])
        return get_record(cls, *args, **kwargs)

    monkeypatch.setattr(RDMRecord, "get_record", classmethod(counting))
    service.read(system_identity, record.id)
    by_service = len(loaded)  # RDMRecordService.read itself resolves the record twice
    loaded.clear()
    assert client.get(f"/{record.ark}").status_code == 302
    assert len(loaded) == by_service


def test_badge(client, running_app):
    """``ARK | value`` badge in the style of RDM's DOI badge (invenio-formatter's generator)."""
    try:
        ImageFont.truetype("DejaVuSans", 11)
    except OSError:
        pytest.skip("DejaVuSans font not installed (invenio-formatter measures badge text with it)")
    response = client.get("/ark-badge/ark:12345/r1abcde12345p.svg")
    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"
    # each text is drawn twice (shadow + text) and indented inside its <text> element
    texts = [t.strip() for t in re.findall(r"<text[^>]*>([^<]*)</text>", response.text)]
    assert texts == ["ARK", "ARK", "ark:12345/r1abcde12345p", "ark:12345/r1abcde12345p"]
