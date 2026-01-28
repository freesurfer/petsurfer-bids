"""Volumetric processing step for petsurfer-km."""

import logging
from argparse import Namespace
from pathlib import Path

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

    # TODO: Implement volumetric processing steps
    # - Smoothing (--vol-fwhm)
    # - Masking
    # - Other MNI space operations

    logger.warning("Volumetric processing not yet implemented")
