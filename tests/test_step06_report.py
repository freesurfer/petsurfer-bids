"""Tests for the participant report's freebrowse surface viewer link.

``generate_freebrowse_surface_viewer`` is replaced by a recorder, so these
check only which BIDS maps are handed to it and when it is called.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import petsurfer_km.steps.participant.step06_report as step06
from petsurfer_km.steps.participant.step06_report import _build_surface_viewer_link

PREFIX = "sub-01_ses-bl_trc-X"
PET_DIR = Path("sub-01") / "ses-bl" / "pet"
FB_NAME = f"{PREFIX}_model-MA1_meas-VT_space-fsaverage_mimap.html"


def _bids_map(output_dir: Path, hemi: str, ext: str) -> Path:
    return output_dir / PET_DIR / (
        f"{PREFIX}_hemi-{hemi}_space-fsaverage_desc-sm10_model-MA1_meas-VT_mimap{ext}"
    )


@pytest.fixture
def setup(tmp_path: Path, monkeypatch):
    """Output dir with GIFTI and NIfTI maps for both hemis; recorded generator calls."""
    for hemi in ("L", "R"):
        for ext in (".func.gii", ".nii.gz"):
            path = _bids_map(tmp_path, hemi, ext)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
    calls: list[tuple[dict, Path]] = []
    monkeypatch.setattr(
        step06, "generate_freebrowse_surface_viewer",
        lambda hemi_maps, output_html: calls.append((hemi_maps, output_html)),
    )
    temps = {"ma1_surf_lh_dir": tmp_path / "w", "ma1_surf_rh_dir": tmp_path / "w"}
    return tmp_path, temps, calls


def _link(output_dir: Path, temps: dict, **args) -> str:
    args = SimpleNamespace(**{"no_freebrowse": False, "nifti_surfaces": False, "surf_fwhm": 10.0, **args})
    return _build_surface_viewer_link(
        "ma1", PREFIX, output_dir, "01", "bl", output_dir / "figs", "sub-01/ses-bl/figures", temps, args
    )


def test_gifti_maps_for_both_hemis(setup) -> None:
    output_dir, temps, calls = setup

    link = _link(output_dir, temps)

    assert calls == [(
        {"lh": _bids_map(output_dir, "L", ".func.gii"), "rh": _bids_map(output_dir, "R", ".func.gii")},
        output_dir / "figs" / FB_NAME,
    )]
    assert link == f'<a href="./sub-01/ses-bl/figures/{FB_NAME}" target="_blank">View surfaces in freebrowse</a>'


def test_nifti_surfaces_use_nifti_maps(setup) -> None:
    output_dir, temps, calls = setup

    _link(output_dir, temps, nifti_surfaces=True)

    assert calls[0][0] == {
        "lh": _bids_map(output_dir, "L", ".nii.gz"),
        "rh": _bids_map(output_dir, "R", ".nii.gz"),
    }


def test_only_hemis_that_were_run(setup) -> None:
    output_dir, temps, calls = setup
    del temps["ma1_surf_rh_dir"]

    _link(output_dir, temps)

    assert list(calls[0][0]) == ["lh"]


def test_no_freebrowse_or_no_maps(setup) -> None:
    output_dir, temps, calls = setup

    assert _link(output_dir, temps, no_freebrowse=True) == ""
    assert _link(output_dir, {}) == ""
    assert _link(output_dir, temps, surf_fwhm=5.0) == ""  # no BIDS maps with that name
    assert calls == []


def test_generation_failure_is_a_warning(setup, monkeypatch, caplog) -> None:
    output_dir, temps, _ = setup

    def boom(hemi_maps, output_html):
        raise RuntimeError("FREESURFER_HOME is not set")

    monkeypatch.setattr(step06, "generate_freebrowse_surface_viewer", boom)
    with caplog.at_level(logging.WARNING, logger="petsurfer_km"):
        assert _link(output_dir, temps) == ""
    assert "Could not generate freebrowse surface viewer for MA1 VT" in caplog.text
