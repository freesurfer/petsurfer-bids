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

PETsurfer functionality is divided between petsurfer-bids and
PETPrep. PETPrep runs the pre-processing aspects (MC, PET-MRI
registration, PVC, sampling to template spaces) whereas petsurfer-bids
runs voxel-wise smoothing, kinetic modeling, group analysis, and
correction for multple comparisons. If the kinetic modeling is
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
* **Kinetic modeling (KM)**: MRTM1, MRTM2, invasive Logan modeling,
  MA, invasive Patlak modeling.
* **Partial volume correction (PVC)** methods: Symmetric GTM (SGTM), two-compartment (Meltzer),
  three-compartment (Muller-Gartner / MG), and RBV; implementations also account for
  tissue fraction effect (TFE).
* **Three-space approach** for voxel-wise work: cortical and subcortical GM analyzed separately
  (LH cortex, RH cortex, subcortical GM) to leverage surface-based operations.

## Usage

PETsurfer-BIDS can perform both participant-level analyses and
group-level analyses.  After running PETprep and (if needed)
bloodstream, petsurfer-bids can be invoked via BIDS-app-conforming
command-line interface:

```
petsurfer-km bids_dir output_dir {participant,group}
```

### Installation

Like all other BIDS apps, PETsurfer-BIDS is intended to be run inside a 
*container* (i.e. [docker](https://www.docker.com/),
[apptainer](https://apptainer.org/), or [singularity](https://sylabs.io/singularity/))
The container registry is located [here](https://hub.docker.com/r/freesurfer/petsurfer-bids/tags)
You can pull `v0.2.1` of the container:

**With Docker**:
```
docker pull freesurfer/petsurfer-bids:0.2.3
```
**With Apptainer**:
```
apptainer pull petsurfer-bids-0.2.1.sif docker://freesurfer/petsurfer-bids:0.2.3
```
**With Singularity CE**:
```
singularity pull petsurfer-bids-0.2.1.sif docker://freesurfer/petsurfer-bids:0.2.3
```

To avoid having to write a command line for your container, we have created a shell script wrapper.
To get this wrapper, clone this repository with
```
git clone https://github.com/freesurfer/petsurfer-bids
```
There will be a petsurfer-km script file in the scripts folder. Put
that in your path. This is all you need from the repository; you do
not need to install anything. If you are going to do group analysis,
then do the same for petsurfer-km-group. Before using these scripts,
create an environment variable to point to the container:
```
set PETSURFER_SIF /place/where/you/put/petsurfer-bids-0.2.1.sif
export PETSURFER_SIF
```
When you run this script, it will assume that you are using apptainer. If you 
want to use singularity or docker, add --singularity or --docker to the
command line. 


PETsurfer-BIDS can also be run locally by installing and configuring a
FreeSurfer and python environment.  See [Development](#development)
for instructions. Note that the python script is also called
petsurfer-km, so make sure you are running the right one.

### Participant-level analysis

**TL;DR**: run `petsurfer-km --help` for usage instructions and point it to
PETprep data that has been run using `--output-spaces MNI152NLin2009cAsym fsaverage`.

PETsurfer-BIDS currently supports the following kinetic models:
- SUVR
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
- ROI based (except for SUVR)
- Volumetric (voxel) based
- Surface (voxel) based

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

For SUVR and MRTM1 modelling, a comma separated list of reference regions can be
specified using the `--ref-roi` flag
(default: `Left-Cerebellum-Cortex,Right-Cerebellum-Cortex`). Alternatively, one 
can specify a [custom reference region from the output of
petprep](https://petprep.readthedocs.io/en/latest/usage.html#reference-region-masks)
(e.g. `semiovale`) to use as the reference region for SUVR and MRTM1 modelling.
The `--ref-roi-label <labelname>` flag will search the petprep directory for
tacs matching the following format to use as the input for the reference region:

```
sub-<subname>_ses-<sesname>_label-<labelname>_desc-preproc_tacs.tsv
```

For MRTM2 modelling, a comma separated list of high-binding regions can be
specified using the `--mrtm2-hb` flag (default: `Left-Putamen,Right-Putamen`).
For both the `--ref-roi` and `--mrtm2-hb` flags, the region names provided should
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
surface-based analysis. Note that it makes no sense to apply volume-based
smoothing to a data set that you have corrected for partial volume, and
it may make the entire analysis invalid.

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
dataset using the Logan-MA1 model.

It assumes:
- The petsurfer-bids container location is set in the PETSURFER_SIF environment variable
- The `ds004230` dataset has been downloaded to `~/datasets/ds004230`
- The output will be written to `~/datasets/petsurfer-bids/ds004230`
- PETPrep has been run on this dataset with `--output-spaces MNI152NLin2009cAsym fsaverage`
  and the output is located at `~/datasets/petprep/ds004230`
- bloodstream has been run on this dataset and the output is located at `~/datasets/bloodstream/ds004230`
- FreeSurfer is installed in $FREESURFER_HOME and there is a valid license file there
- The time to equilibration (t*) is 540 seconds

```
petsurfer-km ~/datasets/ds004230 ~/datasets/petsurfer-bids/ds004230 participant \
  --km-method logan-ma1 \
  --tstar 540
```

Note: you do NOT need to have FreeSurfer installed locally, but you do
need a license, which you can get from
https://surfer.nmr.mgh.harvard.edu/registration.html.  Once you have
the license, then just add --fs-license /path/to/license on the
command line.

If you want to run your own apptainer command, you can run this:

```
apptainer run \
  -B ~/datasets/ds004230:/data/input:ro \
  -B ~/datasets/petsurfer-bids/ds004230:/data/output \
  -B ~/freesurfer/license.txt:/license.txt:ro \
  --env FS_LICENSE=/license.txt \
  --pwd /data/output \
  ~/containers/petsurfer-bids-0.2.1.sif \
    petsurfer-km /data/input /data/output participant \
      --km-method logan-ma1 \
      --tstar 540 \
```

### Group-level analysis

The group analysis allows the user to make inferences across subject and/or time. Make sure
you have petsurfer-km-group in your path and PETSURFER_SIF defined as described above. Continuing
with the Cox1 example, one can run something like:

```
petsurfer-km-group ~/datasets/petsurfer-bids/ds004230 group.analysis \
  --ses baseline \
  --km MA1 \
  --tracer 11CPS13 \
  --space fsaverage-lh fsaverage-rh mni ROI \
  --vol-fwhm 6 \
  --surf-fwhm 10 \
  --cmc 2 500 abs 2 .05

```

This performs a one-sample group mean (OSGM) on all of the baseline
subjects with tracer 11CPS13 that have the Logan MA1 as performed at
the participant level above. The analyses will be performed in ROI
space, fsaverage left and right hemisphere with surface smoothing of
10mm, and MNI152NLin2009cAsym space with a smoothing of 10mm. The
output will go into a folder called "group.analysis" with
glm.fsaverage-lh, glm.fsaverage-rh, glm.mni, and glm.ROI. Each one of
these folders will have and "osgm" folder, and the map of interest
will be either gamma.nii.gz (the population mean map) or the
sig.nii.gz map (this is a map of the -log10(p-value)).

The --cmc flag is optional. It performs correction for multiple comparisons
on the voxel-wise maps (not the ROI) using permutation. The four options are:
- CFT=2 is the cluster-forming threshold in -log10(p) units, so 2 means voxelwise p<.01
- Npermutations=500 number of permutations
- sign=abs -  abs means absolute (ie, an unsigned test), pos means positive, neg mean negative
- nspaces=2 - if eventually performing test over lh and rh hemisphere (likely) use 2
- FWER=.05 Family-wise error rate. This is the final p-value threshold for clusters. Only report on clusters that are this signficant.



## Development

- Setup a [FreeSurfer 8.2.0+ environment](https://surfer.nmr.mgh.harvard.edu/fswiki/DownloadAndInstall)
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
