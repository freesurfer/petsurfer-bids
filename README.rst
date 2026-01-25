PetSurfer
=========

PetSurfer (often styled *PETSurfer* on the FreeSurfer wiki) is a set of tools within
FreeSurfer for end-to-end integrated MRI?PET analysis, including motion correction,
PET?MRI registration, reference-region kinetic modeling, partial volume correction
(PVC), and group analysis in ROI, volume, and surface spaces. :contentReference[oaicite:0]{index=0}

This repository README is a GitHub-friendly reStructuredText adaptation of the
official FreeSurfer wiki page.

.. contents::
   :local:
   :depth: 2


Key capabilities
----------------

* **Integrated MRI?PET workflow**: registration, motion correction, ROI/volume/surface analysis. :contentReference[oaicite:1]{index=1}
* **Kinetic modeling (KM)**: MRTM1, MRTM2, and invasive Logan modeling. :contentReference[oaicite:2]{index=2}
* **Partial volume correction (PVC)** methods: Symmetric GTM (SGTM), two-compartment (Meltzer),
  three-compartment (Müller-Gärtner / MG), and RBV; implementations also account for
  tissue fraction effect (TFE). :contentReference[oaicite:3]{index=3}
* **Three-space approach** for voxel-wise work: cortical and subcortical GM analyzed separately
  (LH cortex, RH cortex, subcortical GM) to leverage surface-based operations. :contentReference[oaicite:4]{index=4}


Recommended citation
--------------------

If you use PETSurfer, please cite:

* Greve, D.N., et al. (2014). *Cortical surface-based analysis reduces bias and variance in kinetic modeling of brain PET data.* NeuroImage, 92, 225?236. :contentReference[oaicite:5]{index=5}
* Greve, D.N., et al. (2016). *Different partial volume correction methods lead to different conclusions: An 18F-FDG-PET study of aging.* NeuroImage, 132, 334?343. :contentReference[oaicite:6]{index=6}


