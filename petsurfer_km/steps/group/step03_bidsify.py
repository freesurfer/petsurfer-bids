"""BIDS-compliant output step for group-level petsurfer-km results.

Copies GLM contrast estimate results (gamma maps + ROI gamma table) from the working
directory to the output directory with BIDS-compliant naming per the BIDS Atlas
specification (BEP 38):

  - Contrast estimate parametric maps (volume/surface): ``_mimap.nii.gz`` + ``_mimap.json``
    under ``tpl-<space>/pet/`` with ``atlas-PetsurferKM`` and ``desc-<contrast>``.
  - Per-ROI contrast estimate kinetic parameters (tabular): ``_kinpar.tsv`` + ``_kinpar.json``
    at the output root with ``atlas-PetsurferKM`` and ``desc-<contrast>``.
  - ``atlas-PetsurferKM_description.json`` (required by BEP 38 when both
    ``tpl-`` and ``atlas-`` entities are present).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from argparse import Namespace
from pathlib import Path

import petsurfer_km
from petsurfer_km import __version__
from petsurfer_km.methods import HEMI_BIDS, ROI_TSV_HEADERS
from petsurfer_km.steps.group.step01_setup import GroupContext

logger = logging.getLogger("petsurfer_km")

ATLAS_LABEL = "PetsurferKM"


def _sanitize_contrast(label: str) -> str:
    """Make an FSGD contrast label BIDS-label-valid: keep [0-9a-zA-Z+], drop the
    rest (hyphens are entity separators in BIDS, so 'group-diff' -> 'groupdiff')."""
    return re.sub(r"[^0-9a-zA-Z+]", "", label)


def run_group_bidsify(context: GroupContext, args: Namespace, workdir: Path) -> None:
    """Copy GLM contrast estimate results into ``args.output_dir`` with BIDS-compliant names."""
    logger.info(f"Writing BIDS group outputs to {args.output_dir}")
    _ensure_dataset_description(args.output_dir, args.petsurfer_dir)
    sample_size = _read_sample_size(workdir, context)
    _write_atlas_description(args.output_dir, sample_size, context)

    for space in context.spaces:
        if space == "ROI":
            _bidsify_roi(workdir, args.output_dir, context)
        else:
            _bidsify_map(workdir, args.output_dir, args, space, context)

    logger.info(f"BIDS group outputs written to {args.output_dir}")


# ---------------------------------------------------------------------------
# Space mapping
# ---------------------------------------------------------------------------

def _space_output(space: str, args: Namespace) -> tuple[str, str | None, int]:
    """Map an internal space name to (tpl_label, hemisphere, fwhm)."""
    if space == "fsaverage-lh":
        return ("fsaverage", "L", int(args.surf_fwhm))
    if space == "fsaverage-rh":
        return ("fsaverage", "R", int(args.surf_fwhm))
    if space == "mni":
        return ("MNI152NLin2009cAsym", None, int(args.vol_fwhm))
    raise ValueError(f"Unknown space: {space}")


# ---------------------------------------------------------------------------
# Contrast discovery
# ---------------------------------------------------------------------------

def _discover_contrasts_map(glmdir: Path) -> list[str]:
    """Enumerate contrast subdirectories of ``glm.<space>/`` containing gamma.nii.gz."""
    contrasts: list[str] = []
    for entry in sorted(glmdir.iterdir()):
        if entry.is_dir() and (entry / "gamma.nii.gz").exists():
            contrasts.append(entry.name)
    if not contrasts:
        logger.warning(f"No contrast dirs with gamma.nii.gz in {glmdir}")
    return contrasts


def _discover_contrasts_roi(glmdir: Path) -> list[str]:
    """Discover contrasts from the column headers of ``glm.ROI/gamma.table.dat``."""
    table = glmdir / "gamma.table.dat"
    if not table.exists():
        logger.warning(f"No gamma.table.dat in {glmdir}")
        return []
    with open(table) as f:
        header = f.readline().split()
    return header[1:]  # drop "Subject"


def _check_collision(contrasts: list[str]) -> None:
    """Raise if two raw contrasts sanitize to the same BIDS label."""
    seen: dict[str, str] = {}
    for raw in contrasts:
        sanitized = _sanitize_contrast(raw)
        if sanitized in seen:
            logger.error(
                f"Contrast labels '{seen[sanitized]}' and '{raw}' "
                f"sanitize to the same BIDS label '{sanitized}'"
            )
            raise RuntimeError(
                f"Contrast labels '{seen[sanitized]}' and '{raw}' "
                f"sanitize to the same BIDS label '{sanitized}'"
            )
        seen[sanitized] = raw


# ---------------------------------------------------------------------------
# BIDSify: voxel/surface maps
# ---------------------------------------------------------------------------

def _bidsify_map(
    workdir: Path,
    output_dir: Path,
    args: Namespace,
    space: str,
    context: GroupContext,
) -> None:
    """Emit one contrast estimate mimap per contrast for a voxel/surface space."""
    glmdir = workdir / f"glm.{space}"
    contrasts = _discover_contrasts_map(glmdir)
    if not contrasts:
        return
    _check_collision(contrasts)

    tpl, hemi, fwhm = _space_output(space, args)
    outdir = output_dir / f"tpl-{tpl}" / "pet"
    outdir.mkdir(parents=True, exist_ok=True)

    for contrast in contrasts:
        src = glmdir / contrast / "gamma.nii.gz"
        if not src.exists():
            logger.warning(f"Expected gamma.nii.gz not found, skipping: {src}")
            continue
        sanitized = _sanitize_contrast(contrast)
        parts = [f"tpl-{tpl}"]
        if hemi:
            parts.append(f"hemi-{hemi}")
        parts += [
            f"atlas-{ATLAS_LABEL}",
            f"desc-{sanitized}",
            f"model-{context.model}",
            f"meas-{context.meas}",
            "mimap",
        ]
        name = "_".join(parts)
        _copy_nifti(src, outdir / f"{name}.nii.gz")
        _write_json(
            outdir / f"{name}.json",
            _build_mimap_sidecar(context, space, fwhm, contrast),
        )


# ---------------------------------------------------------------------------
# BIDSify: ROI table
# ---------------------------------------------------------------------------

def _bidsify_roi(workdir: Path, output_dir: Path, context: GroupContext) -> None:
    """Emit one per-ROI contrast estimate kinpar TSV per contrast."""
    glmdir = workdir / "glm.ROI"
    contrasts = _discover_contrasts_roi(glmdir)
    if not contrasts:
        return
    _check_collision(contrasts)

    for contrast in contrasts:
        sanitized = _sanitize_contrast(contrast)
        name = f"atlas-{ATLAS_LABEL}_desc-{sanitized}_model-{context.model}_kinpar"
        _convert_gamma_table_to_tsv(
            glmdir / "gamma.table.dat",
            output_dir / f"{name}.tsv",
            context.km_method,
            contrast,
        )
        _write_json(
            output_dir / f"{name}.json",
            _build_kinpar_sidecar(context, contrast),
        )


# ---------------------------------------------------------------------------
# Gamma table -> TSV conversion
# ---------------------------------------------------------------------------

def _convert_gamma_table_to_tsv(
    src: Path, dest: Path, method: str, contrast: str
) -> None:
    """Extract one contrast's column from ``gamma.table.dat`` into a BIDS TSV."""
    if not src.exists():
        logger.warning(f"Expected output not found, skipping: {src}")
        return

    with open(src) as f:
        lines = f.readlines()

    header = lines[0].split()
    try:
        col_index = header.index(contrast)
    except ValueError:
        logger.warning(f"Contrast '{contrast}' not in {src.name} header")
        return

    value_label = ROI_TSV_HEADERS[method][1]

    with open(dest, "w") as f:
        f.write(f"ROI\t{value_label}\n")
        for line in lines[1:]:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            f.write(f"{fields[0]}\t{fields[col_index]}\n")

    logger.info(f"  {dest.name}")


