"""Shared report helpers for petsurfer-km participant and group reports.

Provides freebrowse viewer generation, MNI template fetching, sourcedata
copying, and nilearn figure generation.  Extracted from the participant
``step06_report.py`` so both participant and group reports share the same
implementation.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger("petsurfer_km")

# ---------------------------------------------------------------------------
# Freebrowse helpers
# ---------------------------------------------------------------------------

_FREEBROWSE_DIR = Path(__file__).resolve().parent / "freebrowse"

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


def generate_freebrowse_viewer(
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
# Template fetching
# ---------------------------------------------------------------------------


def fetch_mni_template() -> Path | None:
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


def ensure_sourcedata(output_dir: Path, template_path: Path | None) -> None:
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


def robust_vlim(data, percentile: float = 98.0) -> tuple[float, float] | None:
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


def generate_volume_figure(
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
        vlim = robust_vlim(data)

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


def generate_surface_figure(
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
        vlim = robust_vlim(data)

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
