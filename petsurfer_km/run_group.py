"""Main entry point for petsurfer-km-group CLI."""

from __future__ import annotations

# Use pysqlite3 as a drop-in replacement for sqlite3 if available.
# This is needed for FreeSurfer's fspython, which lacks the _sqlite3 C extension.
# pysqlite3-binary only has wheels for Linux x86-64
#
# Be sure to run `fspython -m pip install pysqlite3-binary` if running from
# inside fspython
try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass

import argparse
import logging
import os
import shutil
import sys
import subprocess
from pathlib import Path
import csv

import petsurfer_km
from petsurfer_km import __version__
from petsurfer_km import bidsfsgd
from petsurfer_km.cli.parser import build_parser
from petsurfer_km.inputs import InputGroup, discover_inputs
from petsurfer_km.steps.participant import (
    run_bidsify,
    run_kinetic_modeling,
    run_preprocessing,
    run_report,
    run_surface,
    run_volumetric,
)
from bids import BIDSLayout

logger = logging.getLogger("petsurfer_km")

def setup_logging(level: str) -> None:
    """
    Configure logging for petsurfer-km-group

    Args:
        level: Log level string ('error', 'warn', 'info', or 'debug')
    """
    level_map = {
        "error": logging.ERROR,
        "warn": logging.WARNING,
        "info": logging.INFO,
        "debug": logging.DEBUG,
    }
    log_level = level_map.get(level, logging.WARNING)

    # Configure format based on level
    if log_level == logging.DEBUG:
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    else:
        fmt = "%(levelname)s: %(message)s"

    logging.basicConfig(
        level=log_level,
        format=fmt,
        handlers=[logging.StreamHandler(sys.stderr)],
    )


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """
    Validate parsed arguments for consistency.

    Args:
        args: Parsed arguments namespace.
        parser: ArgumentParser instance for error reporting.
    """
    # Check that tstar is provided for Logan methods
    logan_methods = {"logan", "logan-ma1"}
    selected_logan = logan_methods.intersection(args.km_method)
    if selected_logan and args.tstar is None:
        parser.error(
            f"--tstar is required when using {', '.join(sorted(selected_logan))} method(s)"
        )

    # Cannot disable both volumetric and surface analysis
    if args.no_vol and args.no_surf:
        parser.error(
            "Cannot disable both volumetric (--no-vol) and surface (--no-surf) analysis"
        )

    # Check bloodstream-dir exists for Logan methods (uses arterial input function)
    if selected_logan:
        bloodstream_dir = args.bloodstream_dir
        if bloodstream_dir is None:
            bloodstream_dir = args.bids_dir / "derivatives" / "bloodstream"
        if not bloodstream_dir.exists():
            parser.error(
                f"bloodstream-dir does not exist: {bloodstream_dir}\n"
                f"Logan methods require arterial input function data from bloodstream."
            )

    # Check petprep-dir exists
    petprep_dir = args.petprep_dir
    if petprep_dir is None:
        petprep_dir = args.bids_dir / "derivatives" / "petprep"
    if not petprep_dir.exists():
        parser.error(
            f"petprep-dir does not exist: {petprep_dir}\n"
            f"Please run petprep first or specify --petprep-dir."
        )


