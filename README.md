# PETsurfer-BIDS

## Table of Contents

- [Overview](#overview)
- [Usage](#usage)
  - [Installation](#installation)
  - [Participant-level analysis](#participant-level-analysis)
  - [Group-level analysis](#group-level-analysis)
- [Key capabilities](#key-capabilities)
- [Development](#development)
- [Recommended citation](#recommended-citation)

## Overview

- [Example participant-level HTML report](https://freesurfer.github.io/petsurfer-bids/participant/sub-PS11_ses-baseline.html)
- [Example participant-level HTML visualization](https://freesurfer.github.io/freebrowse/?nvd=https://raw.githubusercontent.com/pwighton/freebrowse-test-data/refs/heads/main/pet/petsurfer-bids/petsurfer-km.nvd)

This project is an encapsulation of the functionality in PETsurfer
within an easy-to-use BIDS app.  PETSurfer is a set of tools within
the FreeSurfer environment for end-to-end integrated MRI-PET
analysis, including: 
- motion correction (MC)
- PET-MRI registration
- reference-region and invasive kinetic modeling
- partial volume correction (PVC)
- MRI distortion correction
- group analysis in ROI, volume, and surface spaces
- correction for multiple comparisons.

The Brain Imaging Data Structure (BIDS) is a simple and
intuitive way to organize and describe neuroimaging data; many
researchers make their data available to the community in BIDS format
via [OpenNeuro](https://openneuro.org/). Software developers have created BIDS
apps which exploit the BIDS structure to write analysis routines that
seamlessly traverse through a BIDS tree, analyzing each data point found, and
storing the result in BIDS format so that other applications can provide further
analysis. 

PETsurfer-BIDS is a BIDS app that builds upon the outputs of
[PETPrep](https://github.com/nipreps/petprep) and
[bloodstream](https://github.com/mathesong/bloodstream). 

PETsurfer functionality is divided between petsurfer-bids and PETPrep. PETPrep runs the
pre-processing aspects (MC, PET-MRI registration, PVC, sampling to
template spaces) whereas petsurfer-bids runs voxel-wise smoothing,
kinetic modeling, and group analysis. If the kinetic modeling is
invasive, then bloodstream can be run to create the arterial input
function (AIF) used by PETsurfer kinetic modeling. The ultimate goal
of this project is to allow users to put their PET data into BIDS
format (or download PET data from OpenNeuro) and then provide
end-to-end PET analysis through BIDS apps.

See the [PETsurfer wiki](https://surfer.nmr.mgh.harvard.edu/fswiki/PetSurfer)
for more information.

## Key capabilities

* **Integrated MRI-PET workflow**: registration, motion correction,
  ROI/volume/surface analysis, MRI gradient distortion correction.
* **Kinetic modeling (KM)**: MRTM1, MRTM2, invasive Logan modeling
  (including MA1), and invasive Patlak modeling.
* **Partial volume correction (PVC)** methods: Symmetric GTM (SGTM), two-compartment (Meltzer),
  three-compartment (Muller-Gartner / MG), and RBV; implementations also account for
  tissue fraction effect (TFE).
* **Three-space approach** for voxel-wise work: cortical and subcortical GM analyzed separately
  (LH cortex, RH cortex, subcortical GM) to leverage surface-based operations.

## Usage

PETsurfer-BIDS can perform both participant-level analyses and group-level
analyses.  It is invoked via `petsurfer-km` and conforms to the BIDS app interface.

```
petsurfer-km bids_dir output_dir {participant,group}
```

### Installation

Like all other BIDS apps, PETsurfer-BIDS is intended to be run inside a 
*container* (i.e. [docker](https://www.docker.com/),
[apptainer](https://apptainer.org/), or [singularity](https://sylabs.io/singularity/))

The container registry is located [here](https://hub.docker.com/r/freesurfer/petsurfer-bids/tags)

You can pull `v0.1.3` of the container:

**With Docker**:
```
docker pull freesurfer/petsurfer-bids:0.1.3
```

**With Apptainer**:
```
apptainer pull petsurfer-bids-0.1.3.sif docker://freesurfer/petsurfer-bids:0.1.3
```

**With Singularity CE**:
```
singularity pull petsurfer-bids-0.1.3.sif docker://freesurfer/petsurfer-bids:0.1.3
```

When running PETsurfer-BIDS inside a container, you will need to *bind mount*
relevant input and output paths.  At a minimum, you will need to *bind mount*:
- The input data directory (`bids_dir`)
- The output data directory (`output_dir`)
- The location of the FreeSurfer license file, which should be mounted to
  `/usr/local/freesurfer/8.1.0-1/.license` inside the container.  If you don't
  have a FreeSurfer license file, you can apply for and receive one immediately
  [here](https://surfer.nmr.mgh.harvard.edu/registration.html).

If you would like to preserve the temporary work directory, you can bind mount 
a directory to that and use `--work-dir` to point to the location inside the
container.

Refer to the documentation for your container platform
([Docker](https://docs.docker.com), [Singularity/SingularityCE](https://docs.sylabs.io),
or [Apptainer](https://apptainer.org/docs)) for more information.

PETsurfer-BIDS can also be run locally by installing and configuring a
FreeSurfer and python environment.  See [Development](#development) for
instructions.

### Participant-level analysis

**TL;DR**: run `petsurfer-km --help` for usage instructions and point it to
PETprep data that has been run using `--output-spaces MNI152NLin2009cAsym fsaverage`.

PETsurfer-BIDS currently supports the following kinetic models:
- MRTM1
- MRTM2
- Logan
- Logan-MA1
- Patlak

Multiple models can be executed simultaneously and can be specified using the
`--km-method` flag.

MRTM2 relies on the rate constant of the reference region (`k2prime`) estimated
in MRTM1. Specifying MRTM2 automatically includes MRTM1.

For each kinetic model specified, the following analyses will be performed:
- ROI based
- Volumetric (voxel) based
- Surface based

The volumetic analysis can be disabled by specifying `--no-vol`.  The surface
based analysis can be disabled by specifying `--no-surf`.  The flags `--lh` and
`--rh` can be used to run only the left or right hemisphere of the surface
based analysis.

All kinetic models rely on the outputs of [PETPrep](https://github.com/nipreps/petprep).
Volumetric analyses are performed in `MNI152NLin2009cAsym` space and
surface-based analyses are performed in `fsaverage` space.  PETPrep must therefore
produce outputs in these spaces.  To be able to perform a full
PETsurfer-BIDS analysis, including volumetric and surface based analyses, be
sure to include the following flag when running PETPrep:

```
--output-spaces MNI152NLin2009cAsym fsaverage
```

Refer to the  [PETPrep documentation](https://petprep.readthedocs.io/en/latest/)
for further details.

The location of the PETPrep output directory is by default assumed to be
`<bids_dir>/derivatives/petprep`, however the location can be specified using the
`--petprep-dir` flag.

For MRTM1 modelling, a comma separated list of reference regions can be
specified using the `--mrtm1-ref` flag
(default: `Left-Cerebellum-Cortex,Right-Cerebellum-Cortex`). For MRTM2
modelling, a comma separated list of high-binding regions can be specified
using the `--mrtm2-hb` flag (default: `Left-Putamen,Right-Putamen`). For both
the `--mrtm1-ref` and `--mrtm2-hb` flags, the region names provided should
correspond to column heading names in the `*_tacs.tsv` outputs from PETPrep.

For Logan, Logan-MA1, and Patlak modelling, the time to equilibration (t*)
must be supplied in seconds using the `--tstar` flag.

For Logan, Logan-MA1, and Patlak modelling, PETsurfer-BIDS also relies on the
outputs of [bloodstream](https://github.com/mathesong/bloodstream).  The
location of the bloodstream output directory is by default assumed to be
`<bids_dir>/derivatives/bloodstream`, however the location can be specified
using the `--bloodstream-dir` flag.

The `--pvc` flag selects which partial-volume-corrected output from PETPrep to
use as input. Its value must match the `--pvc-method` value that was passed to
PETPrep during preprocessing.

Smoothing is specified as the full-width at half-maximum (FWHM) of a Gaussian
kernel in mm: `--vol-fwhm` for volumetric analysis and `--surf-fwhm` for
surface-based analysis.

By default, all subject/session pairs found under `bids_dir` are processed.
To limit what gets processed, pass a space-separated list to
`--participant-label` and/or `--session-label`.

PETsurfer-BIDS will create and use the folder `/tmp/petsurfer-km-<pid>` to store
temporary files while processing.  This folder is deleted when PETsurfer-BIDS
terminates.  To preserve this folder, use the `--nocleanup` flag.  To specify an
alternate temporary folder, use the `--work-dir` flag.

**Outputs**

For each subject/session/kinetic-model combination, the following outputs will
be generated and placed in the `<output_dir>/sub-<subid>/ses-<sessid>/pet/`
directory:
- `*_mimap.[json|nii.gz]`: A molecular imaging map of VT (`meas-VT`; for Logan and Logan-MA1 models),
BPND (`meas-BPND`; for MRTM1 or MRTM2 models), or Ki (`meas-Ki`; for the Patlak model) in:
  - `MNI152NLin2009cAsym` space
  - `fsaverage` space, left (`hemi-L`) and right (`hemi-R`) hemispheres
- `*_kinpar.[json|tsv]`: model parameters averaged across the ROIs defined by PETprep

An html report is also generated for each subject/session pair and placed in the 
`<output_dir>/sub-<subid>/` folder.  Figures that this html report references are
also placed in the `<output_dir>/sub-<subid>/ses-<sessid>/figures/` folder.

By default, PETsurfer-BIDS will also generate browser-based visualizations for
each volumetric analysis performed (visualizations for surface-based analyses 
are coming soon!) using [FreeBrowse](https://freesurfer.github.io/freebrowse/). These are standalone HTML files that contain all of the code to render the visualization as well as the data it visualized.
See the [single HTML file build](https://github.com/freesurfer/freebrowse?tab=readme-ov-file#single-html-file)
of FreeBrowse for further information.  **No data, imaging or otherwise, leaves
your device when viewing these visualizations**.  The generation of FreeBrowse-based
visualizations can be disabled with the `--no-freebrowse` flag.

- [Example participant-level HTML report](https://freesurfer.github.io/petsurfer-bids/participant/sub-PS11_ses-baseline.html)
  - The `View results in freebrowse` links in this HTML report will not work because the data it references have not been uploaded to conserve space.  However the link below is an example of one such visualization.
- [Example participant-level HTML visualization](https://freesurfer.github.io/freebrowse/?nvd=https://raw.githubusercontent.com/pwighton/freebrowse-test-data/refs/heads/main/pet/petsurfer-bids/petsurfer-km.nvd)

**Example**

The following command will process the [`ds004230`](https://openneuro.org/datasets/ds004230)
dataset using the Logan-MA1 model in apptainer.

It assumes:
- The petsurfer-bids container is located at `~/containers/petsurfer-bids-0.1.3.sif`
  - The container can be downloaded using: `apptainer pull ~/containers/petsurfer-bids-0.1.3.sif docker://freesurfer/petsurfer-bids:0.1.3`
- The `ds004230` dataset has been downloaded to `~/datasets/ds004230`
- The output will be written to `~/datasets/petsurfer-bids/ds004230`
- PETPrep has been run on this dataset with `--output-spaces MNI152NLin2009cAsym fsaverage`
  and the output is located at `~/datasets/petprep/ds004230`
- bloodstream has been run on this dataset and the output is located at `~/datasets/bloodstream/ds004230`
- A FreeSurfer license file is located at `~/freesurfer/license.txt`
- The time to equilibration (t*) is 540 seconds

```
apptainer run \
  -B ~/datasets/ds004230:/data/input:ro \
  -B ~/datasets/petsurfer-bids/ds004230:/data/output \
  -B ~/freesurfer/license.txt:/usr/local/freesurfer/8.1.0-1/.license:ro \
  ~/containers/petsurfer-bids-0.1.3.sif \
    petsurfer-km /data/input /data/output participant \
      --km-method logan-ma1 \
      --tstar 540 \
```

### Group-level analysis

TODO

## Development

- Setup a [FreeSurfer 8.1.0+ environment](https://surfer.nmr.mgh.harvard.edu/fswiki/DownloadAndInstall)
- Setup a python 3.8+ environment.  For example, using [miniforge](https://conda-forge.org/download/)

```
conda create -n petsurfer-bids python=3.8 -y
conda activate petsurfer-bids
git clone git@github.com:freesurfer/petsurfer-bids.git
cd petsurfer-bids
pip install -e .
```

When making changes, please bump the vesion number in [`petsurfer_km/__init__.py`](petsurfer_km/__init__.py)

## Recommended citation

If you use PETSurfer, please cite:

- Greve, D.N., et al. (2014). *Cortical surface-based analysis reduces bias and variance in kinetic modeling of brain PET data.* NeuroImage, 92, 225-236.
- Greve, D.N., et al. (2016). *Different partial volume correction methods lead to different conclusions: An 18F-FDG-PET study of aging.* NeuroImage, 132, 334-343.
