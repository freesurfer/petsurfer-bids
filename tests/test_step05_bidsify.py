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

from petsurfer_km.methods import ROI_TSV_HEADERS
from petsurfer_km.steps.participant.step05_bidsify import _convert_dat_to_tsv


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
    _convert_dat_to_tsv(src, dest, "ma1")
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
    assert "not-a-method" not in ROI_TSV_HEADERS
    with pytest.raises(KeyError):
        _convert_dat_to_tsv(src, dest, "not-a-method")


# --- run_bidsify: surface map format ------------------------------------------

import json
from types import SimpleNamespace

import petsurfer_km.steps.participant.step05_bidsify as step05
from petsurfer_km.inputs import InputGroup
from petsurfer_km.steps.participant.step05_bidsify import run_bidsify


def _surface_fixture(tmp_path: Path, nifti_surfaces: bool):
    """Build args/temps/workdir for a MA1 lh+rh surface-only bidsify run."""
    workdir = tmp_path / "work"
    temps: dict[str, Path] = {}
    for hemi in ("lh", "rh"):
        d = workdir / f"ma1.fsaverage.{hemi}.sm05"
        d.mkdir(parents=True)
        (d / "vt.nii.gz").write_bytes(f"fake-{hemi}-vt".encode())
        temps[f"ma1_surf_{hemi}_dir"] = d
    args = SimpleNamespace(
        output_dir=tmp_path / "out",
        petprep_dir=tmp_path / "petprep",
        km_method=["ma1"],
        vol_fwhm=6.0,
        surf_fwhm=5.0,
        hemispheres=["lh", "rh"],
        tstar=540.0,
        ref_roi=None,
        ref_roi_label=None,
        nifti_surfaces=nifti_surfaces,
    )
    inputs = InputGroup(subject="01", session="baseline", tracer="11CPS13")
    return args, temps, workdir, inputs


def _fake_convert(calls: list):
    def fake(src: Path, dest: Path, hemi: str, command_history=None) -> bool:
        calls.append((src, dest, hemi))
        dest.write_bytes(b"fake-gifti")
        if command_history is not None:
            command_history.append((f"mri_convert {src} {dest}", f"Convert {hemi}"))
        return True
    return fake


def test_run_bidsify_writes_gifti_surfaces_by_default(tmp_path: Path, monkeypatch) -> None:
    args, temps, workdir, inputs = _surface_fixture(tmp_path, nifti_surfaces=False)
    calls: list = []
    monkeypatch.setattr(step05, "nifti_to_func_gii", _fake_convert(calls))
    history: list[tuple[str, str]] = []
    mappings: list[tuple[str, str]] = []

    run_bidsify("01", "baseline", inputs, temps, workdir, history, args, mappings)

    pet = args.output_dir / "sub-01" / "ses-baseline" / "pet"
    stem = "sub-01_ses-baseline_trc-11CPS13_hemi-{h}_space-fsaverage_desc-sm5_model-MA1_meas-VT_mimap"
    for h, hemi in (("L", "lh"), ("R", "rh")):
        gii = pet / f"{stem.format(h=h)}.func.gii"
        assert gii.exists() and gii.read_bytes() == b"fake-gifti"
        assert not (pet / f"{stem.format(h=h)}.nii.gz").exists()
        sidecar = json.loads((pet / f"{stem.format(h=h)}.json").read_text())
        assert sidecar["ModelName"] == "MA1"
        assert sidecar["Tstar"] == 540.0
        assert sidecar["Density"].startswith("163842 vertices")
        assert sidecar["SpatialReference"].endswith(f"tpl-fsaverage_hemi-{h}_den-164k_white.surf.gii")
        assert f"fsaverage {hemi} surface" in sidecar["Description"]
    assert [(c[0].name, c[1].suffix, c[2]) for c in calls] == [
        ("vt.nii.gz", ".gii", "lh"),
        ("vt.nii.gz", ".gii", "rh"),
    ]
    assert len(history) == 2
    assert mappings == [
        ("ma1.fsaverage.lh.sm05/vt.nii.gz", f"pet/{stem.format(h='L')}.func.gii"),
        ("ma1.fsaverage.rh.sm05/vt.nii.gz", f"pet/{stem.format(h='R')}.func.gii"),
    ]


