"""Main entry point for petsurfer-km CLI."""

from __future__ import annotations

# Use pysqlite3 as a drop-in replacement for sqlite3 if available.
# This is needed for FreeSurfer's fspython, which lacks the _sqlite3 C extension.
# pysqlite3-binary only has wheels for Linux x86-64
#
# Be sure to run `fspython -m pip install pysqlite3-binary` if running from
# inside fspython
try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

from petsurfer_km import __version__
from petsurfer_km.cli.parser import build_parser
from petsurfer_km.inputs import InputGroup, discover_inputs
from petsurfer_km.steps.participant import (
    run_bidsify,
    run_kinetic_modeling,
    run_preprocessing,
    run_report,
    run_surface,
    run_volumetric,
)
from petsurfer_km.steps.group import run_group_setup, run_group_analyze

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
    # Group-level validation
    if args.analysis_level == "group":
        if not args.petsurfer_dir.exists():
            parser.error(
                f"petsurfer-dir does not exist: {args.petsurfer_dir}\n"
                f"Participant-level analysis must be run first."
            )
        if len(args.km_method) != 1:
            parser.error("Group analysis requires exactly one --km-method.")
        if args.session_label is not None and args.paired is not None:
            parser.error("Cannot specify both --session-label and --paired.")
        if args.session_label is not None and len(args.session_label) > 1:
            parser.error(
                "Group analysis supports one session via --session-label or two via --paired."
            )
        if args.fsgd is not None and not args.fsgd.exists():
            parser.error(f"FSGD file not found: {args.fsgd}")
        if args.cmc is not None and args.cmc[2] not in ("abs", "pos", "neg"):
            parser.error(f"Invalid --cmc sign: {args.cmc[2]} (must be abs, pos, or neg).")
        return

    # Check that tstar is provided for invasive methods (Logan, Patlak)
    invasive_methods = {"logan", "logan-ma1", "patlak"}
    selected_invasive = invasive_methods.intersection(args.km_method)
    if selected_invasive and args.tstar is None:
        parser.error(
            f"--tstar is required when using {', '.join(sorted(selected_invasive))} method(s)"
        )

    # Cannot disable both volumetric and surface analysis
    if args.no_vol and args.no_surf:
        parser.error(
            "Cannot disable both volumetric (--no-vol) and surface (--no-surf) analysis"
        )

    # Check bloodstream-dir exists for invasive methods (use arterial input function)
    if selected_invasive:
        bloodstream_dir = args.bloodstream_dir
        if bloodstream_dir is None:
            bloodstream_dir = args.bids_dir / "derivatives" / "bloodstream"
        if not bloodstream_dir.exists():
            parser.error(
                f"bloodstream-dir does not exist: {bloodstream_dir}\n"
                f"Logan and Patlak methods require arterial input function data from bloodstream."
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
    # Set default petsurfer-dir
    if args.petsurfer_dir is None:
        args.petsurfer_dir = args.bids_dir / "derivatives" / "petsurfer"

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
    input_group: InputGroup,
    args: argparse.Namespace,
) -> None:
    """
    Process a single subject/session.

    Args:
        input_group: InputGroup with paths to input files.
        args: Parsed command-line arguments.

    Raises:
        RuntimeError: If processing fails.
    """
    logger.info(f"Processing: {input_group.label}")
    subject = input_group.subject
    session = input_group.session

    # Create subject working directory
    if session:
        subject_workdir = args.work_dir / f"sub-{subject}_ses-{session}"
    else:
        subject_workdir = args.work_dir / f"sub-{subject}"

    subject_workdir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Current working directory: {subject_workdir}")

    # Initialize temps dict for tracking intermediate files
    temps: dict[str, Path] = {}

    # Initialize command history for this subject
    command_history: list[tuple[str, str]] = []

    # Initialize file mappings (work-relative -> output-relative)
    file_mappings: list[tuple[str, str]] = []

    # Run processing steps
    run_preprocessing(subject, session, input_group, temps, subject_workdir, command_history)
    run_volumetric(subject, session, input_group, temps, subject_workdir, command_history, args)
    run_surface(subject, session, input_group, temps, subject_workdir, command_history, args)
    run_kinetic_modeling(subject, session, input_group, temps, subject_workdir, command_history, args)


    logger.debug(f"Temporary files at the end of the processing steps for {input_group.label}: {temps}")

    if command_history:
        logger.debug(f"Commands executed for {input_group.label}:")
        for description, command in command_history:
            logger.debug(f"  {description}: {command}")

    # Copy files to output directory and BIDSify
    run_bidsify(subject, session, input_group, temps, subject_workdir, command_history, args, file_mappings)

    # Generate per-subject HTML report
    run_report(subject, session, input_group, temps, subject_workdir, command_history, args, file_mappings)

