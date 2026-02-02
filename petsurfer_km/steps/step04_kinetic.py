"""Kinetic modeling step for petsurfer-km."""

import logging
from argparse import Namespace
from pathlib import Path

from petsurfer_km.execution import run_command
from petsurfer_km.inputs import InputGroup

logger = logging.getLogger("petsurfer_km")

# Canonical order for kinetic modeling methods.
# MRTM2 must run after MRTM1 (depends on k2prime output).
# This order is enforced regardless of the order specified on command line.
KM_METHOD_ORDER = ["mrtm1", "mrtm2", "logan", "logan-ma1"]


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

    Methods are executed in canonical order (mrtm1, mrtm2, logan, logan-ma1)
    regardless of the order specified on the command line. This ensures
    dependencies are satisfied (e.g., MRTM2 requires MRTM1's k2prime output).

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

    # Extract reference region TAC if MRTM methods are requested
    mrtm_methods = {"mrtm1", "mrtm2"}
    if mrtm_methods.intersection(methods_to_run):
        _extract_reference_tac(inputs.tacs, workdir, temps, command_history, args.mrtm1_ref)

    # MRTM2 k2prime estimation (if MRTM2 requested)
    if "mrtm2" in methods_to_run:
        _extract_highbinding_tac(inputs.tacs, workdir, temps, command_history, args.mrtm2_hb)
        _compute_k2prime(temps, workdir, command_history)

    for method in methods_to_run:
        logger.info(f"Running {method} for {inputs.label}")

        if method == "mrtm1":
            _run_mrtm1(subject, session, inputs, temps, workdir, command_history, args)
        elif method == "mrtm2":
            _run_mrtm2(subject, session, inputs, temps, workdir, command_history, args)
        elif method == "logan":
            _run_logan(subject, session, inputs, temps, workdir, command_history, args)
        elif method == "logan-ma1":
            _run_logan_ma1(subject, session, inputs, temps, workdir, command_history, args)


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


def _run_logan_roi(
    method: str,
    aif: Path,
    tstar: float,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
) -> None:
    """
    Run Logan ROI-level fitting.

    Operation 7.1.

    Command: mri_glmfit --table <roi_tacs> --logan <aif> <frametime> <tstar> --o <output_dir> --nii.gz

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


def _run_logan_volume(
    method: str,
    aif: Path,
    tstar: float,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """
    Run Logan MNI volume fitting.

    Operation 7.2.

    Command: mri_glmfit --y <smoothed_vol> --logan <aif> <frametime> <tstar> --mask <mask> --o <output_dir> --nii.gz

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


def _run_logan_surface(
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
    Run Logan surface fitting for one hemisphere.

    Operation 7.3.

    Command: mri_glmfit --y <smoothed_surf> --surf fsaverage <hemi> --logan <aif> <frametime> <tstar> --o <output_dir> --nii.gz

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
    _run_logan_roi(
        method="logan",
        aif=aif,
        tstar=args.tstar,
        temps=temps,
        workdir=workdir,
        command_history=command_history,
    )

    # Operation 7.2: MNI volume fitting
    if not args.no_vol and inputs.has_volumetric():
        _run_logan_volume(
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
                _run_logan_surface(
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
    _run_logan_roi(
        method="logan-ma1",
        aif=aif,
        tstar=args.tstar,
        temps=temps,
        workdir=workdir,
        command_history=command_history,
    )

    # Operation 7.2: MNI volume fitting
    if not args.no_vol and inputs.has_volumetric():
        _run_logan_volume(
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
                _run_logan_surface(
                    method="logan-ma1",
                    hemi=hemi,
                    aif=aif,
                    tstar=args.tstar,
                    temps=temps,
                    workdir=workdir,
                    command_history=command_history,
                    args=args,
                )
