"""Argument parser for petsurfer-km CLI."""

import argparse
from pathlib import Path

from petsurfer_km import __version__


def existing_path(value: str) -> Path:
    """Validate that a path exists."""
    path = Path(value)
    if not path.exists():
        raise argparse.ArgumentTypeError(f"Path does not exist: {value}")
    return path


def positive_float(value: str) -> float:
    """Validate positive float."""
    try:
        fvalue = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid number: {value}")
    if fvalue <= 0:
        raise argparse.ArgumentTypeError(f"Must be positive: {value}")
    return fvalue


def non_negative_float(value: str) -> float:
    """Validate non-negative float."""
    try:
        fvalue = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid number: {value}")
    if fvalue < 0:
        raise argparse.ArgumentTypeError(f"Must be non-negative: {value}")
    return fvalue


def comma_separated_list(value: str) -> list[str]:
    """Parse comma-separated string into list."""
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for petsurfer-km."""
    parser = argparse.ArgumentParser(
        prog="petsurfer-km",
        description=(
            "BIDS App for PET kinetic modeling using FreeSurfer's PetSurfer tools. "
            "Performs reference tissue modeling (MRTM1, MRTM2) and Logan graphical "
            "analysis on PET data preprocessed with petprep."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  petsurfer-km /data/bids /data/output participant --km-method mrtm1
  petsurfer-km /data/bids /data/output participant --km-method mrtm1 mrtm2
  petsurfer-km /data/bids /data/output participant --km-method logan --tstar 30
  petsurfer-km /data/bids /data/output participant --participant-label sub-01 sub-02
""",
    )

    # Positional arguments (BIDS App standard)
    parser.add_argument(
        "bids_dir",
        type=existing_path,
        help="Root directory of the BIDS dataset.",
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Output directory for results.",
    )
    parser.add_argument(
        "analysis_level",
        choices=["participant", "group"],
        help="Level of analysis: 'participant' or 'group'.",
    )

    # Kinetic modeling arguments
    km_group = parser.add_argument_group("Kinetic Modeling")
    km_group.add_argument(
        "--km-method",
        nargs="+",
        choices=["mrtm1", "mrtm2", "logan", "logan-ma1"],
        default=["mrtm1"],
        help=(
            "Kinetic modeling method(s) to run. Multiple methods can be specified. "
            "Methods are always executed in order: mrtm1, mrtm2, logan, logan-ma1. "
            "Note: mrtm2 requires mrtm1 output; specifying mrtm2 automatically "
            "includes mrtm1. Default: mrtm1"
        ),
    )
    km_group.add_argument(
        "--tstar",
        type=positive_float,
        metavar="SECONDS",
        help=(
            "Time to equilibration (t*) in seconds for Logan graphical analysis. "
            "Required when using logan or logan-ma1 methods."
        ),
    )
    km_group.add_argument(
        "--mrtm1-ref",
        type=comma_separated_list,
        default=["Left-Cerebellum-Cortex", "Right-Cerebellum-Cortex"],
        metavar="REGIONS",
        help=(
            "Comma-separated list of reference regions for MRTM1. "
            "Default: Left-Cerebellum-Cortex,Right-Cerebellum-Cortex"
        ),
    )
    km_group.add_argument(
        "--mrtm2-hb",
        type=comma_separated_list,
        default=["Left-Putamen", "Right-Putamen"],
        metavar="REGIONS",
        help=(
            "Comma-separated list of high-binding regions for MRTM2. "
            "Default: Left-Putamen,Right-Putamen"
        ),
    )

    # Input data arguments
    input_group = parser.add_argument_group("Input Data")
    input_group.add_argument(
        "--petprep-dir",
        type=Path,
        metavar="PATH",
        help=(
            "Directory containing petprep outputs. "
            "Default: <bids_dir>/derivatives/petprep"
        ),
    )
    input_group.add_argument(
        "--bloodstream-dir",
        type=Path,
        metavar="PATH",
        help=(
            "Directory containing bloodstream outputs. "
            "Default: <bids_dir>/derivatives/bloodstream"
        ),
    )

    # Filtering arguments
    filter_group = parser.add_argument_group("Filtering")
    filter_group.add_argument(
        "--participant-label",
        "--subject-label",
        nargs="+",
        dest="participant_label",
        metavar="LABEL",
        help=(
            "Space-separated list of participant labels to process "
            "(without 'sub-' prefix)."
        ),
    )
    filter_group.add_argument(
        "--session-label",
        nargs="+",
        metavar="LABEL",
        help=(
            "Space-separated list of session labels to process "
            "(without 'ses-' prefix)."
        ),
    )

    # PVC arguments
    pvc_group = parser.add_argument_group("Partial Volume Correction")
    pvc_group.add_argument(
        "--pvc",
        metavar="METHOD",
        help=(
            "Partial volume correction method. Affects which petprep output "
            "files are selected as input."
        ),
    )

    # Analysis space arguments
    space_group = parser.add_argument_group("Analysis Space")
    space_group.add_argument(
        "--no-vol",
        action="store_true",
        help="Skip volumetric analysis.",
    )
    space_group.add_argument(
        "--no-surf",
        action="store_true",
        help="Skip surface-based analysis.",
    )
    space_group.add_argument(
        "--lh",
        action="store_true",
        help="Process left hemisphere only (surface analysis).",
    )
    space_group.add_argument(
        "--rh",
        action="store_true",
        help="Process right hemisphere only (surface analysis).",
    )

    # Smoothing arguments
    smooth_group = parser.add_argument_group("Smoothing")
    smooth_group.add_argument(
        "--vol-fwhm",
        type=non_negative_float,
        default=6.0,
        metavar="MM",
        help="FWHM for volumetric smoothing in mm. Default: 6",
    )
    smooth_group.add_argument(
        "--surf-fwhm",
        type=non_negative_float,
        default=5.0,
        metavar="MM",
        help="FWHM for surface smoothing in mm. Default: 5",
    )

    # Processing arguments
    proc_group = parser.add_argument_group("Processing")
    proc_group.add_argument(
        "-w",
        "--work-dir",
        type=Path,
        metavar="PATH",
        help="Working directory for intermediate files. Default: /tmp/petsurfer-km-<pid>",
    )
    proc_group.add_argument(
        "--nocleanup",
        action="store_true",
        help="Do not delete temporary files after processing.",
    )
    proc_group.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete temporary files after processing (default behavior).",
    )
    proc_group.add_argument(
        "--no-freebrowse",
        action="store_true",
        help="Do not generate interactive freebrowse viewers for volumetric maps.",
    )
    proc_group.add_argument(
        "--abort-on-error",
        action="store_true",
        help="Abort processing if any subject fails. Default: log error and continue.",
    )
    proc_group.add_argument(
        "--log-level",
        choices=["error", "warn", "info", "debug"],
        default="warn",
        help="Logging verbosity level. Default: warn",
    )
    proc_group.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    return parser
