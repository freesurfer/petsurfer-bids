"""Tests for the group-level BIDSify step (issue #14).

Exercises contrast discovery, label sanitization, gamma-table extraction,
sidecar construction, and end-to-end osgm/fsgd BIDSification without requiring
FreeSurfer: synthetic GLM workdirs are built in ``tmp_path``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from petsurfer_km.bidsfsgd import BIDS_FSGD
from petsurfer_km.methods import ROI_TSV_HEADERS
from petsurfer_km.steps.group.step01_setup import GroupContext
from petsurfer_km.steps.group.step03_bidsify import (
    ATLAS_LABEL,
    _build_kinpar_sidecar,
    _build_mimap_sidecar,
    _convert_gamma_table_to_tsv,
    _discover_contrasts_map,
    _discover_contrasts_roi,
    _ensure_dataset_description,
    _read_sample_size,
    _sanitize_contrast,
    _space_output,
    _write_atlas_description,
    run_group_bidsify,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(
    *,
    fsgd: BIDS_FSGD | None = None,
    fsgd_file: Path | None = None,
    km_method: str = "logan-ma1",
    spaces: list[str] | None = None,
) -> GroupContext:
    from petsurfer_km.methods import MEAS_LABELS, MODEL_LABELS
    return GroupContext(
        layout=None,
        spaces=spaces or ["fsaverage-lh", "fsaverage-rh", "mni", "ROI"],
        sessions=["baseline"],
        paired=False,
        tracer="11CPS13",
        km_method=km_method,
        model=MODEL_LABELS[km_method],
        meas=MEAS_LABELS[km_method],
        fsgd=fsgd,
        fsgd_file=fsgd_file,
    )


def _make_args(tmp_path: Path, *, petsurfer_dir: Path | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        output_dir=tmp_path / "out",
        surf_fwhm=5.0,
        vol_fwhm=6.0,
        petsurfer_dir=petsurfer_dir or (tmp_path / "petsurfer"),
    )


def _write_fake_gamma(path: Path, content: bytes = b"fake-gamma") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


# ---------------------------------------------------------------------------
# _sanitize_contrast
# ---------------------------------------------------------------------------

def test_sanitize_contrast_strips_hyphens() -> None:
    assert _sanitize_contrast("group-diff") == "groupdiff"
    assert _sanitize_contrast("group-x-age") == "groupxage"
    assert _sanitize_contrast("osgm") == "osgm"
    assert _sanitize_contrast("a+b") == "a+b"
    assert _sanitize_contrast("sex-x-age") == "sexxage"


# ---------------------------------------------------------------------------
# _space_output
# ---------------------------------------------------------------------------

def test_space_output_mapping() -> None:
    args = SimpleNamespace(surf_fwhm=5.0, vol_fwhm=6.0)
    assert _space_output("fsaverage-lh", args) == ("fsaverage", "L", 5)
    assert _space_output("fsaverage-rh", args) == ("fsaverage", "R", 5)
    assert _space_output("mni", args) == ("MNI152NLin2009cAsym", None, 6)


def test_space_output_unknown_raises() -> None:
    with pytest.raises(ValueError):
        _space_output("foo", SimpleNamespace(surf_fwhm=5.0, vol_fwhm=6.0))


# ---------------------------------------------------------------------------
# _discover_contrasts_map
# ---------------------------------------------------------------------------

def test_discover_contrasts_map(tmp_path: Path) -> None:
    glmdir = tmp_path / "glm.mni"
    _write_fake_gamma(glmdir / "osgm" / "gamma.nii.gz")
    _write_fake_gamma(glmdir / "group-diff" / "gamma.nii.gz")
    (glmdir / "X.mat").write_text("not a dir")
    assert _discover_contrasts_map(glmdir) == ["group-diff", "osgm"]


def test_discover_contrasts_map_empty_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    glmdir = tmp_path / "glm.mni"
    glmdir.mkdir()
    (glmdir / "X.mat").write_text("no gamma here")
    with caplog.at_level(logging.WARNING):
        assert _discover_contrasts_map(glmdir) == []
    assert any("No contrast dirs" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# _discover_contrasts_roi
# ---------------------------------------------------------------------------

def test_discover_contrasts_roi_from_header(tmp_path: Path) -> None:
    glmdir = tmp_path / "glm.ROI"
    glmdir.mkdir()
    (glmdir / "gamma.table.dat").write_text(
        "Subject                           osgm  group-diff\n"
        "Left-Thalamus                     2.873  0.3\n"
    )
    assert _discover_contrasts_roi(glmdir) == ["osgm", "group-diff"]


def test_discover_contrasts_roi_missing_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    glmdir = tmp_path / "glm.ROI"
    glmdir.mkdir()
    with caplog.at_level(logging.WARNING):
        assert _discover_contrasts_roi(glmdir) == []
    assert any("gamma.table.dat" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# _convert_gamma_table_to_tsv
# ---------------------------------------------------------------------------

def test_convert_gamma_table_to_tsv_osgm(tmp_path: Path) -> None:
    src = tmp_path / "gamma.table.dat"
    src.write_text(
        "Subject                           osgm\n"
        "Left-Cerebral-White-Matter          2.540\n"
        "Left-Lateral-Ventricle              1.932\n"
    )
    dest = tmp_path / "out.tsv"
    _convert_gamma_table_to_tsv(src, dest, "logan-ma1", "osgm")
    lines = dest.read_text().splitlines()
    assert lines[0] == "ROI\tVT"
    assert lines[1] == "Left-Cerebral-White-Matter\t2.540"
    assert lines[2] == "Left-Lateral-Ventricle\t1.932"
    assert "Subject" not in dest.read_text()


def test_convert_gamma_table_to_tsv_picks_contrast_column(tmp_path: Path) -> None:
    src = tmp_path / "gamma.table.dat"
    src.write_text(
        "Subject                           age  sex  sex-x-age\n"
        "Left-Thalamus                     0.004  -2.775  0.083\n"
    )
    dest = tmp_path / "out.tsv"
    _convert_gamma_table_to_tsv(src, dest, "logan-ma1", "sex-x-age")
    lines = dest.read_text().splitlines()
    assert lines[0] == "ROI\tVT"
    assert lines[1] == "Left-Thalamus\t0.083"


def test_convert_gamma_table_to_tsv_patlak_header(tmp_path: Path) -> None:
    src = tmp_path / "gamma.table.dat"
    src.write_text("Subject                           osgm\nLeft-Thalamus 1.5\n")
    dest = tmp_path / "out.tsv"
    _convert_gamma_table_to_tsv(src, dest, "patlak", "osgm")
    assert dest.read_text().splitlines()[0] == "ROI\tKi"


def test_convert_gamma_table_missing_source_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    dest = tmp_path / "out.tsv"
    with caplog.at_level(logging.WARNING):
        _convert_gamma_table_to_tsv(tmp_path / "missing.dat", dest, "logan-ma1", "osgm")
    assert not dest.exists()
    assert any("not found" in rec.message for rec in caplog.records)


def test_convert_gamma_table_contrast_not_in_header_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    src = tmp_path / "gamma.table.dat"
    src.write_text("Subject                           osgm\nLeft-Thalamus 1.5\n")
    dest = tmp_path / "out.tsv"
    with caplog.at_level(logging.WARNING):
        _convert_gamma_table_to_tsv(src, dest, "logan-ma1", "missing-contrast")
    assert not dest.exists()
    assert any("not in" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# _read_sample_size
# ---------------------------------------------------------------------------

def test_read_sample_size_fsgd(tmp_path: Path) -> None:
    fsgd_file = tmp_path / "test.fsgd"
    fsgd_file.write_text(
        "GroupDescriptorFile 1\n"
        "Title OSGM\n"
        "Class M\n"
        "Class F\n"
        "Variables age\n"
        "Contrast age 0 0 0.5 0.5\n"
        "Contrast sex 1 -1 0 0\n"
        "Contrast sex-x-age 0 0 1 -1\n"
        "Input PS11 F 32\n"
        "Input PS17 M 22\n"
    )
    fsgd = BIDS_FSGD(str(fsgd_file))
    ctx = _make_ctx(fsgd=fsgd, fsgd_file=fsgd_file)
    assert _read_sample_size(tmp_path, ctx) == 2


def test_read_sample_size_osgm_from_dof(tmp_path: Path) -> None:
    glmdir = tmp_path / "glm.mni"
    glmdir.mkdir()
    (glmdir / "dof.dat").write_text("1")
    ctx = _make_ctx(fsgd=None)
    assert _read_sample_size(tmp_path, ctx) == 2


def test_read_sample_size_missing_returns_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = _make_ctx(fsgd=None)
    with caplog.at_level(logging.WARNING):
        assert _read_sample_size(tmp_path, ctx) is None
    assert any("sample size" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Sidecar builders
# ---------------------------------------------------------------------------

def test_mimap_sidecar_fields() -> None:
    ctx = _make_ctx()
    sc = _build_mimap_sidecar(ctx, "mni", 6, "osgm")
    assert sc["ModelName"] == "MA1"
    assert sc["SoftwareName"] == "petsurfer-km"
    assert "SoftwareVersion" in sc
    assert sc["ContrastName"] == "osgm"
    assert sc["SmoothingFWHM"] == 6
    assert "VT" in sc["Description"]
    assert "MA1" in sc["Description"]


def test_kinpar_sidecar_no_smoothing() -> None:
    ctx = _make_ctx()
    sc = _build_kinpar_sidecar(ctx, "osgm")
    assert sc["ContrastName"] == "osgm"
    assert "SmoothingFWHM" not in sc


# ---------------------------------------------------------------------------
# Atlas + dataset description
# ---------------------------------------------------------------------------

def test_atlas_description_required_fields(tmp_path: Path) -> None:
    ctx = _make_ctx()
    _write_atlas_description(tmp_path, 16, ctx)
    desc = json.loads((tmp_path / f"atlas-{ATLAS_LABEL}_description.json").read_text())
    for key in ("Name", "Authors", "License", "ReferencesAndLinks",
                "Species", "DerivedFrom", "Description", "SampleSize"):
        assert key in desc
    assert desc["SampleSize"] == 16
    assert "16 subject" in desc["Description"]


def test_atlas_description_no_sample_size(tmp_path: Path) -> None:
    ctx = _make_ctx()
    _write_atlas_description(tmp_path, None, ctx)
    desc = json.loads((tmp_path / f"atlas-{ATLAS_LABEL}_description.json").read_text())
    assert "SampleSize" not in desc


def test_dataset_description_copies_source_datasets(tmp_path: Path) -> None:
    petsurfer_dir = tmp_path / "petsurfer"
    petsurfer_dir.mkdir()
    src_desc = {"Name": "petsurfer-km", "SourceDatasets": [{"URL": "https://example.com"}]}
    (petsurfer_dir / "dataset_description.json").write_text(json.dumps(src_desc))
    _ensure_dataset_description(tmp_path / "out", petsurfer_dir)
    desc = json.loads((tmp_path / "out" / "dataset_description.json").read_text())
    assert desc["SourceDatasets"] == src_desc["SourceDatasets"]


def test_dataset_description_idempotent(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    existing = {"Name": "pre-existing"}
    (out / "dataset_description.json").write_text(json.dumps(existing))
    _ensure_dataset_description(out, tmp_path / "petsurfer")
    assert json.loads((out / "dataset_description.json").read_text())["Name"] == "pre-existing"


# ---------------------------------------------------------------------------
# End-to-end: osgm
# ---------------------------------------------------------------------------

def test_run_group_bidsify_osgm_end_to_end(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    # Surface + volume gamma maps
    gamma_lh = b"fake-lh-gamma"
    gamma_rh = b"fake-rh-gamma"
    gamma_mni = b"fake-mni-gamma"
    _write_fake_gamma(workdir / "glm.fsaverage-lh" / "osgm" / "gamma.nii.gz", gamma_lh)
    _write_fake_gamma(workdir / "glm.fsaverage-rh" / "osgm" / "gamma.nii.gz", gamma_rh)
    _write_fake_gamma(workdir / "glm.mni" / "osgm" / "gamma.nii.gz", gamma_mni)
    # dof.dat for sample size
    (workdir / "glm.mni").mkdir(exist_ok=True)
    (workdir / "glm.mni" / "dof.dat").write_text("1")
    # ROI gamma table
    roi_dir = workdir / "glm.ROI"
    roi_dir.mkdir(parents=True)
    (roi_dir / "gamma.table.dat").write_text(
        "Subject                           osgm\n"
        "Left-Cerebral-White-Matter          2.540\n"
        "Left-Thalamus                       2.873\n"
    )
    # Fake petsurfer participant output with dataset_description.json
    petsurfer_dir = tmp_path / "petsurfer"
    petsurfer_dir.mkdir()
    (petsurfer_dir / "dataset_description.json").write_text(
        json.dumps({"Name": "petsurfer-km", "SourceDatasets": [{"URL": "https://example.com"}]})
    )

    ctx = _make_ctx(fsgd=None)
    args = _make_args(tmp_path, petsurfer_dir=petsurfer_dir)
    run_group_bidsify(ctx, args, workdir)

    out = args.output_dir
    # dataset_description.json
    assert (out / "dataset_description.json").exists()
    # atlas description
    atlas_desc = json.loads((out / f"atlas-{ATLAS_LABEL}_description.json").read_text())
    assert atlas_desc["SampleSize"] == 2

    # Surface maps
    for hemi, content in [("L", gamma_lh), ("R", gamma_rh)]:
        nii = out / "tpl-fsaverage" / "pet" / (
            f"tpl-fsaverage_hemi-{hemi}_atlas-{ATLAS_LABEL}_desc-osgm"
            f"_model-MA1_meas-VT_mimap.nii.gz"
        )
        assert nii.exists(), f"Missing {nii}"
        assert nii.read_bytes() == content
        sc = json.loads(nii.with_name(nii.name.replace(".nii.gz", ".json")).read_text())
        assert sc["ContrastName"] == "osgm"
        assert sc["SmoothingFWHM"] == 5

    # Volume map
    nii_mni = out / "tpl-MNI152NLin2009cAsym" / "pet" / (
        f"tpl-MNI152NLin2009cAsym_atlas-{ATLAS_LABEL}_desc-osgm"
        f"_model-MA1_meas-VT_mimap.nii.gz"
    )
    assert nii_mni.exists()
    assert nii_mni.read_bytes() == gamma_mni
    sc_mni = json.loads(nii_mni.with_name(nii_mni.name.replace(".nii.gz", ".json")).read_text())
    assert sc_mni["SmoothingFWHM"] == 6

    # ROI kinpar
    tsv_name = f"atlas-{ATLAS_LABEL}_desc-osgm_model-MA1_kinpar.tsv"
    tsv = out / tsv_name
    assert tsv.exists()
    lines = tsv.read_text().splitlines()
    assert lines[0] == "ROI\tVT"
    assert lines[1] == "Left-Cerebral-White-Matter\t2.540"
    assert lines[2] == "Left-Thalamus\t2.873"
    json_sidecar = json.loads((out / tsv_name.replace(".tsv", ".json")).read_text())
    assert json_sidecar["ContrastName"] == "osgm"
    assert "SmoothingFWHM" not in json_sidecar


# ---------------------------------------------------------------------------
# End-to-end: fsgd (matches real Doug FSGD format)
# ---------------------------------------------------------------------------

FSGD_CONTENT = (
    "GroupDescriptorFile 1\n"
    "Title mytitle\n"
    "Class M\n"
    "Class F\n"
    "Contrast age 0 0 0.5 0.5\n"
    "Contrast sex 1 -1 0 0\n"
    "Contrast sex-x-age 0 0 1 -1\n"
    "Variables age\n"
    "Input PS11 F 32\n"
    "Input PS17 M 22\n"
)


def test_run_group_bidsify_fsgd_end_to_end(tmp_path: Path) -> None:
    fsgd_file = tmp_path / "test.fsgd"
    fsgd_file.write_text(FSGD_CONTENT)
    fsgd = BIDS_FSGD(str(fsgd_file))
    assert len(fsgd.df) == 2

    workdir = tmp_path / "work"
    contrasts = ["age", "sex", "sex-x-age"]
    # Surface + volume gamma maps, one per contrast
    for space in ("glm.fsaverage-lh", "glm.fsaverage-rh", "glm.mni"):
        for c in contrasts:
            _write_fake_gamma(workdir / space / c / "gamma.nii.gz", f"fake-{space}-{c}".encode())

    # ROI gamma table with 3 contrast columns
    roi_dir = workdir / "glm.ROI"
    roi_dir.mkdir(parents=True)
    (roi_dir / "gamma.table.dat").write_text(
        "Subject                           age  sex  sex-x-age\n"
        "Left-Cerebral-White-Matter          0.005  -2.196   0.062\n"
        "Left-Thalamus                       0.004  -2.775   0.083\n"
    )

    # Fake petsurfer participant output
    petsurfer_dir = tmp_path / "petsurfer"
    petsurfer_dir.mkdir()
    (petsurfer_dir / "dataset_description.json").write_text(json.dumps({"Name": "petsurfer-km"}))

    ctx = _make_ctx(fsgd=fsgd, fsgd_file=fsgd_file)
    args = _make_args(tmp_path, petsurfer_dir=petsurfer_dir)
    run_group_bidsify(ctx, args, workdir)

    out = args.output_dir

    # Atlas description: SampleSize == len(fsgd.df) == 2
    atlas_desc = json.loads((out / f"atlas-{ATLAS_LABEL}_description.json").read_text())
    assert atlas_desc["SampleSize"] == 2

    sanitized = {"age": "age", "sex": "sex", "sex-x-age": "sexxage"}

    # Three mimap files per surface space
    for space, tpl, hemi in [
        ("glm.fsaverage-lh", "fsaverage", "L"),
        ("glm.fsaverage-rh", "fsaverage", "R"),
    ]:
        for c in contrasts:
            s = sanitized[c]
            nii = out / "tpl-fsaverage" / "pet" / (
                f"tpl-fsaverage_hemi-{hemi}_atlas-{ATLAS_LABEL}_desc-{s}"
                f"_model-MA1_meas-VT_mimap.nii.gz"
            )
            assert nii.exists(), f"Missing {nii}"
            assert nii.read_bytes() == f"fake-{space}-{c}".encode()
            sc = json.loads(nii.with_name(nii.name.replace(".nii.gz", ".json")).read_text())
            assert sc["ContrastName"] == c  # raw label preserved

    # Three mimap files for mni
    for c in contrasts:
        s = sanitized[c]
        nii = out / "tpl-MNI152NLin2009cAsym" / "pet" / (
            f"tpl-MNI152NLin2009cAsym_atlas-{ATLAS_LABEL}_desc-{s}"
            f"_model-MA1_meas-VT_mimap.nii.gz"
        )
        assert nii.exists(), f"Missing {nii}"

    # Three kinpar TSVs, each holding only its contrast's values
    for c in contrasts:
        s = sanitized[c]
        tsv_name = f"atlas-{ATLAS_LABEL}_desc-{s}_model-MA1_kinpar.tsv"
        tsv = out / tsv_name
        assert tsv.exists(), f"Missing {tsv}"
        lines = tsv.read_text().splitlines()
        assert lines[0] == "ROI\tVT"
        # Each row has only the contrast's value
        col_index = ["age", "sex", "sex-x-age"].index(c)
        roi_data = [
            ("Left-Cerebral-White-Matter", ["0.005", "-2.196", "0.062"]),
            ("Left-Thalamus", ["0.004", "-2.775", "0.083"]),
        ]
        for i, (roi_label, values) in enumerate(roi_data):
            assert lines[i + 1] == f"{roi_label}\t{values[col_index]}"

        # JSON sidecar: raw contrast name, not sanitized
        sc = json.loads((out / tsv_name.replace(".tsv", ".json")).read_text())
        assert sc["ContrastName"] == c

    # Specifically verify sex-x-age: filename uses sanitized, JSON uses raw
    sexxage_tsv = out / f"atlas-{ATLAS_LABEL}_desc-sexxage_model-MA1_kinpar.tsv"
    assert sexxage_tsv.exists()
    sexxage_json = json.loads(
        (out / f"atlas-{ATLAS_LABEL}_desc-sexxage_model-MA1_kinpar.json").read_text()
    )
    assert sexxage_json["ContrastName"] == "sex-x-age"
