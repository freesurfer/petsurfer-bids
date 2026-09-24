"""Tests for the ``mri_convert``-based NIfTI -> ``.func.gii`` helper.

The unit tests fake ``run_command`` so no FreeSurfer is needed.  One
integration test runs the real ``mri_convert`` and is skipped when it is not
on PATH (source the FreeSurfer environment to enable it).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

import petsurfer_km.gifti as gifti
from petsurfer_km.execution import CommandResult
from petsurfer_km.gifti import (
    FSAVERAGE_DENSITY,
    fsaverage_spatial_reference,
    nifti_to_func_gii,
    surface_sidecar_fields,
)


def _fake_run_command(exit_code: int = 0, write_output: bool = True):
    """Return a ``run_command`` stand-in that records calls and optionally writes dest."""
    calls: list[tuple[list[str], str]] = []

    def fake(cmd: list[str], description: str) -> CommandResult:
        calls.append((list(cmd), description))
        if write_output:
            Path(cmd[-1]).write_bytes(b"<GIFTI/>")
        return CommandResult(
            exit_code=exit_code,
            command=" ".join(cmd),
            stdout="",
            stderr="boom" if exit_code else "",
        )

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


# --- unit tests (no FreeSurfer) ------------------------------------------------

def test_calls_mri_convert_and_records_history(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "vt.nii.gz"
    src.write_bytes(b"fake")
    dest = tmp_path / "sub-01_hemi-L_space-fsaverage_mimap.func.gii"
    fake = _fake_run_command()
    monkeypatch.setattr(gifti, "run_command", fake)
    history: list[tuple[str, str]] = []

    assert nifti_to_func_gii(src, dest, "lh", history) is True

    assert fake.calls == [(["mri_convert", str(src), str(dest)], "Convert lh surface overlay to GIFTI")]
    assert dest.exists()
    assert history == [(f"mri_convert {src} {dest}", "Convert lh surface overlay to GIFTI")]


def test_history_optional(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "vt.nii.gz"
    src.write_bytes(b"fake")
    monkeypatch.setattr(gifti, "run_command", _fake_run_command())
    assert nifti_to_func_gii(src, tmp_path / "out.func.gii", "rh") is True


def test_nonzero_exit_raises(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "vt.nii.gz"
    src.write_bytes(b"fake")
    monkeypatch.setattr(gifti, "run_command", _fake_run_command(exit_code=1))
    history: list[tuple[str, str]] = []
    with pytest.raises(RuntimeError, match="mri_convert failed"):
        nifti_to_func_gii(src, tmp_path / "out.func.gii", "lh", history)
    # The failed command is still recorded for the report.
    assert len(history) == 1


def test_missing_output_raises(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "vt.nii.gz"
    src.write_bytes(b"fake")
    monkeypatch.setattr(gifti, "run_command", _fake_run_command(write_output=False))
    with pytest.raises(RuntimeError, match="did not write"):
        nifti_to_func_gii(src, tmp_path / "out.func.gii", "lh")


def test_missing_source_warns_and_returns_false(tmp_path: Path, monkeypatch, caplog) -> None:
    fake = _fake_run_command()
    monkeypatch.setattr(gifti, "run_command", fake)
    dest = tmp_path / "out.func.gii"
    with caplog.at_level(logging.WARNING, logger="petsurfer_km"):
        assert nifti_to_func_gii(tmp_path / "missing.nii.gz", dest, "lh") is False
    assert "Expected output not found" in caplog.text
    assert fake.calls == []
    assert not dest.exists()


def test_sidecar_fields() -> None:
    fields = surface_sidecar_fields("L")
    assert fields["Density"] == FSAVERAGE_DENSITY
    assert fields["SpatialReference"] == fsaverage_spatial_reference("L")
    assert fsaverage_spatial_reference("R").endswith("tpl-fsaverage_hemi-R_den-164k_white.surf.gii")
    assert fsaverage_spatial_reference("R").startswith("https://templateflow.s3.amazonaws.com/")


# --- integration test (real mri_convert) --------------------------------------

@pytest.mark.skipif(shutil.which("mri_convert") is None, reason="mri_convert not on PATH")
def test_real_mri_convert_roundtrip(tmp_path: Path) -> None:
    """mri_convert writes one float32 NIFTI_INTENT_NONE array with identical values."""
    import nibabel as nb
    import numpy as np

    rng = np.random.default_rng(0)
    values = rng.standard_normal(100).astype(np.float32)
    src = tmp_path / "overlay.nii.gz"
    nb.save(nb.Nifti1Image(values.reshape(100, 1, 1), np.eye(4)), src)
    dest = tmp_path / "overlay.func.gii"
    history: list[tuple[str, str]] = []

    assert nifti_to_func_gii(src, dest, "lh", history) is True

    img = nb.load(dest)
    assert len(img.darrays) == 1
    da = img.darrays[0]
    assert da.intent == 0  # NIFTI_INTENT_NONE
    assert da.data.dtype == np.float32
    assert da.data.shape == (100,)
    assert np.array_equal(da.data, values)
