"""Canonical kinetic-modeling method enumeration.

Shared by the CLI parser, the kinetic-modeling step, and the report builder
so that all four sites stay in sync. Methods execute in this order regardless
of CLI argument order. MRTM2 depends on MRTM1's k2prime output.  Running MRTM2
implies also running MRTM1
"""

from __future__ import annotations

KM_METHOD_ORDER = ["suvr", "mrtm1", "mrtm2", "logan", "logan-ma1", "patlak"]