# ---------------------------------------------------------------------------
# Sidecar builders
# ---------------------------------------------------------------------------

def _build_mimap_sidecar(
    context: GroupContext, space: str, fwhm: int, contrast: str
) -> dict:
    return {
        "ModelName": context.model,
        "SoftwareName": "petsurfer-km",
        "SoftwareVersion": __version__,
        "ContrastName": contrast,
        "SmoothingFWHM": fwhm,
        "Description": (
            f"{context.meas} contrast estimate parametric map ({contrast} contrast) "
            f"in {space} from {context.model} model"
        ),
    }


def _build_kinpar_sidecar(context: GroupContext, contrast: str) -> dict:
    return {
        "ModelName": context.model,
        "SoftwareName": "petsurfer-km",
        "SoftwareVersion": __version__,
        "ContrastName": contrast,
        "Description": (
            f"Per-ROI {ROI_TSV_HEADERS[context.km_method][1]} contrast estimate "
            f"({contrast} contrast) kinetic parameters from {context.model} model"
        ),
    }


# ---------------------------------------------------------------------------
# Atlas + dataset description
# ---------------------------------------------------------------------------

def _write_atlas_description(
    output_dir: Path, sample_size: int | None, context: GroupContext
) -> None:
    desc: dict = {
        "Name": "petsurfer-km contrast estimate atlas",
        "Authors": ["FreeSurfer petsurfer-km contributors"],
        "License": "CC0",
        "ReferencesAndLinks": [
            "https://doi.org/10.1016/j.neuroimage.2013.12.021",
            "https://doi.org/10.1016/j.neuroimage.2016.02.042",
        ],
        "Species": "Human",
        "DerivedFrom": "PET kinetic modeling (petsurfer-km participant level)",
        "Description": (
            f"Contrast estimate {context.meas} parametric maps aggregated across "
            f"{sample_size} subject(s) by petsurfer-km ({context.model} model)."
        ),
    }
    if sample_size is not None:
        desc["SampleSize"] = sample_size
    _write_json(output_dir / f"atlas-{ATLAS_LABEL}_description.json", desc)


