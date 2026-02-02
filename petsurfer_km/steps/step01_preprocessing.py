"""Preprocessing step for petsurfer-km (TAC extraction)."""

import logging
from pathlib import Path

from petsurfer_km.execution import run_command
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
    Run preprocessing steps (TAC extraction).

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

    # Check that TACs file exists
    if inputs.tacs is None:
        raise RuntimeError(f"No TACs file found for {inputs.label}")

    # Extract frame times
    _extract_frame_times(inputs.tacs, workdir, temps, command_history)

    # Extract all ROI TACs
    _extract_roi_tacs(inputs.tacs, workdir, temps, command_history)


def _extract_frame_times(
    tacs_file: Path,
    workdir: Path,
    temps: dict[str, Path],
    command_history: list[tuple[str, str]],
) -> None:
    """
    Extract frame times from TAC file.

    Command: tsv2petsurfer --tsv <tac_file> --frametime --o <output>
    Output: frametime-petsurfer.dat

    Adds to temps:
        frametime: Path to frametime-petsurfer.dat
    """
    output_file = workdir / "frametime-petsurfer.dat"

    cmd = [
        "tsv2petsurfer",
        "--tsv", str(tacs_file),
        "--frametime",
        "--o", str(output_file),
    ]

    result = run_command(cmd, "Extract frame times from TAC file")
    command_history.append((result.command, "Extract frame times from TAC file"))

    if result.exit_code != 0:
        raise RuntimeError(
            f"Failed to extract frame times: {result.stderr}"
        )

    if not output_file.exists():
        raise RuntimeError(
            f"Frame times file not created: {output_file}"
        )

    temps["frametime"] = output_file
    logger.debug(f"Frame times extracted to: {output_file}")


def _extract_roi_tacs(
    tacs_file: Path,
    workdir: Path,
    temps: dict[str, Path],
    command_history: list[tuple[str, str]],
) -> None:
    """
    Extract all ROI TACs from TAC file.

    Command: tsv2petsurfer --tsv <tac_file> --all --o <output>
    Output: roi-tacs-petsurfer.dat

    Excludes non-brain structures: frame_end, Left-vessel, Right-vessel,
    CSF-ExtraCerebral, Head-ExtraCerebral, AirCavity, Optic-Chiasm,
    3rd-Ventricle, 4th-Ventricle

    Adds to temps:
        roi_tacs: Path to roi-tacs-petsurfer.dat
    """
    output_file = workdir / "roi-tacs-petsurfer.dat"

    cmd = [
        "tsv2petsurfer",
        "--tsv", str(tacs_file),
        "--all",
        "--o", str(output_file),
    ]

    result = run_command(cmd, "Extract ROI TACs")
    command_history.append((result.command, "Extract ROI TACs"))

    if result.exit_code != 0:
        raise RuntimeError(
            f"Failed to extract ROI TACs: {result.stderr}"
        )

    if not output_file.exists():
        raise RuntimeError(
            f"ROI TACs file not created: {output_file}"
        )

    temps["roi_tacs"] = output_file
    logger.debug(f"ROI TACs extracted to: {output_file}")
