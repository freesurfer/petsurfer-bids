"""Per-subject HTML report for petsurfer-km.

Generates a Bootstrap 5 HTML report summarising kinetic-modelling results
for one subject/session, including:

- Summary of analysis parameters
- Parametric maps (volumetric MNI152 + surface fsaverage) per method
- ROI-level parameter tables per method
- About / provenance section

Visualisations are created with nilearn and saved as SVG in
``<output_dir>/sub-XX/figures/``.
"""

import importlib.util
import json
import logging
import shutil
import sys
from argparse import Namespace
from html import escape
from pathlib import Path

from petsurfer_km import __version__
from petsurfer_km.inputs import InputGroup

logger = logging.getLogger("petsurfer_km")

# ---------------------------------------------------------------------------
# Method metadata (shared with step05_bidsify)
# ---------------------------------------------------------------------------

MODEL_LABELS = {
    "mrtm1": "MRTM1",
    "mrtm2": "MRTM2",
    "logan": "Logan",
    "logan-ma1": "MA1",
}

MEAS_LABELS = {
    "mrtm1": "BPND",
    "mrtm2": "BPND",
    "logan": "VT",
    "logan-ma1": "VT",
}

HEMI_BIDS = {"lh": "L", "rh": "R"}

# ---------------------------------------------------------------------------
# Freebrowse helpers
# ---------------------------------------------------------------------------

_FREEBROWSE_DIR = Path(__file__).resolve().parent.parent / "freebrowse"

# Module-level caches (populated on first use)
_freebrowse_html_cache: str | None = None
_nvd_create_mod = None
_nvd_embed_mod = None


