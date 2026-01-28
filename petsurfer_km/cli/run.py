"""Main entry point for petsurfer-km CLI."""

import argparse
import logging
import shutil
import sys
from pathlib import Path

from petsurfer_km import __version__
from petsurfer_km.cli.parser import build_parser
from petsurfer_km.inputs import InputGroup, discover_inputs
from petsurfer_km.steps import (
    run_kinetic_modeling,
    run_preprocessing,
    run_surface,
    run_volumetric,
)

logger = logging.getLogger("petsurfer_km")


def setup_logging(level: str) -> None:
    """
    Configure logging for petsurfer-km.

    Args:
        level: Log level string ('error', 'warn', 'info', or 'debug')
    """
    level_map = {
        "error": logging.ERROR,
        "warn": logging.WARNING,
        "info": logging.INFO,
        "debug": logging.DEBUG,
    }
    log_level = level_map.get(level, logging.WARNING)

    # Configure format based on level
    if log_level == logging.DEBUG:
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    else:
        fmt = "%(levelname)s: %(message)s"

    logging.basicConfig(
        level=log_level,
        format=fmt,
        handlers=[logging.StreamHandler(sys.stderr)],
    )


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """
    Validate parsed arguments for consistency.

    Args:
        args: Parsed arguments namespace.
        parser: ArgumentParser instance for error reporting.
    """
    # Check that tstar is provided for Logan methods
    logan_methods = {"logan", "logan-ma1"}
    selected_logan = logan_methods.intersection(args.km_method)
    if selected_logan and args.tstar is None:
        parser.error(
            f"--tstar is required when using {', '.join(sorted(selected_logan))} method(s)"
        )

    # Cannot disable both volumetric and surface analysis
    if args.no_vol and args.no_surf:
        parser.error(
            "Cannot disable both volumetric (--no-vol) and surface (--no-surf) analysis"
        )

    # Check bloodstream-dir exists for Logan methods (uses arterial input function)
    if selected_logan:
        bloodstream_dir = args.bloodstream_dir
        if bloodstream_dir is None:
            bloodstream_dir = args.bids_dir / "derivatives" / "bloodstream"
        if not bloodstream_dir.exists():
            parser.error(
                f"bloodstream-dir does not exist: {bloodstream_dir}\n"
                f"Logan methods require arterial input function data from bloodstream."
            )

    # Check petprep-dir exists
    petprep_dir = args.petprep_dir
    if petprep_dir is None:
        petprep_dir = args.bids_dir / "derivatives" / "petprep"
    if not petprep_dir.exists():
        parser.error(
            f"petprep-dir does not exist: {petprep_dir}\n"
            f"Please run petprep first or specify --petprep-dir."
        )


