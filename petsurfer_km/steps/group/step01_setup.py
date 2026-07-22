"""Group analysis setup: BIDS layout, tracer inference, space/session resolution."""

from __future__ import annotations

import logging
import os
from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from pathlib import Path

import petsurfer_km
from bids import BIDSLayout

from petsurfer_km.bidsfsgd import BIDS_FSGD
from petsurfer_km.methods import MODEL_LABELS, MEAS_LABELS

logger = logging.getLogger("petsurfer_km")


@dataclass
class GroupContext:
    """Context for group-level analysis, computed during setup."""

    layout: BIDSLayout
    spaces: list[str]           # e.g. ["fsaverage-lh", "fsaverage-rh", "mni", "ROI"]
    sessions: list[str]         # e.g. ["baseline"] or ["test", "retest"]
    paired: bool
    tracer: str                 # inferred, e.g. "11CPS13"
    km_method: str              # single method, e.g. "logan-ma1"
    model: str                  # MODEL_LABELS[km_method], e.g. "MA1"
    meas: str                   # MEAS_LABELS[km_method], e.g. "VT"
    fsgd: BIDS_FSGD | None
    fsgd_file: Path | None


def run_group_setup(args: Namespace, workdir: Path, parser: ArgumentParser | None = None) -> GroupContext:
    """Build the GroupContext from CLI args and the BIDS layout.

    Args:
        args: Parsed CLI arguments.
        workdir: Working directory (unused for layout, reserved for future use).
        parser: Argument parser for ``parser.error()`` calls. If None, a
            ValueError is raised instead so the function is testable without
            a parser.
    """
    # --- 1. Create BIDSLayout from petsurfer_dir ---
    logger.info(f"Loading BIDS layout from {args.petsurfer_dir}")
    pkgdir = os.path.dirname(petsurfer_km.__file__)
    pskmconfig = os.path.join(pkgdir, "petsurfer-km-bids-config.json")
    layout = BIDSLayout(
        str(args.petsurfer_dir),
        validate=False,
        config=["bids", "derivatives", pskmconfig],
    )

    # --- 2. Infer tracer ---
    tracers = layout.get(target="tracer", return_type="id")
    # BIDSLayout may return a nested list; flatten
    if tracers and isinstance(tracers[0], list):
        tracers = [t for sub in tracers for t in sub]
    unique_tracers = list(dict.fromkeys(tracers))  # preserve order, deduplicate
    if len(unique_tracers) == 0:
        msg = "No tracer found in petsurfer-dir."
        if parser:
            parser.error(msg)
        raise ValueError(msg)
    if len(unique_tracers) > 1:
        msg = (
            f"Multiple tracers found: {unique_tracers}. Tracer inference is not "
            "yet supported; ensure participant-level data has a single tracer."
        )
        if parser:
            parser.error(msg)
        raise ValueError(msg)
    tracer = unique_tracers[0]
    logger.debug(f"Inferred tracer: {tracer}")

    # --- 3. Validate and map km-method ---
    if len(args.km_method) != 1:
        msg = f"Group analysis requires exactly one --km-method (got {len(args.km_method)})."
        if parser:
            parser.error(msg)
        raise ValueError(msg)
    km_method = args.km_method[0]
    model = MODEL_LABELS[km_method]
    meas = MEAS_LABELS[km_method]
    logger.debug(f"km_method={km_method} model={model} meas={meas}")

    # --- 4. Determine spaces ---
    spaces: list[str] = []
    if not args.no_vol:
        spaces.append("mni")
    if not args.no_surf:
        for hemi in args.hemispheres:
            spaces.append(f"fsaverage-{hemi}")
    if km_method != "suvr":
        spaces.append("ROI")
    if not spaces:
        msg = "No analysis spaces selected."
        if parser:
            parser.error(msg)
        raise ValueError(msg)
    logger.debug(f"Spaces: {spaces}")

    # --- 5. Determine sessions ---
    if args.paired is not None:
        sessions = list(args.paired)
        paired = True
    elif args.session_label is not None:
        if len(args.session_label) > 1:
            msg = (
                "Group analysis supports one session via --session-label or "
                "two via --paired."
            )
            if parser:
                parser.error(msg)
            raise ValueError(msg)
        sessions = list(args.session_label)
        paired = False
    else:
        sessions = ["baseline"]
        paired = False
    logger.debug(f"Sessions: {sessions} paired={paired}")

    # --- 6. Parse FSGD ---
    fsgd = None
    fsgd_file = None
    if args.fsgd is not None:
        fsgd = BIDS_FSGD(str(args.fsgd))
        fsgd_file = args.fsgd
        logger.info(f"Loaded FSGD: {len(fsgd.df)} subjects from {args.fsgd}")

    return GroupContext(
        layout=layout,
        spaces=spaces,
        sessions=sessions,
        paired=paired,
        tracer=tracer,
        km_method=km_method,
        model=model,
        meas=meas,
        fsgd=fsgd,
        fsgd_file=fsgd_file,
    )
