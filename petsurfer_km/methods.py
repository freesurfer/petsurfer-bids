"""Canonical kinetic-modeling method enumeration.

Shared by the CLI parser, the kinetic-modeling step, and the report builder
so that all four sites stay in sync. Methods execute in this order regardless
of CLI argument order. MRTM2 depends on MRTM1's k2prime output.  Running MRTM2
implies also running MRTM1
"""

from __future__ import annotations

KM_METHOD_ORDER = ["suvr", "mrtm1", "mrtm2", "logan", "ma1", "patlak"]

# BEP023 model labels
MODEL_LABELS = {
    "suvr": "SUVR",
    "mrtm1": "MRTM1",
    "mrtm2": "MRTM2",
    "logan": "Logan",
    "ma1": "MA1",
    "patlak": "Patlak",
}

# Primary measurement per method
MEAS_LABELS = {
    "suvr": "SUVR",
    "mrtm1": "BPND",
    "mrtm2": "BPND",
    "logan": "VT",
    "ma1": "VT",
    "patlak": "Ki",
}

# Real mri_glmfit CLI flag name for invasive (AIF-based) methods. mri_glmfit
# itself only recognizes --logan-ma1 (there is no --ma1 flag), so this
# decouples our internal/CLI method identifier ("ma1") from the fixed
# external tool flag name. Methods not listed here use their own name as
# the flag (e.g. "logan" -> --logan, "patlak" -> --patlak).
GLMFIT_FLAG = {
    "ma1": "logan-ma1",
}

# Hemisphere mapping: internal (lh/rh) → BIDS (L/R)
HEMI_BIDS = {"lh": "L", "rh": "R"}

# Per-method header row for the ROI _kinpar.tsv (issue #2).
# First column is the ROI label; remaining columns are the parameters
# reported in the FreeSurfer .dat table for that method.
ROI_TSV_HEADERS: dict[str, tuple[str, ...]] = {
    "suvr":      ("ROI", "SUVR"),
    "mrtm1":     ("ROI", "k2", "k2a", "k2-k2a"),
    "mrtm2":     ("ROI", "k2", "k2a", "k2-k2a"),
    "logan":     ("ROI", "VT"),
    "ma1":       ("ROI", "VT"),
    "patlak":    ("ROI", "Ki"),
}

# FreeSurfer output filenames per method in the work directory:
# (volumetric/surface map .nii.gz, ROI .dat).  Surface maps are 1D NIfTI here
# and are converted to GIFTI (.func.gii) at the BIDSify step unless --nifti-surfaces.
MAP_FILES: dict[str, tuple[str | None, str | None]] = {
    "suvr": ("suvr.nii.gz", "suvr.dat"),
    "mrtm1": ("bp.nii.gz", "gamma.table.dat"),
    "mrtm2": ("bp.nii.gz", "gamma.table.dat"),
    "logan": ("vt.nii.gz", "vt.dat"),
    "ma1": ("vt.nii.gz", "vt.dat"),
    "patlak": ("Ki.nii.gz", "Ki.dat"),
}