def set_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Set default values that depend on other arguments."""
    # Set default petprep-dir
    if args.petprep_dir is None:
        args.petprep_dir = args.bids_dir / "derivatives" / "petprep"

    # Set default bloodstream-dir
    if args.bloodstream_dir is None:
        args.bloodstream_dir = args.bids_dir / "derivatives" / "bloodstream"

    # Set default work-dir
    if args.work_dir is None:
        args.work_dir = Path(f"/tmp/petsurfer-km-{os.getpid()}")

    # Handle hemisphere selection
    if not args.lh and not args.rh:
        # Default: process both hemispheres
        args.hemispheres = ["lh", "rh"]
    else:
        args.hemispheres = []
        if args.lh:
            args.hemispheres.append("lh")
        if args.rh:
            args.hemispheres.append("rh")

    return args


def ensure_fsaverage() -> None:
    """
    Set SUBJECTS_DIR to $FREESURFER/subjects to ensure fsaverage exists

    Raises:
        RuntimeError: If FREESURFER_HOME is not set
    """

    freesurfer_home = os.environ.get("FREESURFER_HOME")
    if not freesurfer_home:
        raise RuntimeError(f"FREESURFER_HOME is not set. Cannot locate fsaverage.")

    subjects_dir = Path(freesurfer_home) / "subjects"
    os.environ.get("SUBJECTS_DIR",subjects_dir)

def run(args: argparse.Namespace) -> int:
    """
    Execute petsurfer-km processing.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    logger.info(f"petsurfer_km_group version: {__version__}")
    logger.debug(f"BIDS directory: {args.bids_dir}")
    logger.debug(f"Output directory: {args.output_dir}")
    logger.debug(f"Kinetic methods: {args.km_method}")
    logger.debug(f"PetPrep directory: {args.petprep_dir}")
    logger.debug(f"Work directory: {args.work_dir}")
    if args.session_label:
        logger.debug(f"Sessions: {args.session_label}")
    logger.debug(f"Volumetric FWHM: {args.vol_fwhm} mm")
    logger.debug(f"Surface FWHM: {args.surf_fwhm} mm")
    logger.debug(f"Hemispheres: {args.hemispheres}")
    logger.debug(f"Skip volumetric: {args.no_vol}")
    logger.debug(f"Skip surface: {args.no_surf}")

    # Create working directory
    args.work_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Created working directory: {args.work_dir}")

    # Ensure fsaverage is available under SUBJECTS_DIR
    ensure_fsaverage()

    # Track processing results
    failed_subjects: list[str] = []
    successful_subjects: list[str] = []

    # Process each subject/session
    for input_group in input_groups:
        try:
            process_subject(input_group, args)
            successful_subjects.append(input_group.label)
        except Exception as e:
            failed_subjects.append(input_group.label)
            logger.error(f"Failed to process {input_group.label}: {e}")
            if args.abort_on_error:
                logger.error("Aborting due to --abort-on-error")
                break

    # Log summary
    if successful_subjects:
        logger.info(f"Successfully processed: {', '.join(successful_subjects)}")
    if failed_subjects:
        logger.error(f"Failed to process: {', '.join(failed_subjects)}")

    # Cleanup
    if args.nocleanup:
        logger.info(f"Skipping cleanup (--nocleanup). Working directory: {args.work_dir}")
    else:
        logger.info(f"Cleaning up working directory: {args.work_dir}")
        try:
            shutil.rmtree(args.work_dir)
        except Exception as e:
            logger.warning(f"Failed to clean up working directory: {e}")

    # Return exit code
    if failed_subjects:
        return 1
    return 0


