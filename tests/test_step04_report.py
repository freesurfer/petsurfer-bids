"""Tests for the group report's freebrowse surface viewer (one per contrast).

Figure rendering and ``generate_freebrowse_surface_viewer`` are replaced by
stubs, so these check only which BIDS maps are handed to the viewer and where
its link lands.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import petsurfer_km.steps.group.step04_report as step04
from petsurfer_km.methods import MEAS_LABELS, MODEL_LABELS
from petsurfer_km.steps.group.step01_setup import GroupContext
from petsurfer_km.steps.group.step04_report import (
    _build_figure_sections,
    _build_surface_viewer_link,
)

CONTRASTS = ["osgm", "group-diff"]


def _context(spaces: list[str]) -> GroupContext:
    return GroupContext(
        layout=None, spaces=spaces, sessions=["baseline"], paired=False, tracer="X",
        km_method="ma1", model=MODEL_LABELS["ma1"], meas=MEAS_LABELS["ma1"],
        fsgd=None, fsgd_file=None,
    )


def _args(output_dir: Path, **overrides) -> SimpleNamespace:
    return SimpleNamespace(**{
        "output_dir": output_dir, "surf_fwhm": 5.0, "vol_fwhm": 6.0,
        "no_freebrowse": False, "nifti_surfaces": False, **overrides,
    })


def _bids_map(output_dir: Path, hemi: str, desc: str, ext: str) -> Path:
    return output_dir / "tpl-fsaverage" / "pet" / (
        f"tpl-fsaverage_hemi-{hemi}_atlas-PetsurferKM_desc-{desc}_model-MA1_meas-VT_mimap{ext}"
    )


@pytest.fixture
def setup(tmp_path: Path, monkeypatch):
    """Output dir with GIFTI and NIfTI maps for both hemis and contrasts; recorded viewer calls."""
    output_dir = tmp_path / "out"
    for desc in ("osgm", "groupdiff"):
        for hemi in ("L", "R"):
            for ext in (".func.gii", ".nii.gz"):
                path = _bids_map(output_dir, hemi, desc, ext)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"")
    calls: list[tuple[dict, Path]] = []
    monkeypatch.setattr(
        step04, "generate_freebrowse_surface_viewer",
        lambda hemi_maps, output_html: calls.append((hemi_maps, output_html)),
    )
    return output_dir, calls


def _link(output_dir: Path, contrast: str = "osgm", hemis=("lh", "rh"), **args) -> str:
    return _build_surface_viewer_link(
        contrast, list(hemis), _context([]), _args(output_dir, **args), output_dir, output_dir / "figures"
    )


def test_gifti_maps_for_both_hemis(setup) -> None:
    output_dir, calls = setup
    fb_name = "tpl-fsaverage_atlas-PetsurferKM_desc-groupdiff_model-MA1_meas-VT_mimap.html"

    link = _link(output_dir, "group-diff")

    assert calls == [(
        {
            "lh": _bids_map(output_dir, "L", "groupdiff", ".func.gii"),
            "rh": _bids_map(output_dir, "R", "groupdiff", ".func.gii"),
        },
        output_dir / "figures" / fb_name,
    )]
    assert link == f'<a href="./figures/{fb_name}" target="_blank">View surfaces in freebrowse</a>'


def test_nifti_surfaces_and_single_hemi(setup) -> None:
    output_dir, calls = setup

    _link(output_dir, hemis=("rh",), nifti_surfaces=True)

    assert calls[0][0] == {"rh": _bids_map(output_dir, "R", "osgm", ".nii.gz")}


def test_no_freebrowse_or_no_maps(setup) -> None:
    output_dir, calls = setup

    assert _link(output_dir, no_freebrowse=True) == ""
    assert _link(output_dir, "unknown") == ""
    assert calls == []


def test_generation_failure_is_a_warning(setup, monkeypatch, caplog) -> None:
    output_dir, _ = setup

    def boom(hemi_maps, output_html):
        raise RuntimeError("FREESURFER_HOME is not set")

    monkeypatch.setattr(step04, "generate_freebrowse_surface_viewer", boom)
    with caplog.at_level(logging.WARNING, logger="petsurfer_km"):
        assert _link(output_dir) == ""
    assert "Could not generate freebrowse surface viewer for osgm" in caplog.text


def test_figure_sections_link_one_viewer_per_contrast(setup, tmp_path: Path, monkeypatch) -> None:
    output_dir, calls = setup
    monkeypatch.setattr(step04, "generate_surface_figure", lambda *a, **k: None)
    workdir = tmp_path / "work"
    for space in ("fsaverage-lh", "fsaverage-rh"):
        for contrast in CONTRASTS:
            gamma = workdir / f"glm.{space}" / contrast / "gamma.nii.gz"
            gamma.parent.mkdir(parents=True)
            gamma.write_bytes(b"")
    context = _context(["fsaverage-lh", "fsaverage-rh", "ROI"])

    sections = _build_figure_sections(
        context, _args(output_dir), workdir, output_dir, output_dir / "figures", None
    )

    assert [sorted(hemi_maps) for hemi_maps, _ in calls] == [["lh", "rh"], ["lh", "rh"]]
    assert len(sections) == 2
    for section, desc in zip(sections, ("groupdiff", "osgm")):
        rh_figure = section.index("Surface (RH, fsaverage)")
        link = section.index(f"desc-{desc}_model-MA1_meas-VT_mimap.html")
        assert section.count("View surfaces in freebrowse") == 1
        assert link > rh_figure
