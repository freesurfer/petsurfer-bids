"""Surface processing step for petsurfer-km."""

import logging
from argparse import Namespace
from pathlib import Path

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

        logger.debug(f"Processing {hemi} hemisphere")

        # TODO: Implement surface processing steps
        # - Smoothing (--surf-fwhm)
        # - Other fsaverage space operations

    logger.warning("Surface processing not yet implemented")
