"""Tests for `_convert_dat_to_tsv` header contract (issue #2).

Exercises the pure conversion function without requiring FreeSurfer or a BIDS
layout: synthetic `.dat` sources are written to `tmp_path` and the produced
`_kinpar.tsv` is asserted to start with the per-method header and to drop the
FreeSurfer source header / comments.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from petsurfer_km.steps.participant.step05_bidsify import ROI_TSV_HEADERS, _convert_dat_to_tsv


# --- per-method header --------------------------------------------------------

@pytest.mark.parametrize("method,expected_header", list(ROI_TSV_HEADERS.items()))
def test_first_line_is_method_header(tmp_path: Path, method: str, expected_header: tuple[str, ...]) -> None:
    """Output TSV starts with the tab-joined per-method header."""
    src = tmp_path / "roi.dat"
    src.write_text("# FreeSurfer comment\nLeft-Cerebellum-Cortex 0.1 0.2 0.3\n")
    dest = tmp_path / "out.tsv"
    _convert_dat_to_tsv(src, dest, method)
    lines = dest.read_text().splitlines()
    assert lines[0] == "\t".join(expected_header)


def test_mrtm_source_header_is_stripped(tmp_path: Path) -> None:
    """The `frame_start` line from gamma.table.dat must not appear in the output."""
    src = tmp_path / "gamma.table.dat"
    src.write_text(
        "# comment line\n"
        "frame_start k2 k2a k2-k2a\n"
        "Left-Cerebellum-Cortex 0.10 0.20 0.30\n"
        "Right-Cerebellum-Cortex 0.11 0.21 0.31\n"
    )
    dest = tmp_path / "mrtm2.tsv"
    _convert_dat_to_tsv(src, dest, "mrtm2")
    lines = dest.read_text().splitlines()
    assert lines[0] == "ROI\tk2\tk2a\tk2-k2a"
    assert "frame_start" not in {line.split("\t")[0] for line in lines}
    assert "# comment line" not in lines
    assert lines[1] == "Left-Cerebellum-Cortex\t0.10\t0.20\t0.30"
    assert lines[2] == "Right-Cerebellum-Cortex\t0.11\t0.21\t0.31"


def test_frame_capitalized_header_is_stripped(tmp_path: Path) -> None:
    """The `Frame` variant (used by some FreeSurfer .dat writers) is also dropped."""
    src = tmp_path / "gamma.table.dat"
    src.write_text("Frame k2 k2a k2-k2a\nLeft-Cerebellum-Cortex 0.1 0.2 0.3\n")
    dest = tmp_path / "mrtm1.tsv"
    _convert_dat_to_tsv(src, dest, "mrtm1")
    lines = dest.read_text().splitlines()
    assert lines[0] == "ROI\tk2\tk2a\tk2-k2a"
    assert all(not line.startswith("Frame") for line in lines)


def test_logan_two_column_data_preserved(tmp_path: Path) -> None:
    """Logan/MA1 .dat has no source header; output gets `ROI\tVT` + data rows."""
    src = tmp_path / "vt.dat"
    src.write_text(
        "# comment\n"
        "Left-Cerebellum-Cortex 1.23\n"
        "Right-Cerebellum-Cortex 1.24\n"
    )
    dest = tmp_path / "logan.tsv"
    _convert_dat_to_tsv(src, dest, "logan")
    lines = dest.read_text().splitlines()
    assert lines[0] == "ROI\tVT"
    assert lines[1] == "Left-Cerebellum-Cortex\t1.23"
    assert lines[2] == "Right-Cerebellum-Cortex\t1.24"


def test_ma1_header_matches_logan(tmp_path: Path) -> None:
    """MA1 shares the Logan VT header."""
    src = tmp_path / "vt.dat"
    src.write_text("Left-Cerebellum-Cortex 1.23\n")
    dest = tmp_path / "ma1.tsv"
    _convert_dat_to_tsv(src, dest, "logan-ma1")
    assert dest.read_text().splitlines()[0] == "ROI\tVT"


def test_patlak_header(tmp_path: Path) -> None:
    """Patlak output gets `ROI\tKi`."""
    src = tmp_path / "Ki.dat"
    src.write_text("Left-Cerebellum-Cortex 0.005\n")
    dest = tmp_path / "patlak.tsv"
    _convert_dat_to_tsv(src, dest, "patlak")
    lines = dest.read_text().splitlines()
    assert lines[0] == "ROI\tKi"
    assert lines[1] == "Left-Cerebellum-Cortex\t0.005"


def test_empty_source_yields_header_only(tmp_path: Path) -> None:
    """A source with only comments/blanks still produces the header line."""
    src = tmp_path / "vt.dat"
    src.write_text("# only a comment\n\n")
    dest = tmp_path / "logan.tsv"
    _convert_dat_to_tsv(src, dest, "logan")
    assert dest.read_text() == "ROI\tVT\n"


# --- failure path -------------------------------------------------------------

def test_missing_source_logs_warning_and_writes_no_file(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A missing source logs a warning and does not create the destination."""
    src = tmp_path / "missing.dat"
    dest = tmp_path / "out.tsv"
    with caplog.at_level(logging.WARNING, logger="petsurfer_km"):
        _convert_dat_to_tsv(src, dest, "logan")
    assert not dest.exists()
    assert any("not found" in rec.message for rec in caplog.records)


# --- guardrail ----------------------------------------------------------------

def test_unknown_method_raises_keyerror(tmp_path: Path) -> None:
    """A method without a header entry surfaces a KeyError (no silent headerless file)."""
    src = tmp_path / "roi.dat"
    src.write_text("Left-Cerebellum-Cortex 0.1\n")
    dest = tmp_path / "out.tsv"
    with pytest.raises(KeyError):
        _convert_dat_to_tsv(src, dest, "suvr")
