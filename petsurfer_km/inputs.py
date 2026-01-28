"""Input file discovery and grouping using pybids."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from bids import BIDSLayout

logger = logging.getLogger("petsurfer_km")


@dataclass
class InputGroup:
    """A group of input files for a single subject/session."""

    subject: str
    session: str | None

    # PETPrep outputs
    pet_mni: Path | None = None  # Volumetric PET in MNI space
    pet_fsnative_lh: Path | None = None  # Surface PET, left hemisphere, native
    pet_fsnative_rh: Path | None = None  # Surface PET, right hemisphere, native
    pet_fsaverage_lh: Path | None = None  # Surface PET, left hemisphere, fsaverage
    pet_fsaverage_rh: Path | None = None  # Surface PET, right hemisphere, fsaverage
    tacs: Path | None = None  # Tissue activity curves (GTM)

    # Bloodstream outputs
    input_function: Path | None = None  # Arterial input function

    # Tracking missing files
    missing: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """Return a label for this group (sub-XX[_ses-YY])."""
        if self.session:
            return f"sub-{self.subject}_ses-{self.session}"
        return f"sub-{self.subject}"

    def has_volumetric(self) -> bool:
        """Check if volumetric inputs are available."""
        return self.pet_mni is not None

    def has_surface(self, hemisphere: str | None = None) -> bool:
        """Check if surface inputs are available for the given hemisphere."""
        if hemisphere == "lh":
            return self.pet_fsaverage_lh is not None
        elif hemisphere == "rh":
            return self.pet_fsaverage_rh is not None
        else:
            return self.pet_fsaverage_lh is not None or self.pet_fsaverage_rh is not None

    def has_input_function(self) -> bool:
        """Check if arterial input function is available."""
        return self.input_function is not None

    def is_valid(self, require_input_function: bool = False) -> bool:
        """Check if this group has minimum required inputs."""
        has_pet = self.has_volumetric() or self.has_surface()
        if require_input_function:
            return has_pet and self.has_input_function()
        return has_pet


def _find_petprep_files(
    layout: BIDSLayout,
    subject: str,
    session: str | None,
) -> dict:
    """Find PETPrep output files for a subject/session."""
    base_query = {"subject": subject}
    if session:
        base_query["session"] = session

    files = {
        "pet_mni": None,
        "pet_fsnative_lh": None,
        "pet_fsnative_rh": None,
        "pet_fsaverage_lh": None,
        "pet_fsaverage_rh": None,
        "tacs": None,
    }

    # Find volumetric PET in MNI space
    mni_files = layout.get(
        **base_query,
        extension=[".nii.gz", ".nii"],
        space="MNI152NLin2009cAsym",
        suffix="pet",
        desc="preproc",
        return_type="filename",
    )
    if mni_files:
        files["pet_mni"] = Path(mni_files[0])

    # Find surface PET files (.func.gii extension)
    for hemi, hemi_key in [("L", "lh"), ("R", "rh")]:
        # Native surface
        fsnative = layout.get(
            **base_query,
            extension=".func.gii",
            hemi=hemi,
            space="fsnative",
            suffix="pet",
            return_type="filename",
        )
        if fsnative:
            files[f"pet_fsnative_{hemi_key}"] = Path(fsnative[0])

        # fsaverage surface
        fsaverage = layout.get(
            **base_query,
            extension=".func.gii",
            hemi=hemi,
            space="fsaverage",
            suffix="pet",
            return_type="filename",
        )
        if fsaverage:
            files[f"pet_fsaverage_{hemi_key}"] = Path(fsaverage[0])

    # Find tissue activity curves
    tacs_query = {"subject": subject, "extension": ".tsv"}
    if session:
        tacs_query["session"] = session
    tacs_files = layout.get(
        **tacs_query,
        suffix="tacs",
        return_type="filename",
    )
    if tacs_files:
        files["tacs"] = Path(tacs_files[0])

    return files


def _find_bloodstream_files(
    layout: BIDSLayout,
    subject: str,
    session: str | None,
) -> dict:
    """Find bloodstream output files for a subject/session."""
    files = {"input_function": None}

    query = {"subject": subject, "suffix": "inputfunction", "extension": ".tsv"}
    if session:
        query["session"] = session

    aif_files = layout.get(**query, return_type="filename")
    if aif_files:
        files["input_function"] = Path(aif_files[0])

    return files


def discover_inputs(
    petprep_dir: Path,
    bloodstream_dir: Path | None,
    participant_label: list[str] | None = None,
    session_label: list[str] | None = None,
    require_input_function: bool = False,
) -> list[InputGroup]:
    """
    Discover and group input files from petprep and bloodstream derivatives.

    Args:
        petprep_dir: Path to petprep derivatives directory.
        bloodstream_dir: Path to bloodstream derivatives directory (optional).
        participant_label: List of subjects to include (None = all).
        session_label: List of sessions to include (None = all).
        require_input_function: Whether input function is required (for Logan).

    Returns:
        List of InputGroup objects for valid subject/session combinations.
    """
    logger.debug(f"Loading petprep layout from: {petprep_dir}")
    petprep_layout = BIDSLayout(
        petprep_dir,
        validate=False,
        is_derivative=True,
    )

    bloodstream_layout = None
    if bloodstream_dir and bloodstream_dir.exists():
        logger.debug(f"Loading bloodstream layout from: {bloodstream_dir}")
        # Handle nested structure (bloodstream/Primary_Analysis)
        primary_analysis = bloodstream_dir / "Primary_Analysis"
        if primary_analysis.exists():
            bloodstream_dir = primary_analysis
        try:
            bloodstream_layout = BIDSLayout(
                bloodstream_dir,
                validate=False,
                is_derivative=True,
            )
        except (TypeError, ValueError) as e:
            # Some bloodstream outputs have malformed dataset_description.json
            # Try loading without derivative validation
            logger.warning(
                f"Could not load bloodstream as derivative: {e}. "
                "Trying without derivative validation."
            )
            try:
                bloodstream_layout = BIDSLayout(
                    bloodstream_dir,
                    validate=False,
                    is_derivative=False,
                )
            except Exception as e2:
                logger.warning(f"Could not load bloodstream layout: {e2}")

    # Get list of subjects
    subjects = petprep_layout.get_subjects()
    if participant_label:
        # Filter to requested subjects (handle with/without 'sub-' prefix)
        requested = {s.replace("sub-", "") for s in participant_label}
        subjects = [s for s in subjects if s in requested]
    logger.debug(f"Found {len(subjects)} subjects to process")

    # Get list of sessions
    all_sessions = petprep_layout.get_sessions()
    if not all_sessions:
        all_sessions = [None]  # Dataset without sessions
    elif session_label:
        # Filter to requested sessions
        requested = {s.replace("ses-", "") for s in session_label}
        all_sessions = [s for s in all_sessions if s in requested]
    logger.debug(f"Sessions to process: {all_sessions}")

    groups = []
    for subject in subjects:
        for session in all_sessions:
            group = InputGroup(subject=subject, session=session)

            # Find petprep files
            petprep_files = _find_petprep_files(petprep_layout, subject, session)
            group.pet_mni = petprep_files["pet_mni"]
            group.pet_fsnative_lh = petprep_files["pet_fsnative_lh"]
            group.pet_fsnative_rh = petprep_files["pet_fsnative_rh"]
            group.pet_fsaverage_lh = petprep_files["pet_fsaverage_lh"]
            group.pet_fsaverage_rh = petprep_files["pet_fsaverage_rh"]
            group.tacs = petprep_files["tacs"]

            # Find bloodstream files
            if bloodstream_layout:
                bloodstream_files = _find_bloodstream_files(
                    bloodstream_layout, subject, session
                )
                group.input_function = bloodstream_files["input_function"]

            # Track missing required files
            if not group.pet_mni:
                group.missing.append("volumetric PET (MNI space)")
            if not group.pet_fsaverage_lh:
                group.missing.append("surface PET (fsaverage, left hemisphere)")
            if not group.pet_fsaverage_rh:
                group.missing.append("surface PET (fsaverage, right hemisphere)")
            if require_input_function and not group.input_function:
                group.missing.append("arterial input function")

            # Log status
            if group.is_valid(require_input_function=require_input_function):
                logger.info(f"Valid input group: {group.label}")
                logger.info(f"  Volumetric PET: {group.pet_mni}")
                logger.info(f"  Surface PET (lh): {group.pet_fsaverage_lh}")
                logger.info(f"  Surface PET (rh): {group.pet_fsaverage_rh}")
                if group.input_function:
                    logger.info(f"  Input function: {group.input_function}")
                if group.tacs:
                    logger.info(f"  TACs: {group.tacs}")
                groups.append(group)
            else:
                if group.missing:
                    logger.warning(
                        f"{group.label}: Missing required files: {', '.join(group.missing)}"
                    )

    logger.debug(f"Found {len(groups)} valid input groups")
    return groups
