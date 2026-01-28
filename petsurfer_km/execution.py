"""Command execution utilities for petsurfer-km."""

import logging
import subprocess
from typing import NamedTuple

logger = logging.getLogger("petsurfer_km")


class CommandResult(NamedTuple):
    """Result of a command execution."""

    exit_code: int
    command: str
    stdout: str
    stderr: str


def run_command(cmd: list[str], description: str) -> CommandResult:
    """
    Execute a command and return the result.

    Args:
        cmd: Command and arguments as a list of strings.
        description: Human-readable description of what the command does.

    Returns:
        CommandResult with exit_code, command string, stdout, and stderr.
    """
    command_str = " ".join(cmd)
    logger.debug(f"Running: {description}")
    logger.debug(f"Command: {command_str}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    logger.debug(f"stdout: {result.stdout}")
    if result.returncode != 0:
        logger.debug(f"Command failed with exit code {result.returncode}")
        if result.stderr:
            logger.debug(f"stderr: {result.stderr}")

    return CommandResult(
        exit_code=result.returncode,
        command=command_str,
        stdout=result.stdout,
        stderr=result.stderr,
    )
