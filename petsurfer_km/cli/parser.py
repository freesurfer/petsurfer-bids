"""Argument parser for petsurfer-km CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from petsurfer_km import __version__
from petsurfer_km.methods import KM_METHOD_ORDER


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
            "Performs reference tissue modeling (MRTM1, MRTM2), Logan graphical "
            "analysis, and Patlak graphical analysis on PET data preprocessed "
            "with petprep."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  petsurfer-km /data/bids /data/output participant --km-method mrtm1
  petsurfer-km /data/bids /data/output participant --km-method mrtm1 mrtm2
  petsurfer-km /data/bids /data/output participant --km-method logan --tstar 30
  petsurfer-km /data/bids /data/output participant --km-method patlak --tstar 540
  petsurfer-km /data/bids /data/output participant --km-method suvr --suvr-frame 5 --ref-roi-label semiovale
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
        choices=KM_METHOD_ORDER,
        default=["mrtm1"],
        help=(
            "Kinetic modeling method(s) to run. Multiple methods can be specified. "
            "Methods are always executed in order: suvr, mrtm1, mrtm2, logan, "
            "logan-ma1, patlak. Note: mrtm2 requires mrtm1 output; specifying "
            "mrtm2 automatically includes mrtm1. SUVR is not strictly a kinetic "
            "model but produces voxel-/surface-wise SUVR maps using the same "
            "reference region. Default: mrtm1"
        ),
    )
    km_group.add_argument(
        "--tstar",
        type=positive_float,
        metavar="SECONDS",
        help=(
            "Time to equilibration (t*) in seconds for Logan and Patlak graphical "
            "analysis. Required when using logan, logan-ma1, or patlak methods."
        ),
    )
    km_group.add_argument(
        "--suvr-frame",
        type=int,
        metavar="INDEX",
        help=(
            "0-indexed frame number from the (smoothed) PET 4D used to compute "
            "SUVR. Required when --km-method includes suvr."
        ),
    )
    ref_roi_group = km_group.add_mutually_exclusive_group()
    ref_roi_group.add_argument(
        "--ref-roi", "--mrtm1-ref",
        dest="ref_roi",
        type=comma_separated_list,
        default=["Left-Cerebellum-Cortex", "Right-Cerebellum-Cortex"],
        metavar="REGIONS",
        help=(
            "Comma-separated list of reference regions, averaged from the GTM "
            "tacs.tsv. Used for MRTM1, MRTM2, and SUVR. "
            "Default: Left-Cerebellum-Cortex,Right-Cerebellum-Cortex"
        ),
    )
    ref_roi_group.add_argument(
        "--ref-roi-label", "--mrtm1-ref-label",
        dest="ref_roi_label",
        metavar="LABEL",
        help=(
            "BIDS label-<LABEL> entity of a petprep-emitted single-region "
            "reference TAC (e.g. 'semiovale' from "
            "`petprep --ref-mask-name semiovale`). The corresponding "
            "sub-<subj>[_ses-<sess>]_label-<LABEL>_desc-preproc_tacs.tsv "
            "in the petprep directory will be used as the reference TAC. "
            "Used for MRTM1, MRTM2, and SUVR. Mutually exclusive with --ref-roi."
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

    # Group analysis arguments
    group_group = parser.add_argument_group("Group Analysis")
    group_group.add_argument(
        "--petsurfer-dir",
        type=Path,
        metavar="PATH",
        help=(
            "Directory containing participant-level petsurfer-km outputs. "
            "Default: <bids_dir>/derivatives/petsurfer"
        ),
    )
    group_group.add_argument(
        "--fsgd",
        type=Path,
        metavar="PATH",
        help="FreeSurfer Group Descriptor file specifying subjects, classes, and covariates.",
    )
    group_group.add_argument(
        "--paired",
        nargs=2,
        metavar=("SES1", "SES2"),
        help="Paired longitudinal analysis: compute difference between two sessions.",
    )
    group_group.add_argument(
        "--cmc",
        nargs=5,
        metavar=("CFT", "NPERM", "SIGN", "NSPACES", "FWER"),
        help="Correction for multiple comparisons (voxel-wise only): "
             "cluster-forming threshold, n-permutations, sign (abs|pos|neg), "
             "n-spaces, family-wise error rate.",
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
