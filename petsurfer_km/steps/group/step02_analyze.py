"""Group analysis: per-space concatenation, GLM fitting, and CMC."""

from __future__ import annotations

import csv
import logging
import os
from argparse import Namespace
from pathlib import Path
from typing import NamedTuple

from petsurfer_km.execution import run_command
from petsurfer_km.steps.group.step01_setup import GroupContext

logger = logging.getLogger("petsurfer_km")


class _SpaceParams(NamedTuple):
    """BIDS query parameters for a given analysis space."""

    bids_space: str | None
    hemi: str | None
    suffix: str
    extension: str | list[str]
    meas: str | None
    stack: Path


# Participant surface maps may be GIFTI (default since the GIFTI change) or
# FreeSurfer 1D NIfTI (``--nifti-surfaces`` runs, or older outputs).  Both are
# accepted; GIFTI is preferred when a subject has both.
SURFACE_EXTENSIONS = [".func.gii", ".nii.gz"]


def _space_params(space: str, context: GroupContext, workdir: Path) -> _SpaceParams:
    """Map an analysis space name to BIDS query parameters."""
    if space == "fsaverage-lh":
        return _SpaceParams(
            bids_space="fsaverage", hemi="L", suffix="mimap",
            extension=SURFACE_EXTENSIONS, meas=context.meas,
            stack=workdir / "fsaverage-lh.nii.gz",
        )
    if space == "fsaverage-rh":
        return _SpaceParams(
            bids_space="fsaverage", hemi="R", suffix="mimap",
            extension=SURFACE_EXTENSIONS, meas=context.meas,
            stack=workdir / "fsaverage-rh.nii.gz",
        )
    if space == "mni":
        return _SpaceParams(
            bids_space="MNI152NLin2009cAsym", hemi=None, suffix="mimap",
            extension=".nii.gz", meas=context.meas,
            stack=workdir / "mni.nii.gz",
        )
    # ROI
    return _SpaceParams(
        bids_space=None, hemi=None, suffix="kinpar",
        extension=".tsv", meas=None,
        stack=workdir / "roi.csv",
    )


def _pick_input_file(files: list[str], extensions: str | list[str]) -> str:
    """Choose one per-subject input from a pybids match list.

    When several extensions are accepted, the first extension in *extensions*
    wins (GIFTI before NIfTI for surface spaces).  Falls back to the first
    match otherwise.
    """
    if isinstance(extensions, str):
        extensions = [extensions]
    for ext in extensions:
        for f in files:
            if f.endswith(ext):
                if len(files) > 1:
                    logger.debug(f"Several candidate files, using {ext}: {f}")
                return f
    return files[0]


def _is_numeric(value: str) -> bool:
    """Return True if *value* parses as a float (accepts 'NaN'/'nan' too)."""
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


# Row labels that are known FreeSurfer table artifacts, not ROIs, and
# must never be treated as predictor/measure columns (issue #28).
_NON_ROI_LABELS = {"roi", "frame_start", "frame_end", "frame"}


def _read_roi_dict(tsvfile: str) -> dict[str, str]:
    """Read one per-subject ROI TSV/CSV file into ``{roi_name: value}``.

    Skips header/non-ROI label rows (``roi``, ``frame_start``, ``frame_end``,
    ``frame``; see issue #28).
    """
    _, file_extension = os.path.splitext(tsvfile)
    delimiter = "," if file_extension == ".csv" else "\t"
    roi_dict: dict[str, str] = {}
    with open(tsvfile, "r") as fp:
        tsv = csv.reader(fp, delimiter=delimiter, quotechar='"')
        for row in tsv:
            if not row:
                continue
            if row[0].strip().lower() in _NON_ROI_LABELS:
                # skip header row (issue #2) and non-ROI labels (issue #28)
                continue
            roi_dict[row[0]] = row[1]
    return roi_dict


