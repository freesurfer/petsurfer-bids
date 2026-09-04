"""Tests for the SUVR frame-window helpers (issue #11).

Exercises the pure-Python parts of the SUVR path without requiring FreeSurfer:
frame-duration weighting, the reference scalar, and the ROI table. Synthetic
TAC tables are written to ``tmp_path``.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from petsurfer_km.inputs import InputGroup
from petsurfer_km.steps.participant.step04_kinetic import (
    _extract_ref_value_over_frames,
    _read_frame_times,
    _run_suvr_roi,
    _weighted_mean,
    _write_suvr_frame_weights,
)

# Four frames of unequal duration: 30, 30, 60, 60 s
FRAME_STARTS = [0, 30, 60, 120]
FRAME_ENDS = [30, 60, 120, 180]


def _write_tacs(tmp_path: Path) -> Path:
    """Write a minimal petprep-style tacs.tsv with two ROI columns."""
    tacs = tmp_path / "sub-01_desc-preproc_seg-gtm_tacs.tsv"
    rows = ["frame_start\tframe_end\tLeft-Cerebellum-Cortex\tLeft-Putamen"]
    for start, end, cblum, put in zip(FRAME_STARTS, FRAME_ENDS, [1, 2, 3, 4], [2, 4, 6, 8]):
        rows.append(f"{start}\t{end}\t{cblum}\t{put}")
    tacs.write_text("\n".join(rows) + "\n")
    return tacs


def _write_roi_tacs(tmp_path: Path) -> Path:
    """Write a roi-tacs-petsurfer.dat as `tsv2petsurfer --all` emits it.

    First column is frame_start, and every row carries a trailing tab.
    """
    dat = tmp_path / "roi-tacs-petsurfer.dat"
    rows = ["frame_start\tLeft-Cerebellum-Cortex\tLeft-Putamen\t"]
    for start, cblum, put in zip(FRAME_STARTS, [1, 2, 3, 4], [2, 4, 6, 8]):
        rows.append(f"{start}\t{cblum}\t{put}\t")
    dat.write_text("\n".join(rows) + "\n")
    return dat


def _make_args(frames: list[int]) -> Namespace:
    return Namespace(suvr_frame=frames)


# --- frame times ---------------------------------------------------------------

def test_read_frame_times(tmp_path: Path) -> None:
    starts, ends = _read_frame_times(_write_tacs(tmp_path))
    assert starts == [float(s) for s in FRAME_STARTS]
    assert ends == [float(e) for e in FRAME_ENDS]


def test_read_frame_times_missing_columns(tmp_path: Path) -> None:
    bad = tmp_path / "bad.tsv"
    bad.write_text("a\tb\n1\t2\n")
    with pytest.raises(RuntimeError, match="frame_start"):
        _read_frame_times(bad)


# --- frame weights -------------------------------------------------------------

def test_weights_are_duration_proportional(tmp_path: Path) -> None:
    """Frames 1-3 have durations 30, 60, 60 -> weights 0.2, 0.4, 0.4."""
    inputs = InputGroup(subject="01", session=None, tacs=_write_tacs(tmp_path))
    temps: dict = {}
    history: list = []

    weights = _write_suvr_frame_weights(inputs, temps, tmp_path, history, _make_args([1, 2, 3]))

    assert weights == pytest.approx([0.0, 0.2, 0.4, 0.4])
    assert sum(weights) == pytest.approx(1.0)
    assert temps["suvr_time_window"] == (30.0, 180.0)

    # mri_concat's MatrixReadTxt wants exactly one number per line, no header,
    # no blank lines, and one row per frame of the 4D PET
    lines = temps["suvr_weights"].read_text().splitlines()
    assert len(lines) == len(FRAME_STARTS)
    assert all(line.strip() for line in lines)
    assert [float(line) for line in lines] == pytest.approx(weights)


def test_single_frame_weight_is_one(tmp_path: Path) -> None:
    """A single --suvr-frame reproduces plain single-frame selection."""
    inputs = InputGroup(subject="01", session=None, tacs=_write_tacs(tmp_path))
    temps: dict = {}
    weights = _write_suvr_frame_weights(inputs, temps, tmp_path, [], _make_args([2]))
    assert weights == pytest.approx([0.0, 0.0, 1.0, 0.0])
    assert temps["suvr_time_window"] == (60.0, 120.0)


def test_frames_given_out_of_order_are_sorted(tmp_path: Path) -> None:
    inputs = InputGroup(subject="01", session=None, tacs=_write_tacs(tmp_path))
    temps: dict = {}
    weights = _write_suvr_frame_weights(inputs, temps, tmp_path, [], _make_args([3, 1, 2]))
    assert weights == pytest.approx([0.0, 0.2, 0.4, 0.4])
    assert temps["suvr_time_window"] == (30.0, 180.0)


def test_out_of_range_frame_raises(tmp_path: Path) -> None:
    inputs = InputGroup(subject="01", session=None, tacs=_write_tacs(tmp_path))
    with pytest.raises(RuntimeError, match="out of range"):
        _write_suvr_frame_weights(inputs, {}, tmp_path, [], _make_args([2, 4]))


# --- reference value -----------------------------------------------------------

def test_reference_value_is_weighted_mean(tmp_path: Path) -> None:
    ref_tac = tmp_path / "ref-tac-petsurfer.dat"
    ref_tac.write_text("1\n2\n3\n4\n")
    temps: dict = {"ref_tac": ref_tac}
    weights = [0.0, 0.2, 0.4, 0.4]

    value = _extract_ref_value_over_frames(temps, weights)

    assert value == pytest.approx(0.2 * 2 + 0.4 * 3 + 0.4 * 4)
    assert temps["suvr_ref_value"] == pytest.approx(value)


def test_reference_frame_count_mismatch_raises(tmp_path: Path) -> None:
    ref_tac = tmp_path / "ref-tac-petsurfer.dat"
    ref_tac.write_text("1\n2\n")
    with pytest.raises(RuntimeError, match="frames"):
        _extract_ref_value_over_frames({"ref_tac": ref_tac}, [0.0, 0.2, 0.4, 0.4])


# --- ROI table -----------------------------------------------------------------

def test_roi_suvr_table(tmp_path: Path) -> None:
    """Each ROI TAC is weight-averaged then divided by the reference value."""
    temps: dict = {"roi_tacs": _write_roi_tacs(tmp_path)}
    weights = [0.0, 0.2, 0.4, 0.4]
    ref_value = 0.2 * 2 + 0.4 * 3 + 0.4 * 4  # the cerebellum column
    history: list = []

    _run_suvr_roi(temps, tmp_path, history, weights, ref_value)

    rows = [line.split() for line in (tmp_path / "suvr.roi" / "suvr.dat").read_text().splitlines()]
    assert [r[0] for r in rows] == ["Left-Cerebellum-Cortex", "Left-Putamen"]
    # The reference region divided by itself is 1; putamen is exactly 2x
    assert float(rows[0][1]) == pytest.approx(1.0)
    assert float(rows[1][1]) == pytest.approx(2.0)
    assert temps["suvr_roi_dir"] == tmp_path / "suvr.roi"
    assert history  # the step is recorded for the report


def test_roi_frame_count_mismatch_raises(tmp_path: Path) -> None:
    temps: dict = {"roi_tacs": _write_roi_tacs(tmp_path)}
    with pytest.raises(RuntimeError, match="frames"):
        _run_suvr_roi(temps, tmp_path, [], [0.5, 0.5], 1.0)


# --- weighted mean -------------------------------------------------------------

def test_weighted_mean() -> None:
    assert _weighted_mean([1.0, 2.0, 3.0], [0.5, 0.25, 0.25]) == pytest.approx(1.75)
