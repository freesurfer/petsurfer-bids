"""Tests for petsurfer_km.steps.group.step02_analyze.tsv2glmfit.

tsv2glmfit merges per-subject ROI TSVs into a single table for mri_glmfit.
These tests verify the alignment-by-name and intersection-pruning contract.
"""
import csv

import pytest

from petsurfer_km.steps.group.step02_analyze import tsv2glmfit


def _write_tsv(path, rows):
    """Write rows (list of (name, value) tuples) as a tab-separated file."""
    with open(path, "w") as f:
        for name, val in rows:
            f.write(f"{name}\t{val}\n")


def _read_table(path):
    """Read the merged output table (tab-separated) into a list of lists."""
    with open(path) as f:
        return list(csv.reader(f, delimiter="\t"))


def _run(tsv_files, tmp_path, participant_ids=None):
    """Helper: write inputs, run tsv2glmfit, return parsed output rows."""
    paths = []
    for i, rows in enumerate(tsv_files):
        p = tmp_path / f"subj{i}.tsv"
        with open(p, "w") as f:
            for line in rows:
                f.write(line)
        paths.append(str(p))
    out = str(tmp_path / "merged.csv")
    tsv2glmfit(paths, out, participant_ids)
    return _read_table(out)


class TestRaggedROIIntersection:
    """ROIs not present in all subjects are pruned (intersection approach)."""

    def test_extra_roi_pruned(self, tmp_path):
        rows = _run(
            [
                "r1\t1.0\nr2\t2.0\n",
                "r1\t10.0\nr2\t20.0\nr3\t30.0\n",
            ],
            tmp_path,
            participant_ids=["A", "B"],
        )
        # r3 only in subject B → pruned; only r1, r2 remain
        assert rows[0] == ["Subject", "r1", "r2"]
        assert rows[1] == ["A", "1.0", "2.0"]
        assert rows[2] == ["B", "10.0", "20.0"]
        # No NaN in any cell
        assert all("NaN" not in r for r in rows)

    def test_all_rois_common_kept(self, tmp_path):
        rows = _run(
            [
                "r1\t1.0\nr2\t2.0\nr3\t3.0\n",
                "r1\t10.0\nr2\t20.0\nr3\t30.0\n",
            ],
            tmp_path,
            participant_ids=["A", "B"],
        )
        assert rows[0] == ["Subject", "r1", "r2", "r3"]
        assert rows[1] == ["A", "1.0", "2.0", "3.0"]
        assert rows[2] == ["B", "10.0", "20.0", "30.0"]

    def test_extra_roi_in_first_subject_pruned(self, tmp_path):
        rows = _run(
            [
                "r1\t1.0\nr2\t2.0\nr3\t3.0\n",
                "r1\t10.0\nr2\t20.0\n",
            ],
            tmp_path,
            participant_ids=["A", "B"],
        )
        # r3 only in subject A → pruned
        assert rows[0] == ["Subject", "r1", "r2"]
        assert rows[1] == ["A", "1.0", "2.0"]
        assert rows[2] == ["B", "10.0", "20.0"]
        assert all("NaN" not in r for r in rows)


class TestMisalignedOrder:
    """Same ROI sets in different file order must align by name."""

    def test_reordered_rois(self, tmp_path):
        rows = _run(
            [
                "r1\t1.0\nr2\t2.0\nr3\t3.0\n",
                "r3\t30.0\nr1\t10.0\nr2\t20.0\n",
            ],
            tmp_path,
            participant_ids=["A", "B"],
        )
        header = rows[0]
        assert header == ["Subject", "r1", "r2", "r3"]
        # Build a name→value map for each subject and verify
        for row in rows[1:]:
            vals = dict(zip(header[1:], row[1:]))
            if row[0] == "A":
                assert vals == {"r1": "1.0", "r2": "2.0", "r3": "3.0"}
            elif row[0] == "B":
                assert vals == {"r1": "10.0", "r2": "20.0", "r3": "30.0"}


class TestHeaderSkip:
    """ROI header rows (issue #2 forward compatibility) must not be ingested as data."""

    def test_header_skipped(self, tmp_path):
        rows = _run(
            [
                "ROI\tVT\nr1\t1.0\nr2\t2.0\n",
                "r1\t10.0\nr2\t20.0\n",
            ],
            tmp_path,
            participant_ids=["A", "B"],
        )
        assert rows[0] == ["Subject", "r1", "r2"]
        # No 'ROI' in any data row
        data_rows = rows[1:]
        assert all("ROI" not in r for r in data_rows)
        # No 'VT' column
        assert "VT" not in rows[0]


class TestColumnConsistency:
    """Every data row must have the same field count as the header."""

    def test_consistent_columns(self, tmp_path):
        rows = _run(
            [
                "r1\t1.0\nr2\t2.0\nr3\t3.0\nr4\t4.0\n",
                "r1\t10.0\nr2\t20.0\nr3\t30.0\n",
                "r1\t100.0\nr2\t200.0\nr3\t300.0\nr5\t500.0\n",
            ],
            tmp_path,
            participant_ids=["A", "B", "C"],
        )
        header_len = len(rows[0])
        assert all(len(r) == header_len for r in rows)
        # r4 (only A) and r5 (only C) pruned; only r1, r2, r3 survive
        assert rows[0] == ["Subject", "r1", "r2", "r3"]

    def test_consistent_columns_no_participant_ids(self, tmp_path):
        rows = _run(
            [
                "r1\t1.0\nr2\t2.0\n",
                "r1\t10.0\nr2\t20.0\nr3\t30.0\n",
            ],
            tmp_path,
        )
        header_len = len(rows[0])
        assert all(len(r) == header_len for r in rows)
        # r3 pruned
        assert rows[0] == ["Subject", "r1", "r2"]
        assert rows[1] == ["s0", "1.0", "2.0"]
        assert rows[2] == ["s1", "10.0", "20.0"]