class PETsurferGroup:
    def __init__(self,args):
        self.bidsdir = args.bids_dir
        self.outdir = args.output_dir
        self.subjects = None
        self.fsgdfile = args.fsgd;
        self.fsgd = None;
        self.tsv = args.tsv;
        if(args.ses is None and args.paired is None):
            self.sessions = ['baseline'];
        else:
            if(args.ses is not None): self.sessions = [args.ses];
            else:                     self.sessions = args.paired;
        self.paired = 0
        if(args.paired is not None): self.paired = 1
        self.cmc = args.cmc;
        self.spaces = args.space;
        self.tracer = args.tracer;
        self.km = args.km;
        if(self.km in ["MRTM1", "MRTM2"]): self.meas = "BPND";
        if(self.km in ["Logan", "MA1"]):   self.meas = "VT";
        self.volfwhm = args.vol_fwhm
        if(args.vol_fwhm  is None): self.volfwhm = args.fwhm
        self.surffwhm = args.surf_fwhm
        if(args.surf_fwhm is None): self.surffwhm = args.fwhm
        pkgdir = os.path.dirname(petsurfer_km.__file__)
        pskmconfig = os.path.join(pkgdir, "petsurfer-km-bids-config.json")
        self.layout = BIDSLayout(self.bidsdir,validate=False,config=["bids", "derivatives", pskmconfig]);

    def get_subjects(self,space=None):
        # Get list of subjects (sets self.subjects), either all or
        # from an FSGD file. To do: get from a participants.tsv. This
        # function also determines some other useful strings which are
        # passed back to the caller
        if(space is None): space = self.spaces[0];
        hemi = None;
        suffix="mimap",
        extension=".nii.gz",
        meas = self.meas;
        if(space == "fsaverage-lh"):
            stack = Path(self.outdir,space+".nii.gz")
            bidsspacename = "fsaverage"
            hemi = "L";
        if(space == "fsaverage-rh"):
            stack = Path(self.outdir,space+".nii.gz")
            bidsspacename = "fsaverage"
            hemi = "R";
        if(space == "mni"):
            bidsspacename = "MNI152NLin2009cAsym"
            stack = Path(self.outdir,space+".nii.gz")
        if(space == "ROI"):
            stack = Path(self.outdir,"roi.csv")
            bidsspacename = None; # BIDS naming does not include a space for ROI tables
            suffix="kinpar"
            meas=None;
            extension=".tsv"
        if(self.fsgdfile is None and self.tsv is None):
            print("Getting all subjects");
            self.subjects = self.layout.get(target="subject",session=self.sessions[0],datatype="pet",
                                tracer=self.tracer,hemi=hemi,space=bidsspacename,model=self.km,
                                meas=meas,suffix=suffix,extension=extension,return_type="id")
        else:
            self.fsgd = bidsfsgd.BIDS_FSGD(self.fsgdfile);
            self.subjects = self.fsgd.df["subject_id"].tolist()
            #sessions = gd.df["ses"].tolist(); # ignore for now

        return bidsspacename,hemi,suffix,extension,meas,stack;

    def tsv2glmfit(self,tsvlist,outtable,participant_ids=None):
        # This is for ROI/table analysis to merge tsv files from
        # multiple subjects into something that mri_glmfit can
        # ingest. The tsv file for each subject is assumed to have (at
        # least) two columns, the first column is the ROI name and the
        # second is the value of interest. If participant_ids is passed,
        # then the subjectname is put as the first column.
        #
        # Subjects may have different ROI sets (e.g. non-WM-hypointensities
        # is present in some but not all). Values are aligned by ROI name
        # (not by row position) and missing ROIs are filled with NaN so
        # every row has the same number of columns. ROIs not present in
        # all subjects (any column containing NaN) are then pruned before
        # the table is emitted. Remove the pruning step to allow NaNs into
        # mri_glmfit instead.
        if(participant_ids is not None and len(tsvlist) != len(participant_ids)):
            print("ERORR: tsvlist length != subject list length");
            print(len(tsvlist))
            print(len(participant_ids))
            return

        # First pass: read all TSVs into per-subject dicts and collect
        # the union of ROI names in first-appearance order.
        subj_data = []   # list of (subject_id, {roi_name: value})
        all_roinames = []  # union of ROI names, first-appearance order
        seen_rois = set()
        for k,tsvfile in enumerate(tsvlist):
            filename, file_extension = os.path.splitext(tsvfile);
            if(file_extension == ".csv"): delimiter=",";
            if(file_extension == ".tsv"): delimiter="\t";
            tsv = csv.reader(open(tsvfile, "r"), delimiter=delimiter, quotechar='"')
            if(participant_ids is not None): subj_id = participant_ids[k];
            else:                            subj_id = f"s{k}";
            roi_dict = {}
            for row in tsv:
                if not row or row[0] == "ROI":  # skip header row (issue #2)
                    continue
                roi_dict[row[0]] = row[1]
                if row[0] not in seen_rois:
                    seen_rois.add(row[0])
                    all_roinames.append(row[0])
            subj_data.append((subj_id, roi_dict))

        # Second pass: build aligned table, NaN for missing ROIs.
        roitable = []
        for subj_id, roi_dict in subj_data:
            roivals = [subj_id] + [roi_dict.get(rn, "NaN") for rn in all_roinames]
            roitable.append(roivals)

        # Prune ROIs (columns) not present in all subjects: keep only the
        # intersection of ROI sets. Drop any ROI column containing a NaN.
        keep = [i for i in range(len(all_roinames))
                if all(row[i+1] != "NaN" for row in roitable)]
        roinames = ["Subject"] + [all_roinames[i] for i in keep]
        roitable = [[row[0]] + [row[i+1] for i in keep] for row in roitable]

        # Note: can't pass .tsv file to mri_glmfit because it thinks
        # it is a tac file. As a hack, have to call it csv but really
        # putting tabs as the separator. This is ugly.
        with open(outtable, mode='w') as fp:
            writer = csv.writer(fp, delimiter='\t');
            writer.writerow(roinames)
            writer.writerows(roitable)

        return

    def analyze_space(self,space=None):
        if(space is None): space = self.spaces[0];
        # Set self.subjects and get useful strings
        bidsspacename,hemi,suffix,extension,meas,stack = self.get_subjects(space);

        # Get list of files and concat them together into a stack. Order is important
        # if doing something more than cross OSGM. For paired, it must be ses1 then ses2
        # for each subject.
        flist = [];
        for sub in self.subjects: # Loop over subjects
            for ses in self.sessions: #Loop over sessions (if longitudinal)
                flist0 = self.layout.get(subject=sub,session=ses,datatype="pet",
                                tracer=self.tracer,hemi=hemi,space=bidsspacename,model=self.km,
                                meas=meas,suffix=suffix,extension=extension,return_type="filename")
                if(len(flist0)==0):
                    print(f"ERROR: cannot find file for {sub} {ses}")
                    sys.exit(1);
                flist.append(flist0[0]);
        self.outdir.mkdir(parents=True, exist_ok=True)
        if(space != "ROI"): # Voxel-wise space (ie, not ROI)
            flistflat = " ".join(flist);
            cmd = f"mri_concat --o {stack} {flistflat}"
            if(self.paired): cmd = cmd + " --paired-diff"
            print(cmd)
            try:
                result = subprocess.run(cmd.split(), check=True, capture_output=True, text=True)
                print(result.stdout)
            except subprocess.CalledProcessError as e:
                print("Command failed with exit code", e.returncode)
                print(e.stdout)
                raise RuntimeError()
        else:
            self.tsv2glmfit(flist,stack,self.subjects)

        # Run GLM
        glmdir = Path(self.outdir,"glm."+space)
        cmd = f"mri_glmfit --o {glmdir} --nii.gz"
        if(space != "ROI"): cmd = f"{cmd} --y {stack} --eres-save"
        else:               cmd = f"{cmd} --table {stack}"
        if(self.fsgdfile is None): cmd = f"{cmd} --osgm"
        else:                      cmd = f"{cmd} --fsgd {self.fsgdfile}"
        if(space == "fsaverage-lh"): cmd = f"{cmd} --surf fsaverage lh"
        if(space == "fsaverage-rh"): cmd = f"{cmd} --surf fsaverage rh"
        print(cmd)
        try:
            result = subprocess.run(cmd.split(), check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            print("Command failed with exit code", e.returncode)
            print(e.stdout)
            raise RuntimeError()
        
        # Run Corrections for Multiple Comparisons
        # --cmc (0) CFT (1) nperm (2) abs/pos/neg (3) nspaces (4) CWP"))
        if(self.cmc is not None and space != "ROI"):
            cmd = f"mri_glmfit-sim --glmdir {glmdir} --cwp {self.cmc[4]} --perm {self.cmc[0]} {self.cmc[1]} {self.cmc[2]}"
            nspaces = int(self.cmc[3])
            if(nspaces > 1): cmd = f"{cmd} --{nspaces}spaces"
            print(cmd)
            try:
                result = subprocess.run(cmd.split(), check=True, capture_output=True, text=True)
                print(result.stdout)
            except subprocess.CalledProcessError as e:
                print("Command failed with exit code", e.returncode)
                print(e.stdout)
                raise RuntimeError()

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    Parse and validate command-line arguments.
    Args: argv: Command-line arguments (defaults to sys.argv[1:])
    Returns: Validated namespace of arguments.
    Raises: SystemExit: On parsing or validation errors.
    """
    parser = argparse.ArgumentParser(
        prog="petsurfer-km-group",
        description=("BIDS App to perform group analysis on PET kinetic model output of PetSurfer")
    )
    parser.add_argument("bids_dir",type=Path,help="Root directory of the BIDS dataset.");
    parser.add_argument("output_dir",type=Path,help="Output directory for results.");
    parser.add_argument("--km",
        choices=["MRTM1", "MRTM2", "Logan", "MA1"],
        help=("Kinetic modeling method to get data from. Only one method can be specified. "
            "MRTM1, MRTM2, Logan, MA1. "))
    parser.add_argument('--ses',help="Session label.")
    parser.add_argument('--tracer',default="11CPS13",help="Tracer to analyze (eg,11CPS13).")
    parser.add_argument("--space",nargs="+",choices=["fsaverage-lh","fsaverage-rh", "mni","ROI"],help=("Group space(s)."))
    parser.add_argument('--fwhm',help="volume and surface FWHM when running the kinetic modeling.")
    parser.add_argument('--surf-fwhm',help="surface FWHM when running the kinetic modeling.")
    parser.add_argument('--vol-fwhm',help="volume FWHM when running the kinetic modeling.")
    parser.add_argument("--paired",nargs=2,help=("Do a paired longitudinal analysis."))
    parser.add_argument("--fsgd",help=("FSGD file to specify the subjects/sessions"))
    parser.add_argument("--tsv",help=("list of subjects as in participants.tsv"))
    parser.add_argument("--cmc",nargs=5,help=("Correction for multiple comparisons: CFT nperm abs/pos/neg nspaces CWP"))

    args = parser.parse_args(argv)

    print(f"bids_dir {args.bids_dir}")
    print(f"output_dir {args.output_dir}")
    print(f"km {args.km}")
    print(f"ses {args.ses}")
    print(f"tracer {args.tracer}")
    print(f"space {args.space}")
    print(f"fwhm {args.fwhm}")
    print(f"sfwhm {args.surf_fwhm}")
    print(f"vfwhm {args.vol_fwhm}")
    print(f"paired {args.paired}")

    if(args.ses is not None and args.paired is not None):
        print("ERROR: cannot spec --sess and --paired")
        sys.exit(1);

    #args = set_defaults(args)
    #validate_args(args, parser)
    return args

def main(argv: list[str] | None = None) -> None:
    """
    Main entry point for petsurfer-km-group CLI.
    Args: argv: Command-line arguments (defaults to sys.argv[1:])
    """
    args = parse_args(argv)
    psg = PETsurferGroup(args);
    psg.get_subjects();
    print(psg.subjects);
    for space in args.space:
        print(f"{space}---------------------");
        psg.analyze_space(space);
    print("petsurfer-bids run_group done");
    sys.exit(0);


if __name__ == "__main__":
    main()
