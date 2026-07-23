"""Group-level HTML report for petsurfer-km.

Generates a Bootstrap 5 HTML report summarising group-level GLM contrast
estimate results, including:
- Design section (FSGD details or OSGM notice)
- Contrast estimate maps (volumetric MNI152 + surface fsaverage) per contrast
- ROI-level contrast estimate table
- About / provenance section

Visualisations are created with nilearn and saved in ``<output_dir>/figures/``.
Freebrowse interactive viewers are generated for MNI volumetric maps.
"""

from __future__ import annotations

import logging
import sys
from argparse import Namespace
from html import escape
from pathlib import Path

from petsurfer_km import __version__
from petsurfer_km.methods import HEMI_BIDS
from petsurfer_km.report_helpers import (
    ensure_sourcedata,
    fetch_mni_template,
    generate_freebrowse_viewer,
    generate_surface_figure,
    generate_volume_figure,
    robust_vlim,
)
from petsurfer_km.steps.group.step01_setup import GroupContext
from petsurfer_km.steps.group.step03_bidsify import (
    ATLAS_LABEL,
    _discover_contrasts_map,
    _discover_contrasts_roi,
    _read_sample_size,
    _sanitize_contrast,
    _space_output,
)

logger = logging.getLogger("petsurfer_km")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_group_report(
    context: GroupContext,
    args: Namespace,
    workdir: Path,
    command_history: list[tuple[str, str]] | None = None,
    file_mappings: list[tuple[str, str]] | None = None,
) -> None:
    """Generate a group-level HTML report with figures and freebrowse viewers.

    Args:
        context: Group analysis context from setup.
        args: Parsed CLI arguments.
        workdir: Working directory (must still exist; run before cleanup).
        command_history: List of (command, description) tuples from analyze step.
        file_mappings: List of (work_relative, output_relative) tuples from bidsify step.
    """
    logger.info("Generating group report")

    output_dir = args.output_dir
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    # Fetch MNI template (once, with graceful fallback)
    template_path = fetch_mni_template()
    template_warning = template_path is None

    # Copy templates into sourcedata/ (idempotent)
    ensure_sourcedata(output_dir, template_path)

    # Determine sample size for the summary
    sample_size = _read_sample_size(workdir, context)

    # Build figure sections (per contrast)
    figure_sections = _build_figure_sections(
        context, args, workdir, output_dir, figures_dir, template_path
    )

    # Build ROI table section
    roi_section = _build_roi_section(context, workdir)

    # Build summary, design, and about sections
    summary_html = _build_summary_html(context, args, sample_size, template_warning)
    design_html = _build_design_html(context)
    about_html = _build_about_html(args, command_history, workdir, output_dir, file_mappings)

    # Assemble final HTML
    _write_group_report_html(
        output_dir, summary_html, design_html, figure_sections, roi_section, about_html
    )

    logger.info(f"Report written to {output_dir / 'group.html'}")


# ---------------------------------------------------------------------------
# BIDS naming helper
# ---------------------------------------------------------------------------


def _build_mimap_bids_name(
    tpl: str, hemi: str | None, sanitized: str, model: str, meas: str
) -> str:
    """Construct the BIDS mimap base filename (without extension)."""
    parts = [f"tpl-{tpl}"]
    if hemi:
        parts.append(f"hemi-{hemi}")
    parts += [
        f"atlas-{ATLAS_LABEL}",
        f"desc-{sanitized}",
        f"model-{model}",
        f"meas-{meas}",
        "mimap",
    ]
    return "_".join(parts)


# ---------------------------------------------------------------------------
# Figure sections
# ---------------------------------------------------------------------------


