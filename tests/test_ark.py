# SPDX-FileCopyrightText: 2026 CESNET z.s.p.o
# SPDX-License-Identifier: MIT

from __future__ import annotations

import doctest

import pytest

from oarepo_ark import ark
from oarepo_ark.ark import InvalidARKError, is_ark, noid_check_char, normalize_ark, split_ark


def test_doctests():
    assert doctest.testmod(ark).failed == 0


@pytest.mark.parametrize(
    ("value", "check"),
    [
        ("13030/xf93gt2", "q"),  # NOID documentation
        ("12345/r1abcde12345", "p"),
    ],
)
def test_noid_check_char(value, check):
    assert noid_check_char(value) == check


def test_check_char_detects_transposition():
    assert noid_check_char("13030/xf93gt2") != noid_check_char("13030/xf39gt2")


@pytest.mark.parametrize(
    ("value", "normalized"),
    [
        ("ark:12345/r1x", "ark:12345/r1x"),
        ("ark:/12345/r1x", "ark:12345/r1x"),
        ("https://n2t.net/ark:/12345/r1x?info", "ark:12345/r1x"),
        ("ARK:12345/r1x", "ark:12345/r1x"),  # the label in any letter case
        ("https://n2t.net/Ark:/12345/r1x", "ark:12345/r1x"),
        ("ark:B1234/r1x", "ark:b1234/r1x"),  # NAAN lowercased, the name is case sensitive
        ("ark:12345/R1X", "ark:12345/R1X"),
        ("ark:12345/r1-ab-c", "ark:12345/r1abc"),
        ("ark:12345/r1x%2fa%c3", "ark:12345/r1x%2Fa%C3"),
        ("ark:12345/r1x//a..b", "ark:12345/r1x/a.b"),
        ("ark:12345/r1x/", "ark:12345/r1x"),
        ("ark:12345/r1x.", "ark:12345/r1x"),
    ],
)
def test_normalize(value, normalized):
    assert normalize_ark(value) == normalized


@pytest.mark.parametrize("value", ["", "ark:", "ark:12345", "ark:12345/", "ark:12345/-", "doi:10.1/x", "ark:1 2/x"])
def test_invalid(value):
    assert not is_ark(value)
    with pytest.raises(InvalidARKError):
        normalize_ark(value)


def test_split_keeps_qualifier_raw():
    assert split_ark("ark:12345/r1x/files/my-file.csv#top") == ("12345", "r1x/files/my-file.csv")