def test_run_bidsify_nifti_surfaces_flag_copies_nifti(tmp_path: Path, monkeypatch) -> None:
    args, temps, workdir, inputs = _surface_fixture(tmp_path, nifti_surfaces=True)
    calls: list = []
    monkeypatch.setattr(step05, "nifti_to_func_gii", _fake_convert(calls))
    mappings: list[tuple[str, str]] = []

    run_bidsify("01", "baseline", inputs, temps, workdir, [], args, mappings)

    pet = args.output_dir / "sub-01" / "ses-baseline" / "pet"
    stem = "sub-01_ses-baseline_trc-11CPS13_hemi-L_space-fsaverage_desc-sm5_model-MA1_meas-VT_mimap"
    nii = pet / f"{stem}.nii.gz"
    assert nii.exists() and nii.read_bytes() == b"fake-lh-vt"
    assert not (pet / f"{stem}.func.gii").exists()
    assert calls == []
    sidecar = json.loads((pet / f"{stem}.json").read_text())
    assert "Density" in sidecar and "SpatialReference" in sidecar  # describe the data, not the container
    assert mappings[0] == ("ma1.fsaverage.lh.sm05/vt.nii.gz", f"pet/{stem}.nii.gz")


def test_run_bidsify_missing_surface_source_skips(tmp_path: Path, monkeypatch, caplog) -> None:
    """Real helper, faked mri_convert: rh source missing -> warning, no rh output; lh still written."""
    import petsurfer_km.gifti as gifti
    from petsurfer_km.execution import CommandResult

    args, temps, workdir, inputs = _surface_fixture(tmp_path, nifti_surfaces=False)
    (temps["ma1_surf_rh_dir"] / "vt.nii.gz").unlink()

    def fake_run_command(cmd, description):
        Path(cmd[-1]).write_bytes(b"fake-gifti")
        return CommandResult(0, " ".join(cmd), "", "")

    monkeypatch.setattr(gifti, "run_command", fake_run_command)
    mappings: list[tuple[str, str]] = []
    with caplog.at_level(logging.WARNING, logger="petsurfer_km"):
        run_bidsify("01", "baseline", inputs, temps, workdir, [], args, mappings)

    assert "Expected output not found" in caplog.text
    pet = args.output_dir / "sub-01" / "ses-baseline" / "pet"
    assert len(list(pet.glob("*hemi-L*.func.gii"))) == 1
    assert len(list(pet.glob("*hemi-R*.func.gii"))) == 0
    assert len(list(pet.glob("*hemi-R*.json"))) == 0  # no orphan sidecar
    assert len(mappings) == 1 and "hemi-L" in mappings[0][1]


def test_run_bidsify_missing_volume_and_roi_sources_write_no_sidecar(tmp_path: Path, caplog) -> None:
    """Missing vt.nii.gz (MNI) and vt.dat (ROI) -> warnings, no map, no orphan .json."""
    args, temps, workdir, inputs = _surface_fixture(tmp_path, nifti_surfaces=True)
    temps.clear()
    for key, sub in (("ma1_mni_dir", "ma1.mni.sm06"), ("ma1_roi_dir", "ma1.roi")):
        d = workdir / sub
        d.mkdir(parents=True)
        temps[key] = d  # directories exist, files do not
    mappings: list[tuple[str, str]] = []
    with caplog.at_level(logging.WARNING, logger="petsurfer_km"):
        run_bidsify("01", "baseline", inputs, temps, workdir, [], args, mappings)
    pet = args.output_dir / "sub-01" / "ses-baseline" / "pet"
    assert caplog.text.count("Expected output not found") == 2
    assert sorted(p.name for p in pet.iterdir()) == []
    assert mappings == []


def test_run_bidsify_dataset_description_bids_version(tmp_path: Path) -> None:
    args, temps, workdir, inputs = _surface_fixture(tmp_path, nifti_surfaces=True)
    temps.clear()
    run_bidsify("01", "baseline", inputs, temps, workdir, [], args, [])
    desc = json.loads((args.output_dir / "dataset_description.json").read_text())
    assert desc["BIDSVersion"] == "1.11.1"
    assert desc["DatasetType"] == "derivative"
