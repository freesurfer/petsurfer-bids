PETsurfer-BIDS
==============

This project is an encapsulation of the functionality in PETsurfer
within an easy-to-use BIDS app.  PETSurfer is a set of tools within
the FreeSurfer environement for end-to-end integrated MRI-PET
analysis, including motion correction (MC), PET-MRI registration,
reference-region and invasive kinetic modeling, partial volume
correction (PVC), MRI distortion correction, group analysis in ROI,
volume, and surface spaces, and correction for multiple
comparisons. The Brain Imaging Data Structure (BIDS) is a simple and
intuitive way to organize and describe neuroimaging data; many
researchers make their data available to the community in BIDS format
via OpenNeuro. Software developers have exploited the BIDS structure
to write analysis routines that seamlessly traverse through a BIDS
tree, analyzing each data set found, and storing the result in BIDS
format so that other applications can provide further
analysis. PETsurfer-BIDS is a BIDS app built on top of petprep
(https://github.com/nipreps/petprep) and bloodstream
(https://github.com/mathesong/bloodstream). PETsurfer functionality is
divided between petsurfer-bids and petprep. petprep runs the
pre-processing aspects (MC, PET-MRI registration, PVC, sampling to
template spaces) whereas petsurfer-bids runs voxel-wise smoothing,
kinetic modeling, and group analysis. If the kinetic modeling is
invasive, then bloodstream can be run to create the arterial input
function (AIF) used by PETsurfer kinetic modeling. The ultimate goal
of this project is to allow users to put their PET data into BIDS
format (or download PET data from OpenNeuro) and then provide
end-to-end PET analysis through BIDS apps.


PETsurfer wiki https://surfer.nmr.mgh.harvard.edu/fswiki/PetSurfer

.. contents::
   :local:
   :depth: 2


Key capabilities
----------------

* **Integrated MRI-PET workflow**: registration, motion correction,
  ROI/volume/surface analysis, MRI gradient distortion correction.
* **Kinetic modeling (KM)**: MRTM1, MRTM2, and invasive Logan modeling,
  including MA1.
* **Partial volume correction (PVC)** methods: Symmetric GTM (SGTM), two-compartment (Meltzer),
  three-compartment (Müller-Gärtner / MG), and RBV; implementations also account for
  tissue fraction effect (TFE).
* **Three-space approach** for voxel-wise work: cortical and subcortical GM analyzed separately
  (LH cortex, RH cortex, subcortical GM) to leverage surface-based operations.

Development
--------------------

- Setup a [FreeSurfer 8.1.0+ environment](https://surfer.nmr.mgh.harvard.edu/fswiki/DownloadAndInstall)
- Setup a python 3.8+ environment.  For example, using [miniforge](https://conda-forge.org/download/)

```
conda create -n petsurfer-bids python=3.8 -y
conda activate petsurfer-bids
git clone git@github.com:freesurfer/petsurfer-bids.git
cd petsurfer-bids
pip install -e .
```

Recommended citation
--------------------

If you use PETSurfer, please cite:

* Greve, D.N., et al. (2014). *Cortical surface-based analysis reduces bias and variance in kinetic modeling of brain PET data.* NeuroImage, 92, 225?236.
* Greve, D.N., et al. (2016). *Different partial volume correction methods lead to different conclusions: An 18F-FDG-PET study of aging.* NeuroImage, 132, 334?343.


