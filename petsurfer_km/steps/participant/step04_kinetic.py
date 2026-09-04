"""Kinetic modeling step for petsurfer-km."""

from __future__ import annotations

import logging
from argparse import Namespace
from pathlib import Path

from petsurfer_km.execution import run_command
from petsurfer_km.inputs import InputGroup
from petsurfer_km.methods import KM_METHOD_ORDER

logger = logging.getLogger("petsurfer_km")


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

    Methods are executed in canonical order (mrtm1, mrtm2, logan, logan-ma1,
    patlak) regardless of the order specified on the command line. This
    ensures dependencies are satisfied (e.g., MRTM2 requires MRTM1's k2prime
    output).

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

    # Extract reference region TAC if any reference-region method is requested
    ref_methods = {"suvr", "mrtm1", "mrtm2"}
    if ref_methods.intersection(methods_to_run):
        if args.ref_roi_label:
            if inputs.ref_tacs is None:
                raise RuntimeError(
                    f"--ref-roi-label '{args.ref_roi_label}' specified but "
                    f"label TAC file not found in petprep dir for {inputs.label}"
                )
            _extract_reference_tac(
                inputs.ref_tacs, workdir, temps, command_history,
                [args.ref_roi_label],
            )
        else:
            _extract_reference_tac(
                inputs.tacs, workdir, temps, command_history, args.ref_roi,
            )

    # MRTM2 k2prime estimation (if MRTM2 requested)
    if "mrtm2" in methods_to_run:
        _extract_highbinding_tac(inputs.tacs, workdir, temps, command_history, args.mrtm2_hb)
        _compute_k2prime(temps, workdir, command_history)

    for method in methods_to_run:
        logger.info(f"Running {method} for {inputs.label}")

        if method == "suvr":
            _run_suvr(subject, session, inputs, temps, workdir, command_history, args)
        elif method == "mrtm1":
            _run_mrtm1(subject, session, inputs, temps, workdir, command_history, args)
        elif method == "mrtm2":
            _run_mrtm2(subject, session, inputs, temps, workdir, command_history, args)
        elif method == "logan":
            _run_logan(subject, session, inputs, temps, workdir, command_history, args)
        elif method == "logan-ma1":
            _run_logan_ma1(subject, session, inputs, temps, workdir, command_history, args)
        elif method == "patlak":
            _run_patlak(subject, session, inputs, temps, workdir, command_history, args)


def _extract_reference_tac(
    tacs_file: Path,
    workdir: Path,
    temps: dict[str, Path],
    command_history: list[tuple[str, str]],
    ref_regions: list[str],
) -> None:
    """
    Extract and average TAC for reference region(s).

    Command: tsv2petsurfer --tsv <tac_file> --roiavg <ref_regions> --o <output>
    Output: ref-tac-petsurfer.dat

    Adds to temps:
        ref_tac: Path to ref-tac-petsurfer.dat
    """
    output_file = workdir / "ref-tac-petsurfer.dat"

    cmd = [
        "tsv2petsurfer",
        "--tsv", str(tacs_file),
        "--roiavg", *ref_regions,
        "--o", str(output_file),
    ]

    result = run_command(cmd, f"Extract reference TAC ({', '.join(ref_regions)})")
    command_history.append((result.command, "Extract reference region TAC"))

    if result.exit_code != 0:
        raise RuntimeError(
            f"Failed to extract reference TAC: {result.stderr}"
        )

    if not output_file.exists():
        raise RuntimeError(
            f"Reference TAC file not created: {output_file}"
        )

    temps["ref_tac"] = output_file
    logger.debug(f"Reference TAC extracted to: {output_file}")


def _extract_highbinding_tac(
    tacs_file: Path,
    workdir: Path,
    temps: dict[str, Path],
    command_history: list[tuple[str, str]],
    hb_regions: list[str],
) -> None:
    """
    Extract and average TAC for high-binding region(s).

    Command: tsv2petsurfer --tsv <tac_file> --roiavg <hb_regions> --hb --o <output>
    Output: hb-tac-petsurfer.dat

    The --hb flag adds frame numbers and "HighBind" header for mri_glmfit.

    Adds to temps:
        hb_tac: Path to hb-tac-petsurfer.dat
    """
    output_file = workdir / "hb-tac-petsurfer.dat"

    cmd = [
        "tsv2petsurfer",
        "--tsv", str(tacs_file),
        "--roiavg", *hb_regions,
        "--hb",
        "--o", str(output_file),
    ]

    result = run_command(cmd, f"Extract high-binding TAC ({', '.join(hb_regions)})")
    command_history.append((result.command, "Extract high-binding region TAC"))

    if result.exit_code != 0:
        raise RuntimeError(
            f"Failed to extract high-binding TAC: {result.stderr}"
        )

    if not output_file.exists():
        raise RuntimeError(
            f"High-binding TAC file not created: {output_file}"
        )

    temps["hb_tac"] = output_file
    logger.debug(f"High-binding TAC extracted to: {output_file}")