def tsv2glmfit(
    tsvlist: list[str],
    outtable: str,
    participant_ids: list[str] | None = None,
    paired: bool = False,
) -> None:
    """Merge per-subject ROI TSV files into an aligned table for mri_glmfit.

    The TSV file for each subject is assumed to have (at least) two columns:
    the first column is the ROI name and the second is the value of interest.
    If *participant_ids* is passed, the subject name is placed as the first
    column.

    If *paired* is True, *tsvlist* is expected to hold two files per subject
    (session1, session2), in the same order as *participant_ids*, i.e.
    ``[sub1-ses1, sub1-ses2, sub2-ses1, sub2-ses2, ...]``. The per-ROI
    session1 - session2 difference is computed per subject (mirroring
    ``mri_concat --paired-diff``'s "1-2, 3-4, ..." semantics used for the
    voxel/surface spaces) and one row per subject is written, rather than one
    row per session.

    Subjects may have different ROI sets. Values are aligned by ROI name (not
    by row position) and missing ROIs are filled with NaN so every row has the
    same number of columns. ROIs not present in all subjects (any column
    containing NaN) are then pruned before the table is emitted.

    Non-ROI rows such as ``frame_start``/``frame_end``/``Frame`` (FreeSurfer
    table labels that should never be treated as ROI predictors/measures, see
    issue #28) are skipped while reading. As a defense-in-depth guard, any
    remaining column whose values are not all numeric across every subject is
    also excluded (with a warning, not a fatal error) before the table is
    written, since mri_glmfit requires an all-numeric input table.
    """
    if paired:
        if participant_ids is None:
            logger.error("tsv2glmfit: paired=True requires participant_ids")
            return
        if len(tsvlist) != 2 * len(participant_ids):
            logger.error(
                "tsv2glmfit: paired mode expects 2 files (session1, session2) "
                "per subject"
            )
            logger.error(f"  tsvlist length: {len(tsvlist)}")
            logger.error(
                f"  participant_ids length: {len(participant_ids)} "
                f"(expected {len(tsvlist) // 2})"
            )
            return
    elif participant_ids is not None and len(tsvlist) != len(participant_ids):
        logger.error("tsv2glmfit: tsvlist length != subject list length")
        logger.error(f"  tsvlist length: {len(tsvlist)}")
        logger.error(f"  participant_ids length: {len(participant_ids)}")
        return

    # First pass: read all TSVs into per-subject dicts (differenced across
    # sessions when paired) and collect the union of ROI names in
    # first-appearance order.
    subj_data: list[tuple[str, dict[str, str]]] = []
    all_roinames: list[str] = []
    seen_rois: set[str] = set()

    if paired:
        for k, subj_id in enumerate(participant_ids):
            d1 = _read_roi_dict(tsvlist[2 * k])
            d2 = _read_roi_dict(tsvlist[2 * k + 1])
            diff_dict: dict[str, str] = {}
            for roi, val1 in d1.items():
                if roi not in d2:
                    continue
                try:
                    diff_dict[roi] = str(float(val1) - float(d2[roi]))
                except (TypeError, ValueError):
                    # Non-numeric value: carry the raw value through so the
                    # numeric guard below excludes it (with a warning)
                    # rather than diffing failing silently.
                    diff_dict[roi] = val1
                if roi not in seen_rois:
                    seen_rois.add(roi)
                    all_roinames.append(roi)
            subj_data.append((subj_id, diff_dict))
    else:
        for k, tsvfile in enumerate(tsvlist):
            subj_id = participant_ids[k] if participant_ids is not None else f"s{k}"
            roi_dict = _read_roi_dict(tsvfile)
            for roi in roi_dict:
                if roi not in seen_rois:
                    seen_rois.add(roi)
                    all_roinames.append(roi)
            subj_data.append((subj_id, roi_dict))

    # Second pass: build aligned table, NaN for missing ROIs.
    roitable: list[list[str]] = []
    for subj_id, roi_dict in subj_data:
        roivals = [subj_id] + [roi_dict.get(rn, "NaN") for rn in all_roinames]
        roitable.append(roivals)

    # Prune ROIs (columns) not present in all subjects: keep only the
    # intersection of ROI sets. Drop any ROI column containing a NaN.
    keep = [
        i for i in range(len(all_roinames))
        if all(row[i + 1] != "NaN" for row in roitable)
    ]

    # Guard: exclude any remaining column whose values are not all numeric
    # (e.g. a stray non-ROI label that slipped through, or a genuinely
    # non-numeric ROI value). This is a non-fatal safeguard: mri_glmfit
    # requires an all-numeric table, so we drop the offending column and
    # warn rather than let the GLM fit fail (issue #28).
    numeric_keep = []
    for i in keep:
        values = [row[i + 1] for row in roitable]
        if all(_is_numeric(v) for v in values):
            numeric_keep.append(i)
        else:
            bad = next(v for v in values if not _is_numeric(v))
            logger.warning(
                f"tsv2glmfit: excluding non-numeric ROI column "
                f"'{all_roinames[i]}' from group ROI table "
                f"(example value: {bad!r})"
            )
    keep = numeric_keep

    roinames = ["Subject"] + [all_roinames[i] for i in keep]
    roitable = [[row[0]] + [row[i + 1] for i in keep] for row in roitable]

    # Note: can't pass .tsv file to mri_glmfit because it thinks
    # it is a tac file. As a hack, have to call it csv but really
    # putting tabs as the separator. This is ugly.
    with open(outtable, mode="w") as fp:
        writer = csv.writer(fp, delimiter="\t")
        writer.writerow(roinames)
        writer.writerows(roitable)

    logger.debug(f"tsv2glmfit wrote {outtable} ({len(roitable)} subjects, {len(keep)} ROIs)")