def _import_module(name: str, path: Path):
    """Import a Python module from an arbitrary file path (handles hyphens)."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get_freebrowse_modules():
    """Return (nvd_create_mod, nvd_embed_mod), importing on first call."""
    global _nvd_create_mod, _nvd_embed_mod
    if _nvd_create_mod is None:
        _nvd_create_mod = _import_module("nvd_create", _FREEBROWSE_DIR / "nvd-create.py")
    if _nvd_embed_mod is None:
        _nvd_embed_mod = _import_module("nvd_embed", _FREEBROWSE_DIR / "nvd-embed.py")
    return _nvd_create_mod, _nvd_embed_mod


def _get_freebrowse_html() -> str:
    """Return the freebrowse HTML content, reading from disk on first call."""
    global _freebrowse_html_cache
    if _freebrowse_html_cache is None:
        html_path = _FREEBROWSE_DIR / "freebrowse-2.2.1.html"
        _freebrowse_html_cache = html_path.read_text(encoding="utf-8")
    return _freebrowse_html_cache


def _generate_freebrowse_viewer(
    bids_mimap: Path,
    template_path: Path,
    vlim: tuple[float, float],
    output_html: Path,
    workdir: Path,
) -> None:
    """Generate a self-contained freebrowse HTML viewer for a volumetric map.

    Args:
        bids_mimap: Path to the BIDS output mimap.nii.gz.
        template_path: Path to the MNI152 T1w template NIfTI.
        vlim: (vmin, vmax) display limits for the overlay colormap.
        output_html: Where to write the final self-contained HTML.
        workdir: Working directory for temporary files.
    """
    nvd_create_mod, nvd_embed_mod = _get_freebrowse_modules()

    # Load and adjust the NVD template
    nvd_template_path = _FREEBROWSE_DIR / "templates" / "petsurfer-km-template.nvd"
    with open(nvd_template_path) as f:
        nvd_template = json.load(f)

    # Set overlay name and colour limits from the robust vlim
    nvd_template["imageOptionsArray"][1]["name"] = bids_mimap.name
    nvd_template["imageOptionsArray"][1]["cal_min"] = vlim[0]
    nvd_template["imageOptionsArray"][1]["cal_max"] = vlim[1]

    # Write adjusted template to workdir
    adjusted_template = workdir / "freebrowse-template.nvd"
    with open(adjusted_template, "w") as f:
        json.dump(nvd_template, f, indent=2)

    # Create NVD document with embedded image data
    nvd = nvd_create_mod.create_nvd(
        [str(template_path), str(bids_mimap)],
        template_path=str(adjusted_template),
    )
    nvd_json = json.dumps(nvd)

    # Embed into freebrowse HTML
    html_content = _get_freebrowse_html()
    output_content = nvd_embed_mod.embed_nvd(html_content, nvd_json)

    # Write final self-contained HTML
    output_html.parent.mkdir(parents=True, exist_ok=True)
    with open(output_html, "w", encoding="utf-8") as f:
        f.write(output_content)

    logger.debug(f"Freebrowse viewer saved: {output_html}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_report(
    subject: str,
    session: str | None,
    inputs: InputGroup,
    temps: dict[str, Path],
    workdir: Path,
    command_history: list[tuple[str, str]],
    args: Namespace,
) -> None:
    """Generate an HTML report for a single subject/session.

    Args:
        subject: Subject ID (without 'sub-' prefix).
        session: Session ID (without 'ses-' prefix), or None.
        inputs: InputGroup with paths and tracer info.
        temps: Dict of intermediate file paths from earlier steps.
        workdir: Working directory for this subject/session.
        command_history: List of (command, description) tuples.
        args: Parsed command-line arguments.
    """
    label = f"sub-{subject}" + (f"_ses-{session}" if session else "")
    logger.info(f"Generating report for {label}")

    output_dir = args.output_dir
    figures_dir = _make_figures_dir(output_dir, subject, session)

    # Build BIDS-style prefix for figure filenames
    prefix = _build_prefix(inputs)

    # Relative path from the HTML file to the figures directory
    fig_rel_dir = f"sub-{subject}/figures" if not session else f"sub-{subject}/ses-{session}/figures"

    # Fetch MNI template (once, with graceful fallback)
    template_path = _fetch_mni_template()
    template_warning = template_path is None

    # Copy templates into sourcedata/ (idempotent)
    _ensure_sourcedata(output_dir, template_path)

    # Generate figures and collect paths (relative to output_dir)
    figure_sections: list[str] = []
    methods_run = [m for m in ["mrtm1", "mrtm2", "logan", "logan-ma1"] if m in args.km_method]

    for method in methods_run:
        model = MODEL_LABELS[method]
        meas = MEAS_LABELS[method]
        map_file = "bp.nii.gz" if meas == "BPND" else "vt.nii.gz"

        method_figures: list[str] = []

        # --- Volumetric figure ---
        vol_key = f"{method}_mni_dir"
        if vol_key in temps:
            vol_map = temps[vol_key] / map_file
            if vol_map.exists():
                fig_name = (
                    f"{prefix}_model-{model}_meas-{meas}"
                    f"_space-MNI152NLin2009cAsym_mimap.svg"
                )
                fig_path = figures_dir / fig_name
                _generate_volume_figure(vol_map, template_path, fig_path, meas)
                rel = f"{fig_rel_dir}/{fig_name}"
                vol_html = (
                    f'<h5>Volumetric (MNI152)</h5>\n'
                    f'<img src="./{escape(rel)}" class="img-fluid mb-3" '
                    f'alt="{model} {meas} MNI152">'
                )

                # --- Freebrowse viewer ---
                if not getattr(args, "no_freebrowse", False) and template_path is not None:
                    try:
                        # Construct BIDS output path for mimap.nii.gz
                        pet_dir = output_dir / f"sub-{subject}"
                        if session:
                            pet_dir = pet_dir / f"ses-{session}"
                        pet_dir = pet_dir / "pet"
                        fwhm = int(args.vol_fwhm)
                        bids_name = (
                            f"{prefix}_space-MNI152NLin2009cAsym_desc-sm{fwhm}"
                            f"_model-{model}_meas-{meas}_mimap.nii.gz"
                        )
                        bids_mimap = pet_dir / bids_name

                        if bids_mimap.exists():
                            # Compute robust vlim from workdir map (same data)
                            import nibabel as nib
                            import numpy as np

                            img = nib.load(str(vol_map))
                            data = np.asarray(img.dataobj)
                            vlim = _robust_vlim(data)

                            if vlim is not None:
                                fb_name = (
                                    f"{prefix}_model-{model}_meas-{meas}"
                                    f"_space-MNI152NLin2009cAsym_mimap.html"
                                )
                                fb_path = figures_dir / fb_name
                                _generate_freebrowse_viewer(
                                    bids_mimap=bids_mimap,
                                    template_path=template_path,
                                    vlim=vlim,
                                    output_html=fb_path,
                                    workdir=workdir,
                                )
                                fb_rel = f"{fig_rel_dir}/{fb_name}"
                                vol_html += (
                                    f'\n<br><a href="./{escape(fb_rel)}" '
                                    f'target="_blank">View results in freebrowse</a>'
                                )
                        else:
                            logger.debug(
                                f"BIDS mimap not found for freebrowse: {bids_mimap}"
                            )
                    except Exception as exc:
                        logger.warning(
                            f"Could not generate freebrowse viewer for "
                            f"{model} {meas}: {exc}"
                        )

                method_figures.append(vol_html)

        # --- Surface figures ---
        for hemi in ("lh", "rh"):
            surf_key = f"{method}_surf_{hemi}_dir"
            if surf_key in temps:
                surf_map = temps[surf_key] / map_file
                if surf_map.exists():
                    bids_hemi = HEMI_BIDS[hemi]
                    fig_name = (
                        f"{prefix}_model-{model}_meas-{meas}"
                        f"_hemi-{bids_hemi}_space-fsaverage_mimap.png"
                    )
                    fig_path = figures_dir / fig_name
                    _generate_surface_figure(surf_map, hemi, fig_path, meas)
                    rel = f"{fig_rel_dir}/{fig_name}"
                    method_figures.append(
                        f'<h5>Surface ({hemi.upper()}, fsaverage)</h5>\n'
                        f'<img src="./{escape(rel)}" class="img-fluid mb-3" '
                        f'alt="{model} {meas} {hemi}">'
                    )

        if method_figures:
            figures_html = "\n".join(method_figures)
            figure_sections.append(
                f'<div class="mb-4">\n'
                f'<h4>{model} &mdash; {meas}</h4>\n'
                f'{figures_html}\n'
                f'</div>'
            )

    # Build ROI tables
    roi_sections: list[str] = []
    for method in methods_run:
        meas = MEAS_LABELS[method]
        model = MODEL_LABELS[method]
        roi_key = f"{method}_roi_dir"
        if roi_key in temps:
            roi_file = "gamma.table.dat" if meas == "BPND" else "vt.dat"
            roi_path = temps[roi_key] / roi_file
            if roi_path.exists():
                table_html = _build_roi_table_html(roi_path, meas)
                roi_sections.append(
                    f'<div class="mb-4">\n'
                    f'<h4>{model} &mdash; {meas}</h4>\n'
                    f'{table_html}\n'
                    f'</div>'
                )

    # Assemble full report
    summary_html = _build_summary_html(inputs, args, temps, template_warning)
    about_html = _build_about_html(args, command_history)

    _write_report_html(
        output_dir=output_dir,
        subject=subject,
        session=session,
        summary_section=summary_html,
        figures_sections=figure_sections,
        roi_sections=roi_sections,
        about_section=about_html,
    )

    report_name = f"{label}.html"
    logger.info(f"Report written to {output_dir / report_name}")


# ---------------------------------------------------------------------------
# Template fetching
# ---------------------------------------------------------------------------


def _fetch_mni_template() -> Path | None:
    """Fetch the MNI152NLin2009cAsym T1w template via templateflow.

    Returns the path to the template NIfTI, or ``None`` if fetching fails
    (network error, missing package, etc.).
    """
    try:
        from templateflow.api import get as tpl_get

        path = tpl_get(
            "MNI152NLin2009cAsym",
            resolution=1,
            suffix="T1w",
            desc=None,
            extension=".nii.gz",
        )
        if path is not None:
            logger.debug(f"MNI152 template: {path}")
            return Path(path)
    except Exception as exc:
        logger.warning(f"Could not fetch MNI152 template: {exc}")

    return None


# ---------------------------------------------------------------------------
# Sourcedata helpers
# ---------------------------------------------------------------------------


def _ensure_sourcedata(output_dir: Path, template_path: Path | None) -> None:
    """Copy fetched templates into ``<output_dir>/sourcedata/``.

    Mirrors the petprep convention:

    - ``sourcedata/tpl-MNI152NLin2009cAsym/`` — the T1w template file
    - ``sourcedata/freesurfer/fsaverage/`` — nilearn's fsaverage meshes

    Idempotent: skips files that already exist.
    """
    sourcedata = output_dir / "sourcedata"

    # --- MNI152 template ---
    if template_path is not None:
        tpl_dest = sourcedata / "tpl-MNI152NLin2009cAsym" / template_path.name
        if not tpl_dest.exists():
            tpl_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(template_path, tpl_dest)
            logger.info(f"Copied MNI152 template to {tpl_dest}")

    # --- fsaverage meshes ---
    try:
        from nilearn.datasets import fetch_surf_fsaverage

        fsaverage = fetch_surf_fsaverage(mesh="fsaverage")
        fs_dest = sourcedata / "freesurfer" / "fsaverage"
        fs_dest.mkdir(parents=True, exist_ok=True)

        # Only copy the meshes and background maps used for plotting
        used_keys = ("pial_left", "pial_right", "sulc_left", "sulc_right")
        for key in used_keys:
            src = Path(fsaverage[key])
            if src.is_file():
                dst = fs_dest / src.name
                if not dst.exists():
                    shutil.copy2(src, dst)
        logger.info(f"Copied fsaverage meshes to {fs_dest}")
    except Exception as exc:
        logger.warning(f"Could not copy fsaverage to sourcedata: {exc}")


# ---------------------------------------------------------------------------
# Figure helpers
# ---------------------------------------------------------------------------


def _make_figures_dir(output_dir: Path, subject: str, session: str | None) -> Path:
    """Create ``<output_dir>/sub-XX/[ses-YY/]figures/`` and return the path."""
    figures_dir = output_dir / f"sub-{subject}"
    if session:
        figures_dir = figures_dir / f"ses-{session}"
    figures_dir = figures_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    return figures_dir


def _robust_vlim(data, percentile: float = 98.0) -> tuple[float, float] | None:
    """Return robust (vmin, vmax) display limits from non-zero data.

    Some methods (MRTM1, MA1) produce extreme outlier voxels (values > 10 000)
    while the meaningful signal sits near 0-5.  Using the raw min/max washes
    out the colour scale.  We clip to the lower and upper *percentile*
    of non-zero values so that the bulk of the data is visible.
    """
    import numpy as np

    masked = data[data != 0]
    if masked.size == 0:
        return None
    lo = 100.0 - percentile
    vmin = float(np.percentile(masked, lo))
    vmax = float(np.percentile(masked, percentile))
    return vmin, vmax


def _generate_volume_figure(
    stat_map: Path,
    template: Path | None,
    output_path: Path,
    meas: str,
) -> None:
    """Render an axial mosaic of *stat_map* over the MNI template and save SVG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import nibabel as nib
        import numpy as np
        from nilearn.plotting import plot_stat_map

        img = nib.load(str(stat_map))
        data = np.asarray(img.dataobj)
        vlim = _robust_vlim(data)

        kwargs: dict = {
            "stat_map_img": str(stat_map),
            "display_mode": "z",
            "cut_coords": 7,
            "title": meas,
            "colorbar": True,
            "output_file": str(output_path),
        }
        if template is not None:
            kwargs["bg_img"] = str(template)
        if vlim is not None:
            kwargs["vmax"] = max(abs(vlim[0]), abs(vlim[1]))

        plot_stat_map(**kwargs)
        logger.debug(f"Volume figure saved: {output_path}")

    except Exception as exc:
        logger.warning(f"Could not generate volume figure {output_path.name}: {exc}")


