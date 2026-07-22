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
