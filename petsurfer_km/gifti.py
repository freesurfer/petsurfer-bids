"""GIFTI output helpers for surface parametric maps.

petsurfer-km computes surface overlays as FreeSurfer "1D NIfTI" volumes
(``N_vertices x 1 x 1``) in the work directory.  BIDS derivatives expect
per-vertex data on a surface as GIFTI, with ``.func.gii`` the catch-all
extension for floating point data sampled to a surface (one or more
``NIFTI_TYPE_FLOAT32`` data arrays with ``NIFTI_INTENT_NONE`` or a
statistical intent).

The conversion is delegated entirely to FreeSurfer's ``mri_convert``; the file
it writes is the deliverable and is not modified afterwards.  Known
limitations of ``mri_convert``'s GIFTI writer (no ``AnatomicalStructurePrimary``,
``UserName``/``Date`` metadata) are tracked upstream rather than patched here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from petsurfer_km.execution import run_command

logger = logging.getLogger("petsurfer_km")

# GIFTI extension for floating point per-vertex data (BIDS derivatives).
FUNC_GII_EXT = ".func.gii"

# Interpretation of the fsaverage surface density (for the ``Density`` sidecar key).
FSAVERAGE_DENSITY = "163842 vertices per hemisphere (fsaverage)"

# GIFTI standard ``AnatomicalStructurePrimary`` value per hemisphere.  Not written
# by ``mri_convert`` today; kept here for the sidecar/README and future use.
HEMI_STRUCTURE = {"lh": "CortexLeft", "rh": "CortexRight"}

_TEMPLATEFLOW = "https://templateflow.s3.amazonaws.com"


def fsaverage_spatial_reference(bids_hemi: str) -> str:
    """Return the TemplateFlow fsaverage white surface URL for ``bids_hemi`` (``L``/``R``)."""
    return f"{_TEMPLATEFLOW}/tpl-fsaverage/tpl-fsaverage_hemi-{bids_hemi}_den-164k_white.surf.gii"


def surface_sidecar_fields(bids_hemi: str) -> dict:
    """Sidecar keys describing the fsaverage surface sampling of a per-vertex map."""
    return {
        "SpatialReference": fsaverage_spatial_reference(bids_hemi),
        "Density": FSAVERAGE_DENSITY,
    }


def nifti_to_func_gii(
    src: Path,
    dest: Path,
    hemi: str,
    command_history: list[tuple[str, str]] | None = None,
) -> bool:
    """Convert a 1D NIfTI surface overlay to ``.func.gii`` with ``mri_convert``.

    Args:
        src: FreeSurfer 1D NIfTI overlay (``N_vertices x 1 x 1``).
        dest: Output path; should end in ``.func.gii``.
        hemi: Internal hemisphere label (``lh``/``rh``), used for logging only.
        command_history: If given, ``(command, description)`` is appended.

    Returns:
        True if ``dest`` was written; False if ``src`` does not exist (a warning
        is logged, mirroring the behaviour of the plain NIfTI copy).

    Raises:
        RuntimeError: if ``mri_convert`` fails or produces no output file.
    """
    if not src.exists():
        logger.warning(f"Expected output not found, skipping: {src}")
        return False

    description = f"Convert {hemi} surface overlay to GIFTI"
    result = run_command(["mri_convert", str(src), str(dest)], description)
    if command_history is not None:
        command_history.append((result.command, description))
    if result.exit_code != 0:
        raise RuntimeError(
            f"mri_convert failed converting {src} to GIFTI: {result.stderr}"
        )
    if not dest.exists():
        raise RuntimeError(f"mri_convert exited 0 but did not write {dest}")

    logger.info(f"  {dest.name}")
    return True