def _generate_surface_figure(
    stat_map: Path,
    hemi: str,
    output_path: Path,
    meas: str,
) -> None:
    """Render lateral + medial surface views and save as PNG.

    PNG is used instead of SVG because the full-resolution fsaverage mesh
    (163 842 vertices) produces SVG files > 100 MB.

    Our surface parametric maps are FreeSurfer NIfTI (N_vertices x 1 x 1).
    We load with nibabel, flatten, and pass to nilearn's
    ``plot_surf_stat_map``.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import nibabel as nib
        import numpy as np
        from nilearn.datasets import fetch_surf_fsaverage
        from nilearn.plotting import plot_surf_stat_map

        fsaverage = fetch_surf_fsaverage(mesh="fsaverage")

        # Load surface data: FreeSurfer NIfTI → flat array
        img = nib.load(str(stat_map))
        data = np.asarray(img.dataobj).ravel()

        # Robust display range (same logic as volumetric)
        vlim = _robust_vlim(data)

        # nilearn hemisphere keys
        if hemi == "lh":
            mesh_key = "pial_left"
            bg_key = "sulc_left"
        else:
            mesh_key = "pial_right"
            bg_key = "sulc_right"

        fig, axes = plt.subplots(
            1, 2, figsize=(12, 5),
            subplot_kw={"projection": "3d"},
        )

        surf_kwargs: dict = {
            "surf_mesh": fsaverage[mesh_key],
            "stat_map": data,
            "bg_map": fsaverage[bg_key],
            "hemi": ("left" if hemi == "lh" else "right"),
        }
        if vlim is not None:
            surf_kwargs["vmin"] = vlim[0]
            surf_kwargs["vmax"] = vlim[1]

        for ax, view in zip(axes, ["lateral", "medial"]):
            plot_surf_stat_map(
                **surf_kwargs,
                view=view,
                title=f"{meas} ({hemi.upper()}, {view})",
                colorbar=(view == "lateral"),
                axes=ax,
            )

        fig.savefig(str(output_path), format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        logger.debug(f"Surface figure saved: {output_path}")

    except Exception as exc:
        logger.warning(f"Could not generate surface figure {output_path.name}: {exc}")


# ---------------------------------------------------------------------------
# ROI table helpers
# ---------------------------------------------------------------------------


def _build_roi_table_html(roi_path: Path, meas: str) -> str:
    """Read a FreeSurfer .dat ROI file and return an HTML ``<table>``."""
    try:
        with open(roi_path) as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.warning(f"Could not read {roi_path}: {exc}")
        return "<p><em>ROI data unavailable.</em></p>"

    if not lines:
        return "<p><em>ROI data empty.</em></p>"

    # Determine format: gamma.table.dat has a header row; vt.dat / bp.dat do not
    first_fields = lines[0].split()
    has_header = first_fields[0] == "frame_start" or first_fields[0] == "Frame"

    rows: list[list[str]] = []
    header: list[str] = []

    if has_header:
        # gamma.table.dat: first token is a label placeholder, rest are param names
        header = ["Region"] + first_fields[1:]
        for line in lines[1:]:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) >= 2:
                rows.append([parts[0]] + parts[1:])
    else:
        # vt.dat / bp.dat: two columns (region, value)
        header = ["Region", meas]
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) >= 2:
                rows.append([parts[0], parts[-1]])

    # Build HTML
    header_cells = "".join(f"<th>{escape(h)}</th>" for h in header)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{escape(c)}</td>" for c in row)
        body_rows.append(f"<tr>{cells}</tr>")

    return (
        '<div class="table-responsive" style="max-height:400px;overflow-y:auto">\n'
        '<table class="table table-sm table-striped table-hover">\n'
        f"<thead><tr>{header_cells}</tr></thead>\n"
        f'<tbody>\n{"".join(body_rows)}\n</tbody>\n'
        "</table>\n"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Summary / About helpers
# ---------------------------------------------------------------------------


def _build_summary_html(
    inputs: InputGroup,
    args: Namespace,
    temps: dict[str, Path],
    template_warning: bool,
) -> str:
    """Build the summary section HTML."""
    methods_run = [m for m in ["mrtm1", "mrtm2", "logan", "logan-ma1"] if m in args.km_method]

    rows: list[str] = []

    def _row(label: str, value: str) -> None:
        rows.append(f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>")

    _row("Subject", f"sub-{inputs.subject}")
    if inputs.session:
        _row("Session", f"ses-{inputs.session}")
    if inputs.tracer:
        _row("Tracer", inputs.tracer)
    _row("Methods", ", ".join(MODEL_LABELS[m] for m in methods_run))

    # Reference region (MRTM methods)
    if any(m in ("mrtm1", "mrtm2") for m in methods_run):
        _row("Reference region(s)", ", ".join(args.mrtm1_ref))

    # High-binding region (MRTM2)
    if "mrtm2" in methods_run:
        _row("High-binding region(s)", ", ".join(args.mrtm2_hb))
        if "k2prime" in temps:
            try:
                with open(temps["k2prime"]) as f:
                    k2p = f.read().strip()
                _row("k2prime", k2p)
            except OSError:
                pass

    # Logan tstar
    if any(m in ("logan", "logan-ma1") for m in methods_run):
        _row("t* (seconds)", str(args.tstar))

    _row("Volumetric smoothing (FWHM)", f"{args.vol_fwhm} mm")
    _row("Surface smoothing (FWHM)", f"{args.surf_fwhm} mm")

    table = (
        '<table class="table table-bordered">\n'
        f'<tbody>\n{"".join(rows)}\n</tbody>\n'
        "</table>"
    )

    warning = ""
    if template_warning:
        warning = (
            '<div class="alert alert-warning" role="alert">'
            "MNI152 template could not be fetched; volume figures use "
            "nilearn's default background."
            "</div>\n"
        )

    return warning + table


def _build_about_html(
    args: Namespace,
    command_history: list[tuple[str, str]],
) -> str:
    """Build the About section HTML."""
    cmd_line = " ".join(sys.argv) if sys.argv else "(unavailable)"

    # Commands executed during processing
    if command_history:
        cmd_items = "\n".join(
            f"<li><strong>{escape(desc)}</strong><br>"
            f"<code>{escape(cmd)}</code></li>"
            for cmd, desc in command_history
        )
        cmds_cell = f'<ol class="mb-0">\n{cmd_items}\n</ol>'
    else:
        cmds_cell = "<em>No commands recorded.</em>"

    rows = [
        f"<tr><th>petsurfer-km version</th><td>{escape(__version__)}</td></tr>",
        f"<tr><th>Invocation Command</th><td><code>{escape(cmd_line)}</code></td></tr>",
        f"<tr><th>Commands Executed</th><td>{cmds_cell}</td></tr>",
    ]
    return (
        '<table class="table table-bordered">\n'
        f'<tbody>\n{"".join(rows)}\n</tbody>\n'
        "</table>"
    )


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------


def _build_prefix(inputs: InputGroup) -> str:
    """Build BIDS filename prefix: ``sub-XX[_ses-YY][_trc-ZZ]``."""
    parts = [f"sub-{inputs.subject}"]
    if inputs.session:
        parts.append(f"ses-{inputs.session}")
    if inputs.tracer:
        parts.append(f"trc-{inputs.tracer}")
    return "_".join(parts)


def _write_report_html(
    output_dir: Path,
    subject: str,
    session: str | None,
    summary_section: str,
    figures_sections: list[str],
    roi_sections: list[str],
    about_section: str,
) -> None:
    """Assemble all sections into a Bootstrap 5 HTML file."""

    label = f"sub-{subject}" + (f"_ses-{session}" if session else "")

    figures_body = "\n".join(figures_sections) if figures_sections else (
        "<p><em>No parametric map figures available.</em></p>"
    )
    roi_body = "\n".join(roi_sections) if roi_sections else (
        "<p><em>No ROI data available.</em></p>"
    )

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>petsurfer-km &mdash; {escape(label)}</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.2.3/dist/css/bootstrap.min.css"
      rel="stylesheet"
      integrity="sha384-rbsA2VBKQhggwzxH7pPCaAqO46MgnOM80zW1RWuH61DGLwZJEdK2Kadq2F9CUG65"
      crossorigin="anonymous">
<style>
  body {{ padding-top: 56px; }}
  .table th {{ white-space: nowrap; }}
  .table-responsive {{ font-size: 0.85rem; }}
  img.img-fluid {{ max-width: 100%; height: auto; }}
</style>
</head>
<body>

<nav class="navbar navbar-expand-lg navbar-dark bg-dark fixed-top">
  <div class="container-fluid">
    <a class="navbar-brand" href="#">petsurfer-km</a>
    <button class="navbar-toggler" type="button" data-bs-toggle="collapse"
            data-bs-target="#navContent">
      <span class="navbar-toggler-icon"></span>
    </button>
    <div class="collapse navbar-collapse" id="navContent">
      <ul class="navbar-nav me-auto">
        <li class="nav-item"><a class="nav-link" href="#summary">Summary</a></li>
        <li class="nav-item"><a class="nav-link" href="#maps">Parametric Maps</a></li>
        <li class="nav-item"><a class="nav-link" href="#roi">ROI Results</a></li>
        <li class="nav-item"><a class="nav-link" href="#about">About</a></li>
      </ul>
      <span class="navbar-text">{escape(label)}</span>
    </div>
  </div>
</nav>

<div class="container my-4">

  <section id="summary">
    <h2>Summary</h2>
    {summary_section}
  </section>

  <hr>

  <section id="maps">
    <h2>Parametric Maps</h2>
    {figures_body}
  </section>

  <hr>

  <section id="roi">
    <h2>ROI Results</h2>
    {roi_body}
  </section>

  <hr>

  <section id="about">
    <h2>About</h2>
    {about_section}
  </section>

</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.2.3/dist/js/bootstrap.bundle.min.js"
        integrity="sha384-kenU1KFdBIe4zVF0s0G1M5b4hcpxyD9F7jL+jjXkk+Q2h455rYXK/7HAuoJl+0I4"
        crossorigin="anonymous"></script>
</body>
</html>
"""

    report_path = output_dir / f"{label}.html"
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as fh:
        fh.write(html)
