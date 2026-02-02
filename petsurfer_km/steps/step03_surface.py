"""Surface processing step for petsurfer-km."""

import logging
from argparse import Namespace
from pathlib import Path

from petsurfer_km.execution import run_command
from petsurfer_km.inputs import InputGroup

logger = logging.getLogger("petsurfer_km")


def run_surface(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """
    Run surface processing steps (fsaverage space analysis).

    Implements:
    - Smooth surface data (for each hemisphere)

    Args:
        subject: Subject ID (without 'sub-' prefix).
        session: Session ID (without 'ses-' prefix), or None.
        inputs: InputGroup with paths to input files.
        temps: Dict to store paths to temporary/intermediate files.
        workdir: Working directory for this subject/session.
        command_history: List to append (description, command) tuples.
        args: Parsed command-line arguments (includes hemispheres, surf_fwhm).

    Raises:
        RuntimeError: If surface processing fails.
    """
    if args.no_surf:
        logger.debug(f"Skipping surface processing for {inputs.label} (--no-surf)")
        return

    if not inputs.has_surface():
        logger.warning(f"Skipping surface processing for {inputs.label}: no surface data")
        return

    logger.info(f"Running surface processing for {inputs.label}")

    for hemi in args.hemispheres:
        if not inputs.has_surface(hemi):
            logger.warning(f"Skipping {hemi} hemisphere for {inputs.label}: no data")
            continue

        # Get the input surface file for this hemisphere
        if hemi == "lh":
            surf_pet = inputs.pet_fsaverage_lh
        else:
            surf_pet = inputs.pet_fsaverage_rh

        # Operation 3.1: Smooth surface data
        _smooth_surface(surf_pet, hemi, workdir, temps, command_history, args.surf_fwhm)


def _smooth_surface(
    surf_pet: Path,
    hemi: str,
    workdir: Path,
    temps: dict[str, Path],
    command_history: list[tuple[str, str]],
    surf_fwhm: float,
) -> None:
    """
    Apply cortical surface smoothing on the fsaverage template.

    If surf_fwhm > 0: mris_fwhm --s fsaverage --hemi <hemi> --fwhm <surffwhm> --cortex --i <surf_pet> --o <output> --smooth-only
    If surf_fwhm == 0: mri_convert <surf_pet> <output> (copy unchanged)

    Output: fsaverage.<hemi>.sm<NN>.nii.gz

    Adds to temps:
        surf_smoothed_<hemi>: Path to fsaverage.<hemi>.sm<NN>.nii.gz
    """
    # Format FWHM as zero-padded integer for filename
    fwhm_str = f"{int(surf_fwhm):02d}"
    output_file = workdir / f"fsaverage.{hemi}.sm{fwhm_str}.nii.gz"

    if surf_fwhm > 0:
        cmd = [
            "mris_fwhm",
            "--s", "fsaverage",
            "--hemi", hemi,
            "--fwhm", str(surf_fwhm),
            "--cortex",
            "--i", str(surf_pet),
            "--o", str(output_file),
            "--smooth-only",
        ]
        description = f"Smooth {hemi} surface (FWHM={surf_fwhm}mm)"
    else:
        cmd = [
            "mri_convert",
            str(surf_pet),
            str(output_file),
        ]
        description = f"Copy {hemi} surface (no smoothing)"

    result = run_command(cmd, description)
    command_history.append((result.command, description))

    if result.exit_code != 0:
        raise RuntimeError(
            f"Failed to smooth {hemi} surface: {result.stderr}"
        )

    if not output_file.exists():
        raise RuntimeError(
            f"Smoothed surface not created: {output_file}"
        )

    temps[f"surf_smoothed_{hemi}"] = output_file
    logger.debug(f"Smoothed {hemi} surface created: {output_file}")
