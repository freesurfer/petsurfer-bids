"""BIDS-compliant output step for petsurfer-km.

Copies primary outputs from the working directory to the output directory
with filenames following BEP023 (PET Preprocessing Derivatives) conventions:

  - Parametric maps (volume/surface): ``_mimap.nii.gz`` + ``_mimap.json``
  - ROI kinetic parameters (tabular): ``_kinpar.tsv`` + ``_kinpar.json``
"""

import json
import logging
import shutil
from argparse import Namespace
from pathlib import Path

from petsurfer_km import __version__
from petsurfer_km.inputs import InputGroup

logger = logging.getLogger("petsurfer_km")

# BEP023 model labels
MODEL_LABELS = {
    "mrtm1": "MRTM1",
    "mrtm2": "MRTM2",
    "logan": "Logan",
    "logan-ma1": "MA1",
}

# Primary measurement per method
MEAS_LABELS = {
    "mrtm1": "BPND",
    "mrtm2": "BPND",
    "logan": "VT",
    "logan-ma1": "VT",
}

# Hemisphere mapping: internal (lh/rh) → BIDS (L/R)
HEMI_BIDS = {"lh": "L", "rh": "R"}


def run_bidsify(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
    file_mappings: list[tuple[str, str]] | None = None,
) -> None:
    """Copy primary outputs to the output directory with BIDS-compliant names.

    Args:
        subject: Subject ID (without 'sub-' prefix).
        session: Session ID (without 'ses-' prefix), or None.
        inputs: InputGroup with paths and tracer info.
        temps: Dict of intermediate file paths from earlier steps.
        workdir: Working directory for this subject/session.
        command_history: List to append (description, command) tuples.
        args: Parsed command-line arguments.
        file_mappings: Optional list to append (work_relative, output_relative) tuples.
    """
    logger.info(f"Writing BIDS outputs for {inputs.label}")

    output_pet_dir = _make_output_dir(args.output_dir, subject, session)
    _ensure_dataset_description(args.output_dir, args.petprep_dir)
    prefix = _build_prefix(inputs)

    # Subject/session output dir (parent of pet/) for relative path computation
    subject_outdir = output_pet_dir.parent

    for method in args.km_method:
        model = MODEL_LABELS[method]
        meas = MEAS_LABELS[method]
        map_file = "bp.nii.gz" if meas == "BPND" else "vt.nii.gz"
        roi_file = "gamma.table.dat" if meas == "BPND" else "vt.dat"
        sidecar = _build_sidecar(method, inputs, temps, args)

        # Volumetric parametric map (MNI152)
        vol_key = f"{method}_mni_dir"
        if vol_key in temps:
            fwhm = int(args.vol_fwhm)
            name = f"{prefix}_space-MNI152NLin2009cAsym_desc-sm{fwhm}_model-{model}_meas-{meas}_mimap"
            src_nifti = temps[vol_key] / map_file
            dst_nifti = output_pet_dir / f"{name}.nii.gz"
            _copy_nifti(src_nifti, dst_nifti)
            if file_mappings is not None and dst_nifti.exists():
                _record_mapping(file_mappings, src_nifti, dst_nifti, workdir, subject_outdir)
            _write_json(output_pet_dir / f"{name}.json", {
                **sidecar,
                "Description": (
                    f"{meas} parametric map in MNI152 space "
                    f"(smoothed {fwhm}mm FWHM)"
                ),
            })

        # Surface parametric maps (fsaverage, per hemisphere)
        for hemi in args.hemispheres:
            surf_key = f"{method}_surf_{hemi}_dir"
            if surf_key in temps:
                fwhm = int(args.surf_fwhm)
                bids_hemi = HEMI_BIDS[hemi]
                name = (
                    f"{prefix}_hemi-{bids_hemi}_space-fsaverage"
                    f"_desc-sm{fwhm}_model-{model}_meas-{meas}_mimap"
                )
                src_nifti = temps[surf_key] / map_file
                dst_nifti = output_pet_dir / f"{name}.nii.gz"
                _copy_nifti(src_nifti, dst_nifti)
                if file_mappings is not None and dst_nifti.exists():
                    _record_mapping(file_mappings, src_nifti, dst_nifti, workdir, subject_outdir)
                _write_json(output_pet_dir / f"{name}.json", {
                    **sidecar,
                    "Description": (
                        f"{meas} parametric map on fsaverage {hemi} surface "
                        f"(smoothed {fwhm}mm FWHM)"
                    ),
                })

        # ROI kinetic parameters (tabular)
        roi_key = f"{method}_roi_dir"
        if roi_key in temps:
            name = f"{prefix}_model-{model}_kinpar"
            src_dat = temps[roi_key] / roi_file
            dst_tsv = output_pet_dir / f"{name}.tsv"
            _convert_dat_to_tsv(src_dat, dst_tsv)
            if file_mappings is not None and dst_tsv.exists():
                _record_mapping(file_mappings, src_dat, dst_tsv, workdir, subject_outdir)
            _write_json(output_pet_dir / f"{name}.json", {
                **sidecar,
                "Description": f"ROI-level {meas} kinetic parameters from {model}",
            })

    logger.info(f"BIDS outputs written to {output_pet_dir}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_output_dir(
    output_dir: Path, subject: str, session: str | None
) -> Path:
    """Create BIDS output directory: ``<output>/sub-XX/[ses-YY/]pet/``."""
    pet_dir = output_dir / f"sub-{subject}"
    if session:
        pet_dir = pet_dir / f"ses-{session}"
    pet_dir = pet_dir / "pet"
    logger.debug(f"Creating output directory: {pet_dir}")
    pet_dir.mkdir(parents=True, exist_ok=True)
    return pet_dir