def run_group_analyze(
    context: GroupContext,
    args: Namespace,
    workdir: Path,
    command_history: list[tuple[str, str]] | None = None,
) -> None:
    """Execute group-level analysis for each space in the context.

    For each space: discover subjects, gather per-subject files, concatenate
    into a stack, run mri_glmfit, and optionally run mri_glmfit-sim (CMC).
    """
    workdir.mkdir(parents=True, exist_ok=True)

    for space in context.spaces:
        logger.info(f"Analyzing space: {space}")

        # 1. Determine BIDS query parameters
        params = _space_params(space, context, workdir)

        # 2. Discover subjects
        if context.fsgd is not None:
            subjects = context.fsgd.df["subject_id"].tolist()
        else:
            subjects = context.layout.get(
                target="subject",
                session=context.sessions[0],
                datatype="pet",
                tracer=context.tracer,
                hemi=params.hemi,
                space=params.bids_space,
                model=context.model,
                meas=params.meas,
                suffix=params.suffix,
                extension=params.extension,
                return_type="id",
            )
            logger.info(f"Discovered {len(subjects)} subjects")

        # Apply --participant-label filter
        if args.participant_label is not None:
            subjects = [s for s in subjects if s in args.participant_label]

        if not subjects:
            logger.error(f"No subjects found for space {space}")
            raise RuntimeError(f"No subjects found for space {space}")

        # 3. Gather files
        flist: list[str] = []
        for sub in subjects:
            for ses in context.sessions:
                flist0 = context.layout.get(
                    subject=sub,
                    session=ses,
                    datatype="pet",
                    tracer=context.tracer,
                    hemi=params.hemi,
                    space=params.bids_space,
                    model=context.model,
                    meas=params.meas,
                    suffix=params.suffix,
                    extension=params.extension,
                    return_type="filename",
                )
                if not flist0:
                    logger.error(f"Cannot find file for {sub} {ses} in space {space}")
                    raise RuntimeError(
                        f"Cannot find file for {sub} {ses} in space {space}"
                    )
                flist.append(_pick_input_file(flist0, params.extension))
        logger.debug(f"Gathered {len(flist)} files for {space}")

        # 4. Concatenate
        if space != "ROI":
            flistflat = " ".join(flist)
            cmd = f"mri_concat --o {params.stack} {flistflat}"
            if context.paired:
                cmd += " --paired-diff"
            result = run_command(cmd.split(), f"Concatenate {space} stack")
            if result.exit_code != 0:
                raise RuntimeError(
                    f"Failed to concatenate {space}: {result.stderr}"
                )
            if command_history is not None:
                command_history.append((result.command, f"Concatenate {space} stack"))
        else:
            tsv2glmfit(flist, str(params.stack), subjects, paired=context.paired)

        # 5. Run GLM
        glmdir = workdir / f"glm.{space}"
        cmd = f"mri_glmfit --o {glmdir} --nii.gz"
        if space != "ROI":
            cmd += f" --y {params.stack} --eres-save"
        else:
            cmd += f" --table {params.stack}"
        if context.fsgd_file is None:
            cmd += " --osgm"
        else:
            cmd += f" --fsgd {context.fsgd_file}"
        if space == "fsaverage-lh":
            cmd += " --surf fsaverage lh"
        if space == "fsaverage-rh":
            cmd += " --surf fsaverage rh"
        result = run_command(cmd.split(), f"GLM fit {space}")
        if result.exit_code != 0:
            raise RuntimeError(f"GLM fit failed for {space}: {result.stderr}")
        if command_history is not None:
            command_history.append((result.command, f"GLM fit {space}"))
        logger.info(f"GLM complete: {glmdir}")

        # 6. Run CMC (if requested and voxel-wise)
        if args.cmc is not None and space != "ROI":
            cmd = (
                f"mri_glmfit-sim --glmdir {glmdir} "
                f"--cwp {args.cmc[4]} "
                f"--perm {args.cmc[1]} {args.cmc[0]} {args.cmc[2]}"
            )
            nspaces = int(args.cmc[3])
            if nspaces > 1:
                cmd += f" --{nspaces}spaces"
            result = run_command(cmd.split(), f"CMC permutation {space}")
            if result.exit_code != 0:
                raise RuntimeError(f"CMC failed for {space}: {result.stderr}")
            if command_history is not None:
                command_history.append((result.command, f"CMC permutation {space}"))
            logger.info(f"CMC complete: {glmdir}")