def _compute_k2prime(
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
) -> None:
    """
    Compute k2prime via MRTM1 on high-binding region.

    Command: mri_glmfit --table <hb_tac> --mrtm1 <ref_tac> <frametime> --o <output_dir> --nii.gz
    Output: mrtm1.hb/k2prime.dat

    Adds to temps:
        mrtm1_hb_dir: Path to mrtm1.hb/ output directory
        k2prime: Path to mrtm1.hb/k2prime.dat
    """
    output_dir = workdir / "mrtm1.hb"

    cmd = [
        "mri_glmfit",
        "--table", str(temps["hb_tac"]),
        "--mrtm1", str(temps["ref_tac"]), str(temps["frametime"]),
        "--o", str(output_dir),
        "--nii.gz",
    ]

    result = run_command(cmd, "Compute k2prime via MRTM1 on high-binding region")
    command_history.append((result.command, "Compute k2prime for MRTM2"))

    if result.exit_code != 0:
        raise RuntimeError(
            f"Failed to compute k2prime: {result.stderr}"
        )

    k2prime_file = output_dir / "k2prime.dat"
    if not k2prime_file.exists():
        raise RuntimeError(
            f"k2prime file not created: {k2prime_file}"
        )

    temps["mrtm1_hb_dir"] = output_dir
    temps["k2prime"] = k2prime_file
    logger.debug(f"k2prime computed: {k2prime_file}")