def _ensure_dataset_description(output_dir: Path, petprep_dir: Path) -> None:
    """Write ``dataset_description.json`` at derivative root if absent.

    Copies the ``SourceDatasets`` entry from the petprep
    ``dataset_description.json`` if available, so the provenance chain
    back to the original raw BIDS dataset is preserved.
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
            "CodeURL": "https://github.com/freesurfer/petsurfer-km"
        }],
        "HowToAcknowledge": "Please cite 1) https://doi.org/10.1016/j.neuroimage.2013.12.021 and 2) https://doi.org/10.1016/j.neuroimage.2016.02.042",
        "License": "CC0",
    }

    # Copy SourceDatasets from petprep's dataset_description.json
    petprep_desc_file = petprep_dir / "dataset_description.json"
    if petprep_desc_file.exists():
        try:
            with open(petprep_desc_file) as f:
                petprep_desc = json.load(f)
            if "SourceDatasets" in petprep_desc:
                desc["SourceDatasets"] = petprep_desc["SourceDatasets"]
                logger.debug("Copied SourceDatasets from petprep dataset_description.json")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not read petprep dataset_description.json: {e}")

    _write_json(desc_file, desc)
    logger.info(f"Created {desc_file}")


def _build_prefix(inputs: InputGroup) -> str:
    """Build BIDS filename prefix: ``sub-XX[_ses-YY][_trc-ZZ]``."""
    parts = [f"sub-{inputs.subject}"]
    if inputs.session:
        parts.append(f"ses-{inputs.session}")
    if inputs.tracer:
        parts.append(f"trc-{inputs.tracer}")
    return "_".join(parts)


def _build_sidecar(
    method: str,
    inputs: InputGroup,
    temps: dict[str, Path],
    args: Namespace,
) -> dict:
    """Build base JSON sidecar content for a given kinetic modeling method."""
    sidecar: dict = {
        "ModelName": MODEL_LABELS[method],
        "SoftwareName": "petsurfer-km",
        "SoftwareVersion": __version__,
    }

    # MRTM methods: reference region
    if method in ("mrtm1", "mrtm2"):
        sidecar["ReferenceRegion"] = args.mrtm1_ref

    # MRTM2: k2prime input value
    if method == "mrtm2" and "k2prime" in temps:
        k2prime_val = _read_k2prime_value(temps["k2prime"])
        if k2prime_val is not None:
            sidecar["InputValues"] = [k2prime_val]
            sidecar["InputValuesLabels"] = ["k2prime"]

    # Logan methods: tstar and blood type
    if method in ("logan", "logan-ma1"):
        sidecar["Tstar"] = args.tstar
        sidecar["BloodType"] = "arterial"

    return sidecar


def _read_k2prime_value(k2prime_file: Path) -> float | None:
    """Read k2prime numeric value from file, or None on failure."""
    try:
        with open(k2prime_file) as f:
            return float(f.read().strip())
    except (ValueError, OSError) as e:
        logger.warning(f"Could not read k2prime from {k2prime_file}: {e}")
        return None


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


def _record_mapping(
    file_mappings: list[tuple[str, str]],
    src: Path,
    dest: Path,
    workdir: Path,
    output_dir: Path,
) -> None:
    """Append a (work-relative, output-relative) mapping entry."""
    try:
        work_rel = str(src.resolve().relative_to(workdir.resolve()))
    except ValueError:
        work_rel = str(src)
    try:
        out_rel = str(dest.resolve().relative_to(output_dir.resolve()))
    except ValueError:
        out_rel = str(dest)
    file_mappings.append((work_rel, out_rel))


def _convert_dat_to_tsv(src: Path, dest: Path) -> None:
    """Convert a FreeSurfer ``.dat`` table to TSV format.

    Normalizes whitespace to tab separation and drops comment lines
    (starting with ``#``).
    """
    if not src.exists():
        logger.warning(f"Expected output not found, skipping: {src}")
        return

    with open(src) as f:
        lines = f.readlines()

    with open(dest, "w") as f:
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            f.write("\t".join(stripped.split()) + "\n")

    logger.info(f"  {dest.name}")
