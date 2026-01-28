"""Volumetric processing step for petsurfer-km."""

import logging
from argparse import Namespace
from pathlib import Path

from petsurfer_km.execution import run_command
from petsurfer_km.inputs import InputGroup

logger = logging.getLogger("petsurfer_km")


def run_volumetric(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """
    Run volumetric processing steps (MNI space analysis).

    Implements:
    - Compute mean PET volume
    - Create brain mask
    - Smooth MNI volume

    Args:
        subject: Subject ID (without 'sub-' prefix).
        session: Session ID (without 'ses-' prefix), or None.
        inputs: InputGroup with paths to input files.
        temps: Dict to store paths to temporary/intermediate files.
        workdir: Working directory for this subject/session.
        command_history: List to append (description, command) tuples.
        args: Parsed command-line arguments.

    Raises:
        RuntimeError: If volumetric processing fails.
    """
    if args.no_vol:
        logger.debug(f"Skipping volumetric processing for {inputs.label} (--no-vol)")
        return

    if not inputs.has_volumetric():
        logger.warning(f"Skipping volumetric processing for {inputs.label}: no MNI volume")
        return

    logger.info(f"Running volumetric processing for {inputs.label}")

    # Compute mean PET volume
    _compute_mean_volume(inputs.pet_mni, workdir, temps, command_history)

    # Create brain mask
    _create_brain_mask(temps["mni_mean"], workdir, temps, command_history)

    # Smooth MNI volume
    _smooth_volume(inputs.pet_mni, temps["mni_mask"], workdir, temps, command_history, args.vol_fwhm)


def _compute_mean_volume(
    mni_pet: Path,
    workdir: Path,
    temps: dict[str, Path],
    command_history: list[tuple[str, str]],
) -> None:
    """
    Compute temporal mean across all PET frames.

    Command: mri_concat <mni_pet> --mean --o <output>
    Output: mni152.mean.nii.gz

    Adds to temps:
        mni_mean: Path to mni152.mean.nii.gz
    """
    output_file = workdir / "mni152.mean.nii.gz"

    cmd = [
        "mri_concat",
        str(mni_pet),
        "--mean",
        "--o", str(output_file),
    ]

    result = run_command(cmd, "Compute mean PET volume")
    command_history.append((result.command, "Compute mean PET volume"))

    if result.exit_code != 0:
        raise RuntimeError(
            f"Failed to compute mean volume: {result.stderr}"
        )

    if not output_file.exists():
        raise RuntimeError(
            f"Mean volume not created: {output_file}"
        )

    temps["mni_mean"] = output_file
    logger.debug(f"Mean volume computed: {output_file}")


def _create_brain_mask(
    mean_volume: Path,
    workdir: Path,
    temps: dict[str, Path],
    command_history: list[tuple[str, str]],
) -> None:
    """
    Create binary brain mask from mean PET volume.

    Command: mri_binarize --i <mean_vol> --min 1e-9 --o <output>
    Output: mni152.mask.nii.gz

    Adds to temps:
        mni_mask: Path to mni152.mask.nii.gz
    """
    output_file = workdir / "mni152.mask.nii.gz"

    cmd = [
        "mri_binarize",
        "--i", str(mean_volume),
        "--min", "1e-9",
        "--o", str(output_file),
    ]

    result = run_command(cmd, "Create brain mask")
    command_history.append((result.command, "Create brain mask"))

    if result.exit_code != 0:
        raise RuntimeError(
            f"Failed to create brain mask: {result.stderr}"
        )

    if not output_file.exists():
        raise RuntimeError(
            f"Brain mask not created: {output_file}"
        )

    temps["mni_mask"] = output_file
    logger.debug(f"Brain mask created: {output_file}")


def _smooth_volume(
    mni_pet: Path,
    mask: Path,
    workdir: Path,
    temps: dict[str, Path],
    command_history: list[tuple[str, str]],
    vol_fwhm: float,
) -> None:
    """
    Apply spatial smoothing to the dynamic PET volume.

    If vol_fwhm > 0: mri_fwhm --fwhm <volfwhm> --i <mni_pet> --o <output> --mask <mask> --smooth-only
    If vol_fwhm == 0: mri_convert <mni_pet> <output> (copy unchanged)

    Output: mni152.sm<NN>.nii.gz (where NN = zero-padded vol-fwhm)

    Adds to temps:
        mni_smoothed: Path to mni152.sm<NN>.nii.gz
    """
    # Format FWHM as zero-padded integer for filename
    fwhm_str = f"{int(vol_fwhm):02d}"
    output_file = workdir / f"mni152.sm{fwhm_str}.nii.gz"

    if vol_fwhm > 0:
        cmd = [
            "mri_fwhm",
            "--fwhm", str(vol_fwhm),
            "--i", str(mni_pet),
            "--o", str(output_file),
            "--mask", str(mask),
            "--smooth-only",
        ]
        description = f"Smooth MNI volume (FWHM={vol_fwhm}mm)"
    else:
        cmd = [
            "mri_convert",
            str(mni_pet),
            str(output_file),
        ]
        description = "Copy MNI volume (no smoothing)"

    result = run_command(cmd, description)
    command_history.append((result.command, description))

    if result.exit_code != 0:
        raise RuntimeError(
            f"Failed to smooth volume: {result.stderr}"
        )

    if not output_file.exists():
        raise RuntimeError(
            f"Smoothed volume not created: {output_file}"
        )

    temps["mni_smoothed"] = output_file
    logger.debug(f"Smoothed volume created: {output_file}")