def _run_mrtm1(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """
    Run MRTM1 kinetic modeling.

    Implements:
    - Operation 6.1: ROI-level fitting
    - Operation 6.3: MNI volume fitting (if not --no-vol)
    - Operation 6.4: Surface fitting (if not --no-surf)

    Adds to temps:
        mrtm1_roi_dir: Path to mrtm1.roi/ output directory
        mrtm1_mni_dir: Path to mrtm1.mni.sm<NN>/ output directory (if volumetric)
        mrtm1_surf_<hemi>_dir: Path to mrtm1.fsaverage.<hemi>.sm<NN>/ (if surface)
    """
    # Operation 6.1: ROI-level fitting
    _run_mrtm_roi(
        method="mrtm1",
        temps=temps,
        workdir=workdir,
        command_history=command_history,
        k2prime=None,  # Not needed for MRTM1
    )

    # Operation 6.3: MNI volume fitting
    if not args.no_vol and inputs.has_volumetric():
        _run_mrtm_volume(
            method="mrtm1",
            temps=temps,
            workdir=workdir,
            command_history=command_history,
            args=args,
            k2prime=None,
        )

    # Operation 6.4: Surface fitting
    if not args.no_surf and inputs.has_surface():
        for hemi in args.hemispheres:
            if inputs.has_surface(hemi):
                _run_mrtm_surface(
                    method="mrtm1",
                    hemi=hemi,
                    temps=temps,
                    workdir=workdir,
                    command_history=command_history,
                    args=args,
                    k2prime=None,
                )


def _run_mrtm2(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """
    Run MRTM2 kinetic modeling.

    Implements:
    - Operation 6.2: ROI-level fitting
    - Operation 6.3: MNI volume fitting (if not --no-vol)
    - Operation 6.4: Surface fitting (if not --no-surf)

    Requires k2prime from Phase 5 (MRTM1 on high-binding region).

    Adds to temps:
        mrtm2_roi_dir: Path to mrtm2.roi/ output directory
        mrtm2_mni_dir: Path to mrtm2.mni.sm<NN>/ output directory (if volumetric)
        mrtm2_surf_<hemi>_dir: Path to mrtm2.fsaverage.<hemi>.sm<NN>/ (if surface)
    """
    if "k2prime" not in temps:
        raise RuntimeError("MRTM2 requires k2prime (run MRTM1 first or check MRTM2 setup)")

    k2prime = temps["k2prime"]

    # Operation 6.2: ROI-level fitting
    _run_mrtm_roi(
        method="mrtm2",
        temps=temps,
        workdir=workdir,
        command_history=command_history,
        k2prime=k2prime,
    )

    # Operation 6.3: MNI volume fitting
    if not args.no_vol and inputs.has_volumetric():
        _run_mrtm_volume(
            method="mrtm2",
            temps=temps,
            workdir=workdir,
            command_history=command_history,
            args=args,
            k2prime=k2prime,
        )

    # Operation 6.4: Surface fitting
    if not args.no_surf and inputs.has_surface():
        for hemi in args.hemispheres:
            if inputs.has_surface(hemi):
                _run_mrtm_surface(
                    method="mrtm2",
                    hemi=hemi,
                    temps=temps,
                    workdir=workdir,
                    command_history=command_history,
                    args=args,
                    k2prime=k2prime,
                )


def _read_k2prime(k2prime_file: Path) -> str:
    """
    Read k2prime value from file.

    The k2prime.dat file contains a single numeric value.
    mri_glmfit --mrtm2 expects the k2prime as a numeric value, not a file path.

    Args:
        k2prime_file: Path to k2prime.dat file.

    Returns:
        String representation of the k2prime value.
    """
    with open(k2prime_file) as f:
        return f.read().strip()


def _run_mrtm_roi(
    method: str,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    k2prime: Path | None,
) -> None:
    """
    Run MRTM ROI-level fitting.

    Operation 6.1 (MRTM1) or 6.2 (MRTM2).

    Command: mri_glmfit --table <roi_tacs> --mrtm1/2 <ref_tac> <frametime> [<k2prime_value>] --o <output_dir> --nii.gz

    Note: For MRTM2, the k2prime is passed as a numeric value (not file path).

    Adds to temps:
        <method>_roi_dir: Path to <method>.roi/ output directory
    """
    output_dir = workdir / f"{method}.roi"

    cmd = [
        "mri_glmfit",
        "--table", str(temps["roi_tacs"]),
        f"--{method}", str(temps["ref_tac"]), str(temps["frametime"]),
    ]

    # MRTM2 requires k2prime value (not file path)
    if method == "mrtm2" and k2prime is not None:
        k2prime_value = _read_k2prime(k2prime)
        cmd.append(k2prime_value)

    cmd.extend(["--o", str(output_dir), "--nii.gz"])

    description = f"{method.upper()} ROI-level fitting"
    result = run_command(cmd, description)
    command_history.append((result.command, description))

    if result.exit_code != 0:
        raise RuntimeError(f"Failed {method.upper()} ROI fitting: {result.stderr}")

    if not output_dir.exists():
        raise RuntimeError(f"{method.upper()} ROI output not created: {output_dir}")

    temps[f"{method}_roi_dir"] = output_dir
    logger.debug(f"{method.upper()} ROI fitting complete: {output_dir}")


def _run_mrtm_volume(
    method: str,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
    k2prime: Path | None,
) -> None:
    """
    Run MRTM MNI volume fitting.

    Operation 6.3.

    Command: mri_glmfit --y <smoothed_vol> --mrtm1/2 <ref_tac> <frametime> [<k2prime>] --mask <mask> --o <output_dir> --nii.gz

    Adds to temps:
        <method>_mni_dir: Path to <method>.mni.sm<NN>/ output directory
    """
    fwhm_str = f"{int(args.vol_fwhm):02d}"
    output_dir = workdir / f"{method}.mni.sm{fwhm_str}"

    cmd = [
        "mri_glmfit",
        "--y", str(temps["mni_smoothed"]),
        f"--{method}", str(temps["ref_tac"]), str(temps["frametime"]),
    ]

    # MRTM2 requires k2prime value (not file path)
    if method == "mrtm2" and k2prime is not None:
        k2prime_value = _read_k2prime(k2prime)
        cmd.append(k2prime_value)

    cmd.extend([
        "--mask", str(temps["mni_mask"]),
        "--o", str(output_dir),
        "--nii.gz",
    ])

    description = f"{method.upper()} MNI volume fitting (FWHM={args.vol_fwhm}mm)"
    result = run_command(cmd, description)
    command_history.append((result.command, description))

    if result.exit_code != 0:
        raise RuntimeError(f"Failed {method.upper()} MNI volume fitting: {result.stderr}")

    if not output_dir.exists():
        raise RuntimeError(f"{method.upper()} MNI output not created: {output_dir}")

    temps[f"{method}_mni_dir"] = output_dir
    logger.debug(f"{method.upper()} MNI volume fitting complete: {output_dir}")


def _run_mrtm_surface(
    method: str,
    hemi: str,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
    k2prime: Path | None,
) -> None:
    """
    Run MRTM surface fitting for one hemisphere.

    Operation 6.4.

    Command: mri_glmfit --y <smoothed_surf> --surf fsaverage <hemi> --mrtm1/2 <ref_tac> <frametime> [<k2prime>] --cortex --o <output_dir> --nii.gz

    Adds to temps:
        <method>_surf_<hemi>_dir: Path to <method>.fsaverage.<hemi>.sm<NN>/ output directory
    """
    fwhm_str = f"{int(args.surf_fwhm):02d}"
    output_dir = workdir / f"{method}.fsaverage.{hemi}.sm{fwhm_str}"

    cmd = [
        "mri_glmfit",
        "--y", str(temps[f"surf_smoothed_{hemi}"]),
        "--surf", "fsaverage", hemi,
        f"--{method}", str(temps["ref_tac"]), str(temps["frametime"]),
    ]

    # MRTM2 requires k2prime value (not file path)
    if method == "mrtm2" and k2prime is not None:
        k2prime_value = _read_k2prime(k2prime)
        cmd.append(k2prime_value)

    cmd.extend([
        "--cortex",
        "--o", str(output_dir),
        "--nii.gz",
    ])

    description = f"{method.upper()} {hemi} surface fitting (FWHM={args.surf_fwhm}mm)"
    result = run_command(cmd, description)
    command_history.append((result.command, description))

    if result.exit_code != 0:
        raise RuntimeError(f"Failed {method.upper()} {hemi} surface fitting: {result.stderr}")

    if not output_dir.exists():
        raise RuntimeError(f"{method.upper()} {hemi} surface output not created: {output_dir}")

    temps[f"{method}_surf_{hemi}_dir"] = output_dir
    logger.debug(f"{method.upper()} {hemi} surface fitting complete: {output_dir}")


def _read_frame_times(tacs_file: Path) -> tuple[list[float], list[float]]:
    """
    Read the ``frame_start`` / ``frame_end`` columns from a petprep tacs.tsv.

    These are the only source of frame timing in petsurfer-km; the PET JSON
    sidecar is never consulted.

    Returns:
        (starts, ends) in seconds, one entry per frame.
    """
    with open(tacs_file) as f:
        header = f.readline().rstrip("\n").split("\t")
        try:
            fs_idx = header.index("frame_start")
            fe_idx = header.index("frame_end")
        except ValueError:
            raise RuntimeError(
                f"Cannot find frame_start/frame_end columns in {tacs_file}"
            )
        starts: list[float] = []
        ends: list[float] = []
        for line in f:
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            starts.append(float(fields[fs_idx]))
            ends.append(float(fields[fe_idx]))

    return starts, ends


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    """Weighted mean of ``values``; ``weights`` is assumed to sum to 1."""
    return sum(v * w for v, w in zip(values, weights))


def _write_suvr_frame_weights(
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> list[float]:
    """
    Build the frame-duration weight vector defining the SUVR window.

    The weight of frame ``i`` is its duration divided by the total duration of
    the requested window, and 0 for frames outside the window, so the weights
    sum to 1 and a weighted sum over frames is the duration-weighted mean.
    Taking the ratio of two such means is equivalent to the AUC(t1..t2) ratio
    computed by Turku's dftratio / imgratio, since the total duration cancels.

    A single ``--suvr-frame`` yields a weight of exactly 1.0 on that frame, so
    the result is identical to plain single-frame selection.

    Output: suvr-frame-weights.dat, one float per frame, one per line. This is
    the format mri_concat --w expects (plain numbers, no header, no trailing
    blank line), and mri_concat errors out if its length does not match the
    frame count of the 4D PET.

    Adds to temps:
        suvr_weights: Path to suvr-frame-weights.dat
        suvr_time_window: (start, end) of the window in seconds

    Returns:
        The weight vector.
    """
    frames = sorted(args.suvr_frame)
    starts, ends = _read_frame_times(inputs.tacs)
    durations = [e - s for s, e in zip(starts, ends)]
    nframes = len(durations)

    out_of_range = [f for f in frames if f >= nframes]
    if out_of_range:
        raise RuntimeError(
            f"--suvr-frame {out_of_range} out of range; "
            f"{inputs.tacs.name} has {nframes} frames (valid 0..{nframes - 1})"
        )

    total_duration = sum(durations[f] for f in frames)
    if total_duration <= 0:
        raise RuntimeError(
            f"--suvr-frame {frames} selects frames with a total duration of "
            f"{total_duration}s; cannot compute a weighted average"
        )

    weights = [0.0] * nframes
    for f in frames:
        weights[f] = durations[f] / total_duration

    output_file = workdir / "suvr-frame-weights.dat"
    output_file.write_text("\n".join(f"{w:.10f}" for w in weights) + "\n")

    time_window = (starts[frames[0]], ends[frames[-1]])
    temps["suvr_weights"] = output_file
    temps["suvr_time_window"] = time_window

    description = (
        f"Compute SUVR frame-duration weights for frame(s) "
        f"{', '.join(str(f) for f in frames)} (t = {time_window[0]:g}"
        f"\u2013{time_window[1]:g} s)"
    )
    command_history.append((f"# {description} -> {output_file}", description))
    logger.debug(f"SUVR frame weights written to: {output_file}")

    return weights


def _extract_ref_value_over_frames(
    temps: dict[str, Path],
    weights: list[float],
) -> float:
    """
    Compute the scalar reference value: the weighted average of the reference
    TAC over the SUVR window.

    The petsurfer ref-tac file is one float per line, no header (see
    tsv2petsurfer.py with --roiavg). Stores the value in ``temps["suvr_ref_value"]``.
    """
    ref_tac_file = temps["ref_tac"]
    with open(ref_tac_file) as f:
        values = [float(line.strip()) for line in f if line.strip()]

    if len(values) != len(weights):
        raise RuntimeError(
            f"Reference TAC {ref_tac_file.name} has {len(values)} frames but the "
            f"PET TAC table has {len(weights)}; cannot compute the SUVR reference"
        )

    value = _weighted_mean(values, weights)
    if value == 0:
        raise RuntimeError(
            f"Reference value over the SUVR window is zero ({ref_tac_file}); "
            "cannot compute SUVR"
        )

    temps["suvr_ref_value"] = value
    logger.debug(f"Reference value over SUVR window: {value}")
    return value


def _run_suvr(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """
    Compute SUVR as the frame-duration-weighted average of the PET signal over
    the frames selected by --suvr-frame, divided by the same weighted average
    of the reference TAC.

    ROI values come from the petprep ROI TACs, the same source every other
    method's ROI results are derived from. The volume and surface inputs are
    the already-smoothed PET produced by step02/step03, so --vol-fwhm /
    --surf-fwhm are honored.
    """
    weights = _write_suvr_frame_weights(inputs, temps, workdir, command_history, args)
    ref_value = _extract_ref_value_over_frames(temps, weights)

    _run_suvr_roi(temps, workdir, command_history, weights, ref_value)

    if not args.no_vol and inputs.has_volumetric():
        _run_suvr_volume(temps, workdir, command_history, args, ref_value)

    if not args.no_surf and inputs.has_surface():
        for hemi in args.hemispheres:
            if inputs.has_surface(hemi):
                _run_suvr_surface(hemi, temps, workdir, command_history, args, ref_value)


def _run_suvr_roi(
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    weights: list[float],
    ref_value: float,
) -> None:
    """
    SUVR ROI-level values.

    The counterpart of _run_mrtm_roi / _run_invasive_roi, but SUVR needs no
    fit: each ROI TAC in roi-tacs-petsurfer.dat is averaged over the SUVR
    window and divided by the reference value.

    Output: suvr.roi/suvr.dat, two whitespace-separated columns (ROI, SUVR),
    the same shape as logan.roi/vt.dat.

    Adds to temps:
        suvr_roi_dir: Path to suvr.roi/ output directory
    """
    output_dir = workdir / "suvr.roi"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "suvr.dat"

    roi_tacs = temps["roi_tacs"]
    with open(roi_tacs) as f:
        # tsv2petsurfer --all writes a trailing tab on every row
        header = [c for c in f.readline().rstrip("\n").split("\t") if c]
        rows = [
            [float(v) for v in line.rstrip("\n").split("\t") if v]
            for line in f
            if line.strip()
        ]

    if len(rows) != len(weights):
        raise RuntimeError(
            f"ROI TAC table {roi_tacs.name} has {len(rows)} frames but the PET "
            f"TAC table has {len(weights)}; cannot compute ROI SUVR"
        )

    # First column is frame_start (see tsv2petsurfer --all), not an ROI
    lines = []
    for col, roi in enumerate(header[1:], start=1):
        tac = [row[col] for row in rows]
        lines.append(f"{roi:<34s}{_weighted_mean(tac, weights) / ref_value:.5f}")

    output_file.write_text("\n".join(lines) + "\n")

    description = "SUVR ROI-level values (weighted frame average / reference)"
    command_history.append((f"# {description} -> {output_file}", description))

    temps["suvr_roi_dir"] = output_dir
    logger.debug(f"SUVR ROI values written to: {output_file}")


def _suvr_frame_label(args: Namespace) -> str:
    """Human-readable frame list for command descriptions."""
    return ", ".join(str(f) for f in sorted(args.suvr_frame))


def _run_suvr_volume(
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
    ref_value: float,
) -> None:
    """
    SUVR volumetric pipeline:
      1. mri_concat --i <smoothed_4d> --w <weights> --sum --o <frames_wmean>
      2. fscalc <frames_wmean> div <ref_value> --o <suvr_unmasked>
      3. mri_mask <suvr_unmasked> <mni_mask> <suvr.nii.gz>

    Adds to temps:
        suvr_mni_dir: Path to suvr.mni.sm<NN>/ output directory
    """
    fwhm_str = f"{int(args.vol_fwhm):02d}"
    output_dir = workdir / f"suvr.mni.sm{fwhm_str}"
    output_dir.mkdir(parents=True, exist_ok=True)

    frames_wmean = output_dir / "frames.wmean.nii.gz"
    suvr_unmasked = output_dir / "suvr_unmasked.nii.gz"
    suvr = output_dir / "suvr.nii.gz"

    # Step 1: weighted average over the SUVR window
    cmd = [
        "mri_concat",
        "--i", str(temps["mni_smoothed"]),
        "--w", str(temps["suvr_weights"]),
        "--sum",
        "--o", str(frames_wmean),
    ]
    description = (
        f"Weighted average of frame(s) {_suvr_frame_label(args)} "
        f"from MNI volume for SUVR"
    )
    result = run_command(cmd, description)
    command_history.append((result.command, description))
    if result.exit_code != 0 or not frames_wmean.exists():
        raise RuntimeError(f"Failed to average SUVR frames: {result.stderr}")

    # Step 2: divide by reference value
    cmd = [
        "fscalc",
        str(frames_wmean), "div", str(ref_value),
        "--o", str(suvr_unmasked),
    ]
    description = f"Divide MNI weighted average by reference value ({ref_value:g}) for SUVR"
    result = run_command(cmd, description)
    command_history.append((result.command, description))
    if result.exit_code != 0 or not suvr_unmasked.exists():
        raise RuntimeError(f"Failed to compute SUVR (fscalc div): {result.stderr}")

    # Step 3: mask
    cmd = [
        "mri_mask",
        str(suvr_unmasked),
        str(temps["mni_mask"]),
        str(suvr),
    ]
    description = "Mask SUVR map with MNI brain mask"
    result = run_command(cmd, description)
    command_history.append((result.command, description))
    if result.exit_code != 0 or not suvr.exists():
        raise RuntimeError(f"Failed to mask SUVR map: {result.stderr}")

    temps["suvr_mni_dir"] = output_dir
    logger.debug(f"SUVR MNI volume complete: {output_dir}")


def _run_suvr_surface(
    hemi: str,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
    ref_value: float,
) -> None:
    """
    SUVR surface pipeline (no masking step):
      1. mri_concat --i <smoothed_surf> --w <weights> --sum --o <frames_wmean>
      2. fscalc <frames_wmean> div <ref_value> --o <suvr.nii.gz>

    Adds to temps:
        suvr_surf_<hemi>_dir: Path to suvr.fsaverage.<hemi>.sm<NN>/ output directory
    """
    fwhm_str = f"{int(args.surf_fwhm):02d}"
    output_dir = workdir / f"suvr.fsaverage.{hemi}.sm{fwhm_str}"
    output_dir.mkdir(parents=True, exist_ok=True)

    frames_wmean = output_dir / "frames.wmean.nii.gz"
    suvr = output_dir / "suvr.nii.gz"

    cmd = [
        "mri_concat",
        "--i", str(temps[f"surf_smoothed_{hemi}"]),
        "--w", str(temps["suvr_weights"]),
        "--sum",
        "--o", str(frames_wmean),
    ]
    description = (
        f"Weighted average of frame(s) {_suvr_frame_label(args)} "
        f"from {hemi} surface for SUVR"
    )
    result = run_command(cmd, description)
    command_history.append((result.command, description))
    if result.exit_code != 0 or not frames_wmean.exists():
        raise RuntimeError(
            f"Failed to average SUVR {hemi} surface frames: {result.stderr}"
        )

    cmd = [
        "fscalc",
        str(frames_wmean), "div", str(ref_value),
        "--o", str(suvr),
    ]
    description = (
        f"Divide {hemi} surface weighted average by reference value "
        f"({ref_value:g}) for SUVR"
    )
    result = run_command(cmd, description)
    command_history.append((result.command, description))
    if result.exit_code != 0 or not suvr.exists():
        raise RuntimeError(f"Failed to compute SUVR on {hemi} surface: {result.stderr}")

    temps[f"suvr_surf_{hemi}_dir"] = output_dir
    logger.debug(f"SUVR {hemi} surface complete: {output_dir}")


def _run_invasive_roi(
    method: str,
    aif: Path,
    tstar: float,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
) -> None:
    """
    Run an invasive (AIF-based) graphical-analysis ROI-level fit.

    Used by Logan, Logan-MA1, and Patlak. ``method`` is passed straight to
    mri_glmfit as ``--<method>``; all three accept the same
    ``<aif> <frametime> <tstar>`` argument signature.

    Operation 7.1.

    Command: mri_glmfit --table <roi_tacs> --<method> <aif> <frametime> <tstar> --o <output_dir> --nii.gz

    Adds to temps:
        <method>_roi_dir: Path to <method>.roi/ output directory
    """
    output_dir = workdir / f"{method}.roi"

    cmd = [
        "mri_glmfit",
        "--table", str(temps["roi_tacs"]),
        f"--{method}", str(aif), str(temps["frametime"]), str(tstar),
        "--o", str(output_dir),
        "--nii.gz",
    ]

    description = f"{method.upper()} ROI-level fitting (tstar={tstar})"
    result = run_command(cmd, description)
    command_history.append((result.command, description))

    if result.exit_code != 0:
        raise RuntimeError(f"Failed {method.upper()} ROI fitting: {result.stderr}")

    if not output_dir.exists():
        raise RuntimeError(f"{method.upper()} ROI output not created: {output_dir}")

    temps[f"{method}_roi_dir"] = output_dir
    logger.debug(f"{method.upper()} ROI fitting complete: {output_dir}")


def _run_invasive_volume(
    method: str,
    aif: Path,
    tstar: float,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """
    Run an invasive (AIF-based) graphical-analysis MNI volume fit.

    Used by Logan, Logan-MA1, and Patlak.

    Operation 7.2.

    Command: mri_glmfit --y <smoothed_vol> --<method> <aif> <frametime> <tstar> --mask <mask> --o <output_dir> --nii.gz

    Adds to temps:
        <method>_mni_dir: Path to <method>.mni.sm<NN>/ output directory
    """
    fwhm_str = f"{int(args.vol_fwhm):02d}"
    output_dir = workdir / f"{method}.mni.sm{fwhm_str}"

    cmd = [
        "mri_glmfit",
        "--y", str(temps["mni_smoothed"]),
        f"--{method}", str(aif), str(temps["frametime"]), str(tstar),
        "--mask", str(temps["mni_mask"]),
        "--o", str(output_dir),
        "--nii.gz",
    ]

    description = f"{method.upper()} MNI volume fitting (FWHM={args.vol_fwhm}mm, tstar={tstar})"
    result = run_command(cmd, description)
    command_history.append((result.command, description))

    if result.exit_code != 0:
        raise RuntimeError(f"Failed {method.upper()} MNI volume fitting: {result.stderr}")

    if not output_dir.exists():
        raise RuntimeError(f"{method.upper()} MNI output not created: {output_dir}")

    temps[f"{method}_mni_dir"] = output_dir
    logger.debug(f"{method.upper()} MNI volume fitting complete: {output_dir}")


def _run_invasive_surface(
    method: str,
    hemi: str,
    aif: Path,
    tstar: float,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """
    Run an invasive (AIF-based) graphical-analysis surface fit for one hemisphere.

    Used by Logan, Logan-MA1, and Patlak.

    Operation 7.3.

    Command: mri_glmfit --y <smoothed_surf> --surf fsaverage <hemi> --<method> <aif> <frametime> <tstar> --o <output_dir> --nii.gz

    Adds to temps:
        <method>_surf_<hemi>_dir: Path to <method>.fsaverage.<hemi>.sm<NN>/ output directory
    """
    fwhm_str = f"{int(args.surf_fwhm):02d}"
    output_dir = workdir / f"{method}.fsaverage.{hemi}.sm{fwhm_str}"

    cmd = [
        "mri_glmfit",
        "--y", str(temps[f"surf_smoothed_{hemi}"]),
        "--surf", "fsaverage", hemi,
        f"--{method}", str(aif), str(temps["frametime"]), str(tstar),
        "--o", str(output_dir),
        "--nii.gz",
    ]

    description = f"{method.upper()} {hemi} surface fitting (FWHM={args.surf_fwhm}mm, tstar={tstar})"
    result = run_command(cmd, description)
    command_history.append((result.command, description))

    if result.exit_code != 0:
        raise RuntimeError(f"Failed {method.upper()} {hemi} surface fitting: {result.stderr}")

    if not output_dir.exists():
        raise RuntimeError(f"{method.upper()} {hemi} surface output not created: {output_dir}")

    temps[f"{method}_surf_{hemi}_dir"] = output_dir
    logger.debug(f"{method.upper()} {hemi} surface fitting complete: {output_dir}")


def _run_logan(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """
    Run Logan graphical analysis.

    Implements:
    - Operation 7.1: ROI-level fitting
    - Operation 7.2: MNI volume fitting (if not --no-vol)
    - Operation 7.3: Surface fitting (if not --no-surf)

    Requires arterial input function from bloodstream.

    Adds to temps:
        logan_roi_dir: Path to logan.roi/ output directory
        logan_mni_dir: Path to logan.mni.sm<NN>/ output directory (if volumetric)
        logan_surf_<hemi>_dir: Path to logan.fsaverage.<hemi>.sm<NN>/ (if surface)
    """
    if not inputs.has_input_function():
        raise RuntimeError("Logan requires arterial input function")

    aif = inputs.input_function

    # Operation 7.1: ROI-level fitting
    _run_invasive_roi(
        method="logan",
        aif=aif,
        tstar=args.tstar,
        temps=temps,
        workdir=workdir,
        command_history=command_history,
    )

    # Operation 7.2: MNI volume fitting
    if not args.no_vol and inputs.has_volumetric():
        _run_invasive_volume(
            method="logan",
            aif=aif,
            tstar=args.tstar,
            temps=temps,
            workdir=workdir,
            command_history=command_history,
            args=args,
        )

    # Operation 7.3: Surface fitting
    if not args.no_surf and inputs.has_surface():
        for hemi in args.hemispheres:
            if inputs.has_surface(hemi):
                _run_invasive_surface(
                    method="logan",
                    hemi=hemi,
                    aif=aif,
                    tstar=args.tstar,
                    temps=temps,
                    workdir=workdir,
                    command_history=command_history,
                    args=args,
                )


def _run_logan_ma1(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """
    Run Logan MA1 graphical analysis.

    Same as Logan but uses the MA1 (Ichise) variant.

    Implements:
    - Operation 7.1: ROI-level fitting
    - Operation 7.2: MNI volume fitting (if not --no-vol)
    - Operation 7.3: Surface fitting (if not --no-surf)

    Requires arterial input function from bloodstream.

    Adds to temps:
        logan-ma1_roi_dir: Path to logan-ma1.roi/ output directory
        logan-ma1_mni_dir: Path to logan-ma1.mni.sm<NN>/ output directory (if volumetric)
        logan-ma1_surf_<hemi>_dir: Path to logan-ma1.fsaverage.<hemi>.sm<NN>/ (if surface)
    """
    if not inputs.has_input_function():
        raise RuntimeError("Logan-MA1 requires arterial input function")

    aif = inputs.input_function

    # Operation 7.1: ROI-level fitting
    _run_invasive_roi(
        method="logan-ma1",
        aif=aif,
        tstar=args.tstar,
        temps=temps,
        workdir=workdir,
        command_history=command_history,
    )

    # Operation 7.2: MNI volume fitting
    if not args.no_vol and inputs.has_volumetric():
        _run_invasive_volume(
            method="logan-ma1",
            aif=aif,
            tstar=args.tstar,
            temps=temps,
            workdir=workdir,
            command_history=command_history,
            args=args,
        )

    # Operation 7.3: Surface fitting
    if not args.no_surf and inputs.has_surface():
        for hemi in args.hemispheres:
            if inputs.has_surface(hemi):
                _run_invasive_surface(
                    method="logan-ma1",
                    hemi=hemi,
                    aif=aif,
                    tstar=args.tstar,
                    temps=temps,
                    workdir=workdir,
                    command_history=command_history,
                    args=args,
                )


def _run_patlak(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """
    Run Patlak graphical analysis.

    Implements:
    - Operation 7.1: ROI-level fitting
    - Operation 7.2: MNI volume fitting (if not --no-vol)
    - Operation 7.3: Surface fitting (if not --no-surf)

    Requires arterial input function from bloodstream. mri_glmfit's --patlak
    accepts the same ``<aif> <frametime> <tstar>`` signature as --logan, so
    the same generic invasive-fit helpers are reused.

    Adds to temps:
        patlak_roi_dir: Path to patlak.roi/ output directory
        patlak_mni_dir: Path to patlak.mni.sm<NN>/ output directory (if volumetric)
        patlak_surf_<hemi>_dir: Path to patlak.fsaverage.<hemi>.sm<NN>/ (if surface)
    """
    if not inputs.has_input_function():
        raise RuntimeError("Patlak requires arterial input function")

    aif = inputs.input_function

    # Operation 7.1: ROI-level fitting
    _run_invasive_roi(
        method="patlak",
        aif=aif,
        tstar=args.tstar,
        temps=temps,
        workdir=workdir,
        command_history=command_history,
    )

    # Operation 7.2: MNI volume fitting
    if not args.no_vol and inputs.has_volumetric():
        _run_invasive_volume(
            method="patlak",
            aif=aif,
            tstar=args.tstar,
            temps=temps,
            workdir=workdir,
            command_history=command_history,
            args=args,
        )

    # Operation 7.3: Surface fitting
    if not args.no_surf and inputs.has_surface():
        for hemi in args.hemispheres:
            if inputs.has_surface(hemi):
                _run_invasive_surface(
                    method="patlak",
                    hemi=hemi,
                    aif=aif,
                    tstar=args.tstar,
                    temps=temps,
                    workdir=workdir,
                    command_history=command_history,
                    args=args,
                )