def _build_figure_sections(
    context: GroupContext,
    args: Namespace,
    workdir: Path,
    output_dir: Path,
    figures_dir: Path,
    template_path: Path | None,
) -> list[str]:
    """Build per-contrast figure HTML sections across all non-ROI spaces."""
    contrast_html: dict[str, list[str]] = {}
    contrast_order: list[str] = []

    for space in context.spaces:
        if space == "ROI":
            continue

        glmdir = workdir / f"glm.{space}"
        contrasts = _discover_contrasts_map(glmdir)
        if not contrasts:
            continue

        tpl, hemi, fwhm = _space_output(space, args)

        for contrast in contrasts:
            if contrast not in contrast_html:
                contrast_html[contrast] = []
                contrast_order.append(contrast)

            sanitized = _sanitize_contrast(contrast)
            gamma_src = glmdir / contrast / "gamma.nii.gz"

            if space == "mni":
                fig_name = (
                    f"{_build_mimap_bids_name(tpl, None, sanitized, context.model, context.meas)}.svg"
                )
                generate_volume_figure(
                    gamma_src, template_path, figures_dir / fig_name, context.meas
                )
                rel = f"figures/{fig_name}"
                vol_html = (
                    f'<h5>Volumetric (MNI152)</h5>\n'
                    f'<img src="./{escape(rel)}" class="img-fluid mb-3" '
                    f'alt="{escape(contrast)} {context.meas} MNI152">'
                )

                # Freebrowse viewer for MNI maps
                if not getattr(args, "no_freebrowse", False) and template_path is not None:
                    try:
                        bids_name = _build_mimap_bids_name(
                            tpl, None, sanitized, context.model, context.meas
                        )
                        bids_mimap = (
                            output_dir / f"tpl-{tpl}" / "pet" / f"{bids_name}.nii.gz"
                        )
                        if bids_mimap.exists():
                            import nibabel as nib
                            import numpy as np

                            img = nib.load(str(gamma_src))
                            data = np.asarray(img.dataobj)
                            vlim = robust_vlim(data)

                            if vlim is not None:
                                fb_name = f"{bids_name}.html"
                                generate_freebrowse_viewer(
                                    bids_mimap=bids_mimap,
                                    template_path=template_path,
                                    vlim=vlim,
                                    output_html=figures_dir / fb_name,
                                    workdir=workdir,
                                )
                                fb_rel = f"figures/{fb_name}"
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
                            f"{contrast}: {exc}"
                        )

                contrast_html[contrast].append(vol_html)

            elif space.startswith("fsaverage"):
                hemi_internal = space.replace("fsaverage-", "")  # "lh" or "rh"
                fig_name = (
                    f"{_build_mimap_bids_name(tpl, hemi, sanitized, context.model, context.meas)}.png"
                )
                generate_surface_figure(
                    gamma_src, hemi_internal, figures_dir / fig_name, context.meas
                )
                rel = f"figures/{fig_name}"
                contrast_html[contrast].append(
                    f'<h5>Surface ({hemi_internal.upper()}, fsaverage)</h5>\n'
                    f'<img src="./{escape(rel)}" class="img-fluid mb-3" '
                    f'alt="{escape(contrast)} {context.meas} {hemi_internal}">'
                )

    # Assemble one <div> per contrast
    sections: list[str] = []
    for contrast in contrast_order:
        figures_html = "\n".join(contrast_html[contrast])
        sections.append(
            f'<div class="mb-4">\n'
            f'<h4>{escape(contrast)}</h4>\n'
            f'{figures_html}\n'
            f'</div>'
        )
    return sections


# ---------------------------------------------------------------------------
# ROI section
# ---------------------------------------------------------------------------


def _build_roi_section(context: GroupContext, workdir: Path) -> str:
    """Build the ROI HTML table from ``glm.ROI/gamma.table.dat``."""
    if "ROI" not in context.spaces:
        return "<p><em>No ROI data available.</em></p>"

    gamma_table = workdir / "glm.ROI" / "gamma.table.dat"
    if not gamma_table.exists():
        return "<p><em>No ROI data available.</em></p>"

    try:
        with open(gamma_table) as fh:
            lines = fh.readlines()
    except OSError as exc:
        logger.warning(f"Could not read {gamma_table}: {exc}")
        return "<p><em>ROI data unavailable.</em></p>"

    if not lines:
        return "<p><em>ROI data empty.</em></p>"

    # First line is header: "Subject  <contrast1>  <contrast2>  ..."
    header_fields = lines[0].split()
    header_cells = "".join(
        f"<th>{escape(h)}</th>" if i > 0 else f"<th>Region</th>"
        for i, h in enumerate(header_fields)
    )

    body_rows = []
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            cells = "".join(f"<td>{escape(c)}</td>" for c in parts)
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
# Summary section
# ---------------------------------------------------------------------------


