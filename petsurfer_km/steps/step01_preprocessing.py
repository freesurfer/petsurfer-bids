"""Preprocessing step for petsurfer-km (TAC extraction, etc.)."""

import logging
from pathlib import Path

from petsurfer_km.inputs import InputGroup

logger = logging.getLogger("petsurfer_km")


def run_preprocessing(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
) -> None:
    """
    Run preprocessing steps (TAC extraction, reference region preparation).

    Args:
        subject: Subject ID (without 'sub-' prefix).
        session: Session ID (without 'ses-' prefix), or None.
        inputs: InputGroup with paths to input files.
        temps: Dict to store paths to temporary/intermediate files.
        workdir: Working directory for this subject/session.
        command_history: List to append (description, command) tuples.

    Raises:
        RuntimeError: If preprocessing fails.
    """
    logger.info(f"Running preprocessing for {inputs.label}")

    # TODO: Implement preprocessing steps
    # - Extract reference region TACs
    # - Prepare data for kinetic modeling

    logger.warning("Preprocessing not yet implemented")