def set_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Set default values that depend on other arguments."""
    import os

    # Set default petprep-dir
    if args.petprep_dir is None:
        args.petprep_dir = args.bids_dir / "derivatives" / "petprep"

    # Set default bloodstream-dir
    if args.bloodstream_dir is None:
        args.bloodstream_dir = args.bids_dir / "derivatives" / "bloodstream"

    # Set default work-dir
    if args.work_dir is None:
        args.work_dir = Path(f"/tmp/petsurfer-km-{os.getpid()}")

    # If mrtm2 is selected, ensure mrtm1 is also included (mrtm2 depends on mrtm1 output)
    if "mrtm2" in args.km_method and "mrtm1" not in args.km_method:
        logger.debug("Adding mrtm1 (required by mrtm2)")
        args.km_method = ["mrtm1"] + args.km_method

    # Handle hemisphere selection
    if not args.lh and not args.rh:
        # Default: process both hemispheres
        args.hemispheres = ["lh", "rh"]
    else:
        args.hemispheres = []
        if args.lh:
            args.hemispheres.append("lh")
        if args.rh:
            args.hemispheres.append("rh")

    return args


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    Parse and validate command-line arguments.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:])

    Returns:
        Validated namespace of arguments.

    Raises:
        SystemExit: On parsing or validation errors.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    args = set_defaults(args)
    validate_args(args, parser)
    return args


def process_subject(
    group: InputGroup,
    args: argparse.Namespace,
    command_history: list[tuple[str, str]],
) -> None:
    """
    Process a single subject/session.

    Args:
        group: InputGroup with paths to input files.
        args: Parsed command-line arguments.
        command_history: List to append (description, command) tuples.

    Raises:
        RuntimeError: If processing fails.
    """
    subject = group.subject
    session = group.session

    # Create subject working directory
    if session:
        subject_workdir = args.work_dir / f"sub-{subject}_ses-{session}"
    else:
        subject_workdir = args.work_dir / f"sub-{subject}"

    subject_workdir.mkdir(parents=True, exist_ok=True)
    logger.debug(f"Working directory: {subject_workdir}")

    # Initialize temps dict for tracking intermediate files
    temps: dict[str, Path] = {}

    # Run processing steps
    run_preprocessing(subject, session, group, temps, subject_workdir, command_history)
    run_volumetric(subject, session, group, temps, subject_workdir, command_history, args)
    run_surface(subject, session, group, temps, subject_workdir, command_history, args)
    run_kinetic_modeling(subject, session, group, temps, subject_workdir, command_history, args)


def run(args: argparse.Namespace) -> int:
    """
    Execute petsurfer-km processing.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    logger.info(f"petsurfer_km version: {__version__}")
    logger.debug(f"BIDS directory: {args.bids_dir}")
    logger.debug(f"Output directory: {args.output_dir}")
    logger.debug(f"Analysis level: {args.analysis_level}")
    logger.debug(f"Kinetic methods: {args.km_method}")
    logger.debug(f"PetPrep directory: {args.petprep_dir}")
    logger.debug(f"Bloodstream directory: {args.bloodstream_dir}")
    logger.debug(f"Work directory: {args.work_dir}")
    if args.participant_label:
        logger.debug(f"Participants: {args.participant_label}")
    if args.session_label:
        logger.debug(f"Sessions: {args.session_label}")
    logger.debug(f"Volumetric FWHM: {args.vol_fwhm} mm")
    logger.debug(f"Surface FWHM: {args.surf_fwhm} mm")
    logger.debug(f"Hemispheres: {args.hemispheres}")
    logger.debug(f"Skip volumetric: {args.no_vol}")
    logger.debug(f"Skip surface: {args.no_surf}")

    # Handle group-level analysis (not yet implemented)
    if args.analysis_level == "group":
        logger.error("Group-level analysis is not yet implemented")
        return 1

    # Determine if input function is required (for Logan methods)
    logan_methods = {"logan", "logan-ma1"}
    require_input_function = bool(logan_methods.intersection(args.km_method))

    # Discover input files
    input_groups = discover_inputs(
        petprep_dir=args.petprep_dir,
        bloodstream_dir=args.bloodstream_dir,
        participant_label=args.participant_label,
        session_label=args.session_label,
        require_input_function=require_input_function,
        pvc=args.pvc,
    )

    if not input_groups:
        logger.error("No valid input groups found. Nothing to process.")
        return 1

    logger.info(f"Processing {len(input_groups)} subject/session combination(s)")

    # Create working directory
    args.work_dir.mkdir(parents=True, exist_ok=True)
    logger.debug(f"Created working directory: {args.work_dir}")

    # Track command history and processing results
    command_history: list[tuple[str, str]] = []
    failed_subjects: list[str] = []
    successful_subjects: list[str] = []

    # Process each subject/session
    for group in input_groups:
        try:
            process_subject(group, args, command_history)
            successful_subjects.append(group.label)
        except Exception as e:
            failed_subjects.append(group.label)
            logger.error(f"Failed to process {group.label}: {e}")
            if args.abort_on_error:
                logger.error("Aborting due to --abort-on-error")
                break

    # Log summary
    if successful_subjects:
        logger.info(f"Successfully processed: {', '.join(successful_subjects)}")
    if failed_subjects:
        logger.error(f"Failed to process: {', '.join(failed_subjects)}")

    # Log command history
    if command_history:
        logger.debug("Commands executed:")
        for description, command in command_history:
            logger.debug(f"  {description}: {command}")

    # Cleanup
    if args.nocleanup:
        logger.info(f"Skipping cleanup (--nocleanup). Working directory: {args.work_dir}")
    else:
        logger.info(f"Cleaning up working directory: {args.work_dir}")
        try:
            shutil.rmtree(args.work_dir)
        except Exception as e:
            logger.warning(f"Failed to clean up working directory: {e}")

    # Return exit code
    if failed_subjects:
        return 1
    return 0


def main(argv: list[str] | None = None) -> None:
    """
    Main entry point for petsurfer-km CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:])
    """
    args = parse_args(argv)
    setup_logging(args.log_level)
    sys.exit(run(args))


if __name__ == "__main__":
    main()