def ensure_fsaverage() -> None:
    """
    Ensure fsaverage exists under $SUBJECTS_DIR.

    If fsaverage is not found under $SUBJECTS_DIR, copy it from
    $FREESURFER_HOME/subjects/fsaverage. This is needed because FreeSurfer
    commands like mris_fwhm and mri_glmfit resolve the fsaverage subject
    via $SUBJECTS_DIR, which may not contain fsaverage outside the container.

    Raises:
        RuntimeError: If SUBJECTS_DIR or FREESURFER_HOME are not set, or
            if fsaverage cannot be found or copied.
    """
    subjects_dir = os.environ.get("SUBJECTS_DIR")
    if not subjects_dir:
        raise RuntimeError(
            "SUBJECTS_DIR environment variable is not set. "
            "Please source your FreeSurfer environment."
        )

    fsaverage_dst = Path(subjects_dir) / "fsaverage"
    if fsaverage_dst.exists():
        logger.debug(f"fsaverage found at: {fsaverage_dst}")
        return

    freesurfer_home = os.environ.get("FREESURFER_HOME")
    if not freesurfer_home:
        raise RuntimeError(
            f"fsaverage not found in SUBJECTS_DIR ({subjects_dir}) and "
            "FREESURFER_HOME is not set. Cannot locate fsaverage."
        )

    fsaverage_src = Path(freesurfer_home) / "subjects" / "fsaverage"
    if not fsaverage_src.exists():
        raise RuntimeError(
            f"fsaverage not found in SUBJECTS_DIR ({subjects_dir}) or "
            f"FREESURFER_HOME/subjects ({fsaverage_src})."
        )

    logger.info(f"Copying fsaverage from {fsaverage_src} to {fsaverage_dst}")
    shutil.copytree(fsaverage_src, fsaverage_dst)
    logger.info("fsaverage copied successfully")


def run_group(args: argparse.Namespace) -> int:
    """Execute group-level analysis."""
    logger.info(f"petsurfer_km version: {__version__}")
    logger.info("Starting group-level analysis")
    logger.info(f"PetSurfer participant-level directory: {args.petsurfer_dir}")
    logger.info(f"Work directory: {args.work_dir}")

    args.work_dir.mkdir(parents=True, exist_ok=True)
    ensure_fsaverage()

    context = run_group_setup(args, args.work_dir, parser=build_parser())
    run_group_analyze(context, args, args.work_dir)

    logger.info(f"Group analysis complete. Outputs in: {args.work_dir}")

    # Cleanup work-dir unless --nocleanup. The work-dir is an intermediate
    # staging area; a future iteration will BIDSify its contents into
    # output_dir. Users must pass --nocleanup to inspect intermediate outputs.
    if args.nocleanup:
        logger.info(f"Skipping cleanup (--nocleanup). Work directory: {args.work_dir}")
    else:
        logger.info(f"Cleaning up working directory: {args.work_dir}")
        try:
            shutil.rmtree(args.work_dir)
        except Exception as e:
            logger.warning(f"Failed to clean up working directory: {e}")

    return 0


def run(args: argparse.Namespace) -> int:
    """
    Execute petsurfer-km processing.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    logger.info(f"petsurfer_km version: {__version__}")
    logger.info("Starting participant-level analysis")
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

    # Handle group-level analysis
    if args.analysis_level == "group":
        return run_group(args)

    # Determine if input function is required (for invasive methods: Logan, Patlak)
    invasive_methods = {"logan", "logan-ma1", "patlak"}
    require_input_function = bool(invasive_methods.intersection(args.km_method))

    # SUVR requires --suvr-frame
    if "suvr" in args.km_method and args.suvr_frame is None:
        logger.error("--suvr-frame is required when --km-method includes suvr")
        return 1

    # Discover input files
    input_groups = discover_inputs(
        petprep_dir=args.petprep_dir,
        bloodstream_dir=args.bloodstream_dir,
        participant_label=args.participant_label,
        session_label=args.session_label,
        require_input_function=require_input_function,
        pvc=args.pvc,
        bids_dir=args.bids_dir,
        ref_label=args.ref_roi_label,
    )

    if not input_groups:
        logger.error("No valid input groups found. Nothing to process.")
        return 1

    logger.info(f"Processing {len(input_groups)} subject/session combination(s)")

    # Create working directory
    args.work_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Created working directory: {args.work_dir}")

    # Ensure fsaverage is available under SUBJECTS_DIR
    ensure_fsaverage()

    # Track processing results
    failed_subjects: list[str] = []
    successful_subjects: list[str] = []

    # Process each subject/session
    for input_group in input_groups:
        try:
            process_subject(input_group, args)
            successful_subjects.append(input_group.label)
        except Exception as e:
            failed_subjects.append(input_group.label)
            logger.error(f"Failed to process {input_group.label}: {e}")
            if args.abort_on_error:
                logger.error("Aborting due to --abort-on-error")
                break

    # Log summary
    if successful_subjects:
        logger.info(f"Successfully processed: {', '.join(successful_subjects)}")
    if failed_subjects:
        logger.error(f"Failed to process: {', '.join(failed_subjects)}")

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
