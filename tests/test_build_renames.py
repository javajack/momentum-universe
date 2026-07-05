"""Unit tests for tools/build_renames.py — ISIN-continuity classification.

Uses tiny synthetic UDiFF bhavcopy zips (no live data / no network) to verify
the rename/drop/false-positive logic and the additive apply behaviour.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest

from tools import build_renames as br

_HEADER = ["TckrSymb", "SctySrs", "ISIN", "FinInstrmNm"]


def _write_bhav(raw_dir: Path, d: str, rows: list[tuple[str, str, str, str]]) -> None:
    """Write one synthetic bhavcopy zip at raw_dir/<yyyy>/<MON>/BhavCopy_..._<d>_F_0000.csv.zip."""
    year, mon = d[:4], "JAN"
    sub = raw_dir / year / mon
    sub.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_HEADER)
    for r in rows:
        w.writerow(r)
    zpath = sub / f"BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr(f"BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv", buf.getvalue())


@pytest.fixture
def index(tmp_path):
    """3-day history: BAR moves EQ->BE on D2; BAZ renamed to NEWZ (same ISIN) on D3; FOO stays EQ."""
    raw = tmp_path / "raw"
    _write_bhav(raw, "20260105", [
        ("FOO", "EQ", "INEFOO01010", "Foo Ltd"),
        ("BAR", "EQ", "INEBAR01011", "Bar Ltd"),
        ("BAZ", "EQ", "INEBAZ01012", "Baz Ltd"),
    ])
    _write_bhav(raw, "20260106", [
        ("FOO", "EQ", "INEFOO01010", "Foo Ltd"),
        ("BAR", "BE", "INEBAR01011", "Bar Ltd"),       # surveillance move
        ("BAZ", "EQ", "INEBAZ01012", "Baz Ltd"),
    ])
    _write_bhav(raw, "20260107", [
        ("FOO", "EQ", "INEFOO01010", "Foo Ltd"),
        ("BAR", "BE", "INEBAR01011", "Bar Ltd"),
        ("NEWZ", "EQ", "INEBAZ01012", "Baz Renamed Ltd"),  # rename: same ISIN, new ticker
    ])
    return br._build_index(raw, date(2026, 1, 7), window_days=30)


def test_index_parses_all_days(index):
    assert [s["date"] for s in index] == ["20260105", "20260106", "20260107"]


def test_false_positive_returns_none(index):
    # FOO is still EQ in the latest snapshot -> no entry proposed.
    assert br._classify("FOO", index) is None


def test_eq_to_be_is_drop(index):
    p = br._classify("BAR", index)
    assert p["to"] is None
    assert p["isin"] == "INEBAR01011"
    assert p["effective"] == "2026-01-06"   # first day it stopped being EQ
    assert "Trade-to-Trade" in p["note"]


def test_rename_follows_isin(index):
    p = br._classify("BAZ", index)
    assert p["to"] == "NEWZ"
    assert p["isin"] == "INEBAZ01012"
    assert p["effective"] == "2026-01-07"


def test_delisting_when_isin_disappears(tmp_path):
    raw = tmp_path / "raw"
    _write_bhav(raw, "20260105", [("GONE", "EQ", "INEGONE0101", "Gone Ltd")])
    _write_bhav(raw, "20260106", [("FOO", "EQ", "INEFOO01010", "Foo Ltd")])  # GONE/ISIN absent
    idx = br._build_index(raw, date(2026, 1, 6), window_days=30)
    p = br._classify("GONE", idx)
    assert p["to"] is None
    assert "delisted" in p["note"]


def test_apply_is_additive(tmp_path):
    path = tmp_path / "stock-renames.json"
    path.write_text(json.dumps({"renames": {"OLD": {"to": "NEW", "isin": "X", "effective": "2026-01-01", "note": "pre-existing"}}}))
    proposals = [
        {"old": "OLD", "to": "HACKED", "isin": "Y", "effective": "2026-01-09", "note": "should be ignored"},
        {"old": "FRESH", "to": None, "isin": "INEFRSH0101", "effective": "2026-01-08", "note": "new drop"},
    ]
    added = br.apply_entries(proposals, path=path)
    doc = json.loads(path.read_text())["renames"]
    assert added == 1                          # only FRESH added
    assert doc["OLD"]["to"] == "NEW"           # existing key NOT overwritten
    assert doc["FRESH"]["to"] is None
    assert (tmp_path / "stock-renames.json.bak").exists()