def _build_summary_html(
    context: GroupContext,
    args: Namespace,
    sample_size: int | None,
    template_warning: bool,
) -> str:
    """Build the summary section HTML."""
    rows: list[str] = []

    def _row(label: str, value: str) -> None:
        rows.append(f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>")

    if context.fsgd is None:
        _row("Analysis", "One-sample group mean (OSGM)")
    else:
        _row("Analysis", f"FSGD: {context.fsgd.title}")

    _row("Tracer", context.tracer)
    _row("Kinetic model", context.model)
    _row("Measure", context.meas)
    _row("Sample size", f"{sample_size} subjects" if sample_size is not None else "Unknown")
    _row("Sessions", ", ".join(context.sessions))
    _row("Spaces", ", ".join(context.spaces))
    _row("Volumetric smoothing (FWHM)", f"{args.vol_fwhm} mm")
    _row("Surface smoothing (FWHM)", f"{args.surf_fwhm} mm")

    if args.cmc is not None:
        _row(
            "CMC",
            f"perm={args.cmc[1]}, sign={args.cmc[2]}, cft={args.cmc[0]}, fwer={args.cmc[4]}",
        )
    else:
        _row("CMC", "None")

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


# ---------------------------------------------------------------------------
# Design section
# ---------------------------------------------------------------------------


def _build_design_html(context: GroupContext) -> str:
    """Build the Design section HTML.

    When no FSGD file was used (one-sample group mean), shows a simple OSGM
    notice. When an FSGD file was used, shows the full design details:
    file path, metadata, contrasts, subject table, and raw file content.
    """
    if context.fsgd is None:
        return (
            '<div class="alert alert-info">\n'
            "  <strong>Design:</strong> One-sample group mean (OSGM). "
            'A single contrast (<code>osgm</code>) testing whether the group '
            "mean differs from zero. No FSGD file was provided.\n"
            "</div>"
        )

    fsgd = context.fsgd
    parts: list[str] = []

    # 1. File path
    if context.fsgd_file is not None:
        parts.append(
            f'<p><strong>FSGD file:</strong> <code>{escape(str(context.fsgd_file))}</code></p>'
        )

    # 2. Metadata table
    meta_rows = [
        f"<tr><th>Title</th><td>{escape(fsgd.title or '')}</td></tr>",
        f"<tr><th>Classes</th><td>{escape(', '.join(fsgd.classes))}</td></tr>",
        f"<tr><th>Variables</th><td>{escape(', '.join(fsgd.variables) if fsgd.variables else 'None')}</td></tr>",
        f"<tr><th>Subjects</th><td>{len(fsgd.df)}</td></tr>",
    ]
    parts.append(
        '<table class="table table-bordered">\n'
        f'<tbody>\n{"".join(meta_rows)}\n</tbody>\n'
        "</table>"
    )

    # 3. Contrasts table
    contrasts = _parse_fsgd_contrasts(context.fsgd_file) if context.fsgd_file else []
    if contrasts:
        contrast_rows = "\n".join(
            f"<tr><td>{escape(label)}</td><td>{escape(weights)}</td></tr>"
            for label, weights in contrasts
        )
        parts.append(
            '<h5>Contrasts</h5>\n'
            '<table class="table table-sm table-striped">\n'
            "<thead><tr><th>Contrast</th><th>Weights</th></tr></thead>\n"
            f"<tbody>\n{contrast_rows}\n</tbody>\n"
            "</table>"
        )

    # 4. Subject table (manual build with escaping)
    df = fsgd.df
    col_names = ["subject_id", "group"] + list(fsgd.variables)
    header_cells = "".join(f"<th>{escape(c)}</th>" for c in col_names)
    body_rows = []
    for _, row in df.iterrows():
        cells = "".join(f"<td>{escape(str(row.get(c, '')))}</td>" for c in col_names)
        body_rows.append(f"<tr>{cells}</tr>")
    parts.append(
        '<h5>Subjects</h5>\n'
        '<div class="table-responsive" style="max-height:300px;overflow-y:auto">\n'
        '<table class="table table-sm table-striped table-hover">\n'
        f"<thead><tr>{header_cells}</tr></thead>\n"
        f'<tbody>\n{"".join(body_rows)}\n</tbody>\n'
        "</table>\n"
        "</div>"
    )

    # 5. Raw FSGD file content
    if context.fsgd_file is not None:
        try:
            raw_content = context.fsgd_file.read_text()
        except OSError as exc:
            logger.warning(f"Could not read FSGD file: {exc}")
            raw_content = f"(Could not read file: {exc})"
        parts.append(
            '<h5>Raw FSGD File</h5>\n'
            f'<pre class="bg-light p-3"><code>{escape(raw_content)}</code></pre>'
        )

    return "\n".join(parts)


def _parse_fsgd_contrasts(filepath: Path) -> list[tuple[str, str]]:
    """Read an FSGD file and extract Contrast lines.

    Returns a list of (label, weights_string) tuples.
    """
    contrasts: list[tuple[str, str]] = []
    with open(filepath) as f:
        for line in f:
            parts = line.strip().split()
            if parts and parts[0] == "Contrast":
                label = parts[1]
                weights = " ".join(parts[2:])
                contrasts.append((label, weights))
    return contrasts


# ---------------------------------------------------------------------------
# About section
# ---------------------------------------------------------------------------


def _build_about_html(
    args: Namespace,
    command_history: list[tuple[str, str]] | None,
    workdir: Path,
    output_dir: Path,
    file_mappings: list[tuple[str, str]] | None,
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

    # Work directory cell
    work_dir_cell = f"<code>{escape(str(workdir))}</code>"
    if not args.nocleanup:
        work_dir_cell += (
            '<br><small><b>Note:</b> work directory was cleaned up and is no longer '
            'available. Use <code>--nocleanup</code> to preserve work directory.</small>'
        )

    # Output directory cell
    output_dir_cell = f"<code>{escape(str(output_dir))}</code>"

    # File mapping cell
    if file_mappings:
        mapping_rows = "\n".join(
            f"<tr><td><code>{escape(work_rel)}</code></td>"
            f"<td><code>{escape(out_rel)}</code></td></tr>"
            for work_rel, out_rel in file_mappings
        )
        mapping_cell = (
            '<div class="table-responsive">\n'
            '<table class="table table-sm table-striped table-hover mb-0">\n'
            "<thead><tr><th>Work Directory</th><th>Output Directory</th></tr></thead>\n"
            f"<tbody>\n{mapping_rows}\n</tbody>\n"
            "</table>\n"
            "</div>"
        )
    else:
        mapping_cell = "<em>No file mappings recorded.</em>"

    rows = [
        f"<tr><th>petsurfer-km version</th><td>{escape(__version__)}</td></tr>",
        f"<tr><th>Invocation Command</th><td><code>{escape(cmd_line)}</code></td></tr>",
        f"<tr><th>Commands Executed</th><td>{cmds_cell}</td></tr>",
        f"<tr><th>Work Directory</th><td>{work_dir_cell}</td></tr>",
        f"<tr><th>Output Directory</th><td>{output_dir_cell}</td></tr>",
        f"<tr><th>File Mapping</th><td>{mapping_cell}</td></tr>",
    ]
    return (
        '<table class="table table-bordered">\n'
        f'<tbody>\n{"".join(rows)}\n</tbody>\n'
        "</table>"
    )


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------


def _write_group_report_html(
    output_dir: Path,
    summary_section: str,
    design_section: str,
    figures_sections: list[str],
    roi_section: str,
    about_section: str,
) -> None:
    """Assemble all sections into a Bootstrap 5 HTML file."""
    figures_body = "\n".join(figures_sections) if figures_sections else (
        "<p><em>No contrast map figures available.</em></p>"
    )

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>petsurfer-km &mdash; Group Analysis</title>
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
        <li class="nav-item"><a class="nav-link" href="#design">Design</a></li>
        <li class="nav-item"><a class="nav-link" href="#maps">Contrast Maps</a></li>
        <li class="nav-item"><a class="nav-link" href="#roi">ROI Results</a></li>
        <li class="nav-item"><a class="nav-link" href="#about">About</a></li>
      </ul>
      <span class="navbar-text">Group Analysis</span>
    </div>
  </div>
</nav>

<div class="container my-4">

  <section id="summary">
    <h2>Group-level Summary</h2>
    {summary_section}
  </section>

  <hr>

  <section id="design">
    <h2>Design</h2>
    {design_section}
  </section>

  <hr>

  <section id="maps">
    <h2>Contrast Maps</h2>
    {figures_body}
  </section>

  <hr>

  <section id="roi">
    <h2>ROI Results</h2>
    {roi_section}
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

    report_path = output_dir / "group.html"
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as fh:
        fh.write(html)