class TestNonROILabelsExcluded:
    """Non-ROI label rows (e.g. frame_start/frame_end/Frame) must never be
    emitted as predictor/measure columns in the group ROI table (issue #28).
    """

    def test_frame_start_excluded_mrtm1(self, tmp_path, caplog):
        """Reproduce issue #28: MRTM1 per-subject kinpar TSVs contain a
        'frame_start\tR1' row (non-numeric value 'R1'). The merged ROI table
        must exclude this column entirely so mri_glmfit does not fail on a
        non-numeric predictor/measure.
        """
        rows = _run(
            [
                "frame_start\tR1\nr1\t1.0\nr2\t2.0\n",
                "frame_start\tR1\nr1\t10.0\nr2\t20.0\n",
            ],
            tmp_path,
            participant_ids=["01", "02"],
        )
        header = rows[0]
        assert "frame_start" not in header
        assert header == ["Subject", "r1", "r2"]
        assert rows[1] == ["01", "1.0", "2.0"]
        assert rows[2] == ["02", "10.0", "20.0"]
        # No non-numeric value anywhere in the emitted data rows
        for row in rows[1:]:
            for val in row[1:]:
                float(val)  # raises if non-numeric

    def test_frame_end_and_frame_excluded(self, tmp_path):
        rows = _run(
            [
                "frame_start\tR1\nframe_end\t90.0\nFrame\t1\nr1\t1.0\n",
                "frame_start\tR1\nframe_end\t90.0\nFrame\t1\nr1\t10.0\n",
            ],
            tmp_path,
            participant_ids=["A", "B"],
        )
        assert rows[0] == ["Subject", "r1"]

    def test_stray_non_numeric_roi_column_dropped_with_warning(self, tmp_path, caplog):
        """A genuinely non-numeric ROI value (not one of the known
        FreeSurfer labels) should also be dropped, with a non-fatal warning,
        rather than crash or propagate into the GLM input table."""
        import logging

        caplog.set_level(logging.WARNING, logger="petsurfer_km")
        rows = _run(
            [
                "r1\t1.0\nbadroi\tNotANumber\n",
                "r1\t10.0\nbadroi\tNotANumber\n",
            ],
            tmp_path,
            participant_ids=["A", "B"],
        )
        assert rows[0] == ["Subject", "r1"]
        assert any(
            "badroi" in rec.message and "non-numeric" in rec.message
            for rec in caplog.records
        )


class TestLengthMismatch:
    """tsvlist and participant_ids length mismatch should return without writing."""

    def test_length_mismatch(self, tmp_path):
        p = tmp_path / "a.tsv"
        p.write_text("r1\t1.0\n")
        out = str(tmp_path / "merged.csv")
        tsv2glmfit([str(p)], out, ["A", "B"])  # 1 file, 2 ids
        # Function returns early; out file should not exist
        import os
        assert not os.path.exists(out)


class TestPairedDiff:
    """Group-level --paired longitudinal analysis for the ROI space.

    Reproduces the bug where the group ROI GLM failed for --paired because
    tsv2glmfit received two TSV files per subject (session1, session2) but
    only one participant_id per subject, tripping the length-mismatch guard
    and never writing the ROI table (downstream mri_glmfit then failed on a
    missing/empty file).
    """

    def test_paired_diff_one_row_per_subject(self, tmp_path):
        # Two subjects, each with session1 and session2 ROI TSVs.
        sub1_ses1 = tmp_path / "sub1_ses1.tsv"
        sub1_ses2 = tmp_path / "sub1_ses2.tsv"
        sub2_ses1 = tmp_path / "sub2_ses1.tsv"
        sub2_ses2 = tmp_path / "sub2_ses2.tsv"
        _write_tsv(sub1_ses1, [("roiA", "1.0"), ("roiB", "2.0")])
        _write_tsv(sub1_ses2, [("roiA", "0.6"), ("roiB", "2.5")])
        _write_tsv(sub2_ses1, [("roiA", "3.0"), ("roiB", "4.0")])
        _write_tsv(sub2_ses2, [("roiA", "2.0"), ("roiB", "4.5")])

        out = str(tmp_path / "merged.csv")
        tsv2glmfit(
            [str(sub1_ses1), str(sub1_ses2), str(sub2_ses1), str(sub2_ses2)],
            out,
            participant_ids=["sub1", "sub2"],
            paired=True,
        )

        rows = _read_table(out)
        # One row per subject (2), not per session (4).
        assert len(rows) == 3  # header + 2 subjects
        assert rows[0] == ["Subject", "roiA", "roiB"]
        assert rows[1][0] == "sub1"
        assert pytest.approx(float(rows[1][1])) == 1.0 - 0.6
        assert pytest.approx(float(rows[1][2])) == 2.0 - 2.5
        assert rows[2][0] == "sub2"
        assert pytest.approx(float(rows[2][1])) == 3.0 - 2.0
        assert pytest.approx(float(rows[2][2])) == 4.0 - 4.5

    def test_paired_length_mismatch_still_guarded(self, tmp_path):
        # 3 files but only 1 participant_id: not a multiple-of-2 relationship
        # with participant_ids, should be rejected without writing.
        p1 = tmp_path / "a.tsv"
        p2 = tmp_path / "b.tsv"
        p3 = tmp_path / "c.tsv"
        for p in (p1, p2, p3):
            p.write_text("roiA\t1.0\n")
        out = str(tmp_path / "merged.csv")
        tsv2glmfit([str(p1), str(p2), str(p3)], out, ["A"], paired=True)
        import os
        assert not os.path.exists(out)
