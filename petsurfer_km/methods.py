"""Canonical kinetic-modeling method enumeration.

Shared by the CLI parser, the kinetic-modeling step, and the report builder
so that all four sites stay in sync. Methods execute in this order regardless
of CLI argument order. MRTM2 depends on MRTM1's k2prime output.  Running MRTM2
implies also running MRTM1
"""

from __future__ import annotations

KM_METHOD_ORDER = ["suvr", "mrtm1", "mrtm2", "logan", "logan-ma1", "patlak"]

# BEP023 model labels
MODEL_LABELS = {
    "suvr": "SUVR",
    "mrtm1": "MRTM1",
    "mrtm2": "MRTM2",
    "logan": "Logan",
    "logan-ma1": "MA1",
    "patlak": "Patlak",
}

# Primary measurement per method
MEAS_LABELS = {
    "suvr": "SUVR",
    "mrtm1": "BPND",
    "mrtm2": "BPND",
    "logan": "VT",
    "logan-ma1": "VT",
    "patlak": "Ki",
}