def _ensure_dataset_description(output_dir: Path, petsurfer_dir: Path) -> None:
    """Write ``dataset_description.json`` at derivative root if absent.

    Copies the ``SourceDatasets`` entry from the participant petsurfer-km
    ``dataset_description.json`` if available, preserving the provenance chain.
    """
    desc_file = output_dir / "dataset_description.json"
    if desc_file.exists():
        return
    output_dir.mkdir(parents=True, exist_ok=True)

    desc: dict = {
        "Name": "petsurfer-km",
        "BIDSVersion": "1.9.0",
        "DatasetType": "derivative",
        "GeneratedBy": [{
            "Name": "petsurfer-km",
            "Version": __version__,
            "CodeURL": "https://github.com/freesurfer/petsurfer-km",
        }],
        "HowToAcknowledge": (
            "Please cite 1) https://doi.org/10.1016/j.neuroimage.2013.12.021 "
            "and 2) https://doi.org/10.1016/j.neuroimage.2016.02.042"
        ),
        "License": "CC0",
    }

    src_desc = petsurfer_dir / "dataset_description.json"
    if src_desc.exists():
        try:
            with open(src_desc) as f:
                pd = json.load(f)
            if "SourceDatasets" in pd:
                desc["SourceDatasets"] = pd["SourceDatasets"]
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not read {src_desc}: {e}")

    _write_json(desc_file, desc)
    logger.info(f"Created {desc_file}")


# ---------------------------------------------------------------------------
# Sample size
# ---------------------------------------------------------------------------

def _read_sample_size(workdir: Path, context: GroupContext) -> int | None:
    """Determine the number of subjects in the group analysis."""
    if context.fsgd is not None:
        return len(context.fsgd.df)
    for space in context.spaces:
        dof = workdir / f"glm.{space}" / "dof.dat"
        if dof.exists():
            try:
                return int(dof.read_text().strip()) + 1
            except ValueError:
                logger.warning(f"Could not parse {dof}")
                return None
    logger.warning("Could not determine sample size (no dof.dat found)")
    return None


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict) -> None:
    """Write a JSON sidecar file."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    logger.debug(f"  Wrote {path.name}")


def _copy_nifti(src: Path, dest: Path) -> None:
    """Copy a NIfTI file with logging."""
    if not src.exists():
        logger.warning(f"Expected output not found, skipping: {src}")
        return
    shutil.copy2(src, dest)
    logger.info(f"  {dest.name}")
