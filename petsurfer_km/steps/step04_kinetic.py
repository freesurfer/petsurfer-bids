"""Kinetic modeling step for petsurfer-km."""

import logging
from argparse import Namespace
from pathlib import Path

from petsurfer_km.inputs import InputGroup

logger = logging.getLogger("petsurfer_km")

# Canonical order for kinetic modeling methods.
# MRTM2 must run after MRTM1 (depends on k2prime output).
# This order is enforced regardless of the order specified on command line.
KM_METHOD_ORDER = ["mrtm1", "mrtm2", "logan", "logan-ma1"]


def run_kinetic_modeling(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """
    Run kinetic modeling for all requested methods.

    Methods are executed in canonical order (mrtm1, mrtm2, logan, logan-ma1)
    regardless of the order specified on the command line. This ensures
    dependencies are satisfied (e.g., MRTM2 requires MRTM1's k2prime output).

    Args:
        subject: Subject ID (without 'sub-' prefix).
        session: Session ID (without 'ses-' prefix), or None.
        inputs: InputGroup with paths to input files.
        temps: Dict to store paths to temporary/intermediate files.
        workdir: Working directory for this subject/session.
        command_history: List to append (description, command) tuples.
        args: Parsed command-line arguments (includes km_method, tstar, etc.).

    Raises:
        RuntimeError: If kinetic modeling fails.
    """
    logger.info(f"Running kinetic modeling for {inputs.label}")

    # Execute methods in canonical order
    methods_to_run = [m for m in KM_METHOD_ORDER if m in args.km_method]
    logger.debug(f"Methods to run (in order): {methods_to_run}")

    for method in methods_to_run:
        logger.info(f"Running {method} for {inputs.label}")

        if method == "mrtm1":
            _run_mrtm1(subject, session, inputs, temps, workdir, command_history, args)
        elif method == "mrtm2":
            _run_mrtm2(subject, session, inputs, temps, workdir, command_history, args)
        elif method == "logan":
            _run_logan(subject, session, inputs, temps, workdir, command_history, args)
        elif method == "logan-ma1":
            _run_logan_ma1(subject, session, inputs, temps, workdir, command_history, args)


def _run_mrtm1(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """Run MRTM1 kinetic modeling."""
    # TODO: Implement MRTM1
    # - Uses reference regions from --mrtm1-ref
    # - Outputs k2prime for use by MRTM2
    logger.warning("MRTM1 not yet implemented")


def _run_mrtm2(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """Run MRTM2 kinetic modeling."""
    # TODO: Implement MRTM2
    # - Requires k2prime from MRTM1 (should be in temps dict)
    # - Uses high-binding regions from --mrtm2-hb
    if "k2prime" not in temps:
        raise RuntimeError("MRTM2 requires MRTM1 to run first (k2prime not found)")
    logger.warning("MRTM2 not yet implemented")


def _run_logan(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """Run Logan graphical analysis."""
    # TODO: Implement Logan
    # - Requires input function from bloodstream
    # - Uses --tstar parameter
    if not inputs.has_input_function():
        raise RuntimeError("Logan requires arterial input function")
    logger.warning("Logan not yet implemented")


def _run_logan_ma1(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """Run Logan MA1 graphical analysis."""
    # TODO: Implement Logan MA1
    # - Requires input function from bloodstream
    # - Uses --tstar parameter
    if not inputs.has_input_function():
        raise RuntimeError("Logan-MA1 requires arterial input function")
    logger.warning("Logan-MA1 not yet implemented")
