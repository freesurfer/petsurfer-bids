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
    extension: str
    meas: str | None
    stack: Path


def _space_params(space: str, context: GroupContext, workdir: Path) -> _SpaceParams:
    """Map an analysis space name to BIDS query parameters."""
    if space == "fsaverage-lh":
        return _SpaceParams(
            bids_space="fsaverage", hemi="L", suffix="mimap",
            extension=".nii.gz", meas=context.meas,
            stack=workdir / "fsaverage-lh.nii.gz",
        )
    if space == "fsaverage-rh":
        return _SpaceParams(
            bids_space="fsaverage", hemi="R", suffix="mimap",
            extension=".nii.gz", meas=context.meas,
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


def tsv2glmfit(
    tsvlist: list[str],
    outtable: str,
    participant_ids: list[str] | None = None,
) -> None:
    """Merge per-subject ROI TSV files into an aligned table for mri_glmfit.

    The TSV file for each subject is assumed to have (at least) two columns:
    the first column is the ROI name and the second is the value of interest.
    If *participant_ids* is passed, the subject name is placed as the first
    column.

    Subjects may have different ROI sets. Values are aligned by ROI name (not
    by row position) and missing ROIs are filled with NaN so every row has the
    same number of columns. ROIs not present in all subjects (any column
    containing NaN) are then pruned before the table is emitted.
    """
    if participant_ids is not None and len(tsvlist) != len(participant_ids):
        logger.error("tsv2glmfit: tsvlist length != subject list length")
        logger.error(f"  tsvlist length: {len(tsvlist)}")
        logger.error(f"  participant_ids length: {len(participant_ids)}")
        return

    # First pass: read all TSVs into per-subject dicts and collect
    # the union of ROI names in first-appearance order.
    subj_data: list[tuple[str, dict[str, str]]] = []
    all_roinames: list[str] = []
    seen_rois: set[str] = set()
    for k, tsvfile in enumerate(tsvlist):
        _, file_extension = os.path.splitext(tsvfile)
        if file_extension == ".csv":
            delimiter = ","
        if file_extension == ".tsv":
            delimiter = "\t"
        with open(tsvfile, "r") as fp:
            tsv = csv.reader(fp, delimiter=delimiter, quotechar='"')
            if participant_ids is not None:
                subj_id = participant_ids[k]
            else:
                subj_id = f"s{k}"
            roi_dict: dict[str, str] = {}
            for row in tsv:
                if not row or row[0] == "ROI":  # skip header row (issue #2)
                    continue
                roi_dict[row[0]] = row[1]
                if row[0] not in seen_rois:
                    seen_rois.add(row[0])
                    all_roinames.append(row[0])
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
                flist.append(flist0[0])
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
        else:
            tsv2glmfit(flist, str(params.stack), subjects)

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
        logger.info(f"GLM complete: {glmdir}")

        # 6. Run CMC (if requested and voxel-wise)
        if args.cmc is not None and space != "ROI":
            cmd = (
                f"mri_glmfit-sim --glmdir {glmdir} "
                f"--cwp {args.cmc[4]} "
                f"--perm {args.cmc[0]} {args.cmc[1]} {args.cmc[2]}"
            )
            nspaces = int(args.cmc[3])
            if nspaces > 1:
                cmd += f" --{nspaces}spaces"
            result = run_command(cmd.split(), f"CMC permutation {space}")
            if result.exit_code != 0:
                raise RuntimeError(f"CMC failed for {space}: {result.stderr}")
            logger.info(f"CMC complete: {glmdir}")
