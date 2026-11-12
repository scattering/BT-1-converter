"""I/O regression tests; optional existing real files are never modified."""
from copy import deepcopy
from pathlib import Path
import hashlib
import os
import tempfile
import unittest

import numpy as np

from bt1modern.io import Bank, RawDataset, FormatError, parse_bt1, prepare_bt1, read_raw, write_raw


def synthetic_bt1(planned=4, recorded=None, *, masks=False, footer=True):
    recorded = planned if recorded is None else recorded
    lines = [f"'synthetic.bt1' 'Jan  1 2026' 1 0 0 0 1 1 1000. 2 'NEUT' {planned} 'RAW'",
             "Filename Date Exp Parameters Mon Prf Base pts Type", "Synthetic BT-1 test",
             "60 20 15 15 0 0 0 2.0777 25.0 0.0 0.0 32", "Collimation Mosaic Wavelength T-start Incr H-field Det",
             "3 0.0000 0.0000 0.0000", f"4 1.3000 0.0500 {1.3 + (planned - 1) * .05:.4f}",
             "Mot Start Step End"]
    for values, name in [(np.linspace(1, 1.31, 32), "Detector Relative Scalefactors"),
                         (np.arange(32) * .1, "Detector Relative Zero Angles")]:
        for i in range(0, 32, 11):
            lines.append(" ".join(f"{x:.4f}" for x in values[i:i+11]))
        lines.append(name + " GE311")
    lines.append("Data in 'N' Detector Blocks with N = #scl")
    for point in range(recorded):
        counts = list(range(100 + point*32, 132 + point*32))
        if masks and point == 1:
            counts[4] = -999
        lines += [f"$ M4= {1.3 + point*.05:.3f} T= {25+point*.001:.3f} C= 1.0 N=0", "$ 0 0 0 100"]
        # Arbitrary line wrapping is deliberate: not the usual two lines.
        for i in range(0, 32, 7):
            lines.append(",".join(str(c) for c in counts[i:i+7]) + ("," if i+7 < 32 else ""))
    if footer:
        lines.append("$  1-Jan-2026 12:00:00")
    return "\n".join(lines) + "\n"


class IOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def input(self, content=None):
        p = self.root / "input.bt1"
        p.write_text(synthetic_bt1() if content is None else content, encoding="ascii")
        return p

    def test_real_shape_metadata_and_units_synthetic(self):
        d = parse_bt1(self.input())
        self.assertEqual(len(d.banks), 32)
        self.assertEqual(d.header["monitor"], 2000)
        self.assertEqual(d.header["collimation"], (60, 20))
        self.assertEqual(d.header["count_type"], "NEUT")
        self.assertEqual(d.header["experimental_parameters"], (0, 0, 0, 1, 1))
        self.assertEqual(d.header["recorded_points"], 4)
        self.assertTrue(d.header["complete"])
        self.assertAlmostEqual(d.header["temp_summary"]["mean"], 25.0015)
        np.testing.assert_allclose(d.banks[1].angles, [6.3, 6.35, 6.4, 6.45], atol=1e-12)
        self.assertAlmostEqual(d.banks[31].zero, .031)
        self.assertAlmostEqual(d.banks[31].scale, 1.31)
        self.assertEqual(d.banks[31].counts[-1], 227)

    def test_complete_records_without_footer_are_complete(self):
        d = parse_bt1(self.input(synthetic_bt1(footer=False)))
        self.assertTrue(d.header["complete"])
        self.assertIsNone(d.header["terminal_timestamp"])

    def test_partial_requires_explicit_opt_in(self):
        p = self.input(synthetic_bt1(recorded=2, footer=False))
        with self.assertRaisesRegex(FormatError, "Incomplete"):
            parse_bt1(p)
        d = parse_bt1(p, allow_partial=True)
        self.assertFalse(d.header["complete"])
        self.assertEqual(d.header["recorded_points"], 2)

    def test_truncated_last_detector_record_reported(self):
        text = synthetic_bt1(recorded=2, footer=False) + "$ M4= 1.400 T=25.0\n$ auxiliary\n1,2,3\n"
        p = self.input(text)
        with self.assertRaisesRegex(FormatError, "Truncated detector"):
            parse_bt1(p)
        d = parse_bt1(p, allow_partial=True)
        self.assertEqual(d.header["recorded_points"], 2)
        self.assertEqual(d.header["trailing_incomplete_record"]["detector_counts_present"], 3)

    def test_missing_or_irregular_angles_rejected_even_partial(self):
        p = self.input(synthetic_bt1().replace("M4= 1.350", "M4= 1.375"))
        for partial in [False, True]:
            with self.assertRaisesRegex(FormatError, "irregular angle"):
                parse_bt1(p, allow_partial=partial)

    def test_unsupported_five_detector_header(self):
        p = self.input(synthetic_bt1().replace("0.0 0.0 32", "0.0 0.0 5"))
        with self.assertRaisesRegex(FormatError, "five-detector"):
            parse_bt1(p)

    def test_negative_masks_and_drop_keep_physical_bank(self):
        d = parse_bt1(self.input(synthetic_bt1(masks=True)))
        before = d.banks[4].counts.copy()
        out = prepare_bt1(d, cutoff=0, drop_detectors=[2])
        self.assertEqual(len(out.banks), 32)
        self.assertTrue(np.all(out.banks[1].counts < 0))
        np.testing.assert_array_equal(out.banks[4].counts, before)
        self.assertEqual(out.banks[4].counts[1], -999)
        self.assertTrue(np.all(d.banks[1].counts >= 0))
        self.assertNotIn("preparation", d.header)

    def test_explicit_cutoff_m4_first_three_and_keep_one(self):
        d = parse_bt1(self.input())
        out = prepare_bt1(d, cutoff=1.375, keep_detector1=True)
        self.assertEqual(len(out.banks[0].counts), 4)
        self.assertEqual(len(out.banks[1].counts), 2)
        self.assertEqual(len(out.banks[2].counts), 2)
        self.assertEqual(len(out.banks[3].counts), 4)
        self.assertAlmostEqual(out.banks[1].angles[0], 6.4)

    def test_cutoff_entire_bank_rejected(self):
        with self.assertRaisesRegex(FormatError, "every point"):
            prepare_bt1(parse_bt1(self.input()), cutoff=2)

    def test_raw_roundtrip_masked_banks(self):
        d = prepare_bt1(parse_bt1(self.input(synthetic_bt1(masks=True))), cutoff=1.35, drop_detectors=[7])
        p = write_raw(d, self.root / "test.raw")
        actual = read_raw(p)
        self.assertEqual(actual.header["date"], d.header["date"])
        self.assertEqual(actual.header["monitor"], d.header["monitor"])
        self.assertEqual(actual.header["collimation"], d.header["collimation"])
        self.assertIsNone(actual.header["complete"])
        self.assertIsNone(actual.header["temp_summary"])
        for a, b in zip(actual.banks, d.banks):
            np.testing.assert_allclose(a.angles, b.angles, rtol=0, atol=1e-12)
            np.testing.assert_array_equal(a.counts, b.counts)
            self.assertEqual(a.detector, b.detector)
            self.assertAlmostEqual(a.scale, b.scale)
            self.assertAlmostEqual(a.zero, b.zero)
        self.assertFalse((self.root / "test.raw.metadata.json").exists())

    def test_raw_overflow_rejected_before_output_created(self):
        d = parse_bt1(self.input())
        d.banks[0].counts[0] = 1000000
        p = self.root / "fail.raw"
        with self.assertRaisesRegex(FormatError, "overflows"):
            write_raw(d, p)
        self.assertFalse(p.exists())

    def test_raw_fractional_centidegree_rejected(self):
        d = parse_bt1(self.input())
        d.banks[0].angles = 1.3 + np.arange(4) * .025
        with self.assertRaisesRegex(FormatError, "integer centidegree"):
            write_raw(d, self.root / "bad.raw")

    def test_raw_irregular_angles_rejected(self):
        d = parse_bt1(self.input())
        d.banks[0].angles[1] += .01
        with self.assertRaisesRegex(FormatError, "irregular"):
            write_raw(d, self.root / "bad.raw")

    def test_raw_scale_precision_loss_rejected(self):
        d = parse_bt1(self.input())
        d.banks[0].scale = 1.234567
        with self.assertRaisesRegex(FormatError, "refusing to round"):
            write_raw(d, self.root / "bad.raw")

    def test_no_overwrite_and_validation_before_overwrite(self):
        d = parse_bt1(self.input())
        p = write_raw(d, self.root / "out.raw")
        old = p.read_bytes()
        with self.assertRaises(FileExistsError):
            write_raw(d, p)
        d.banks[0].counts[0] = 1000000
        with self.assertRaises(FormatError):
            write_raw(d, p, overwrite=True)
        self.assertEqual(old, p.read_bytes())

    def test_raw_truncated_count_rows_rejected(self):
        d = parse_bt1(self.input())
        p = write_raw(d, self.root / "out.raw")
        p.write_text("\n".join(p.read_text().splitlines()[:-1]) + "\n")
        with self.assertRaisesRegex(FormatError, "Truncated RAW"):
            read_raw(p)

    def test_raw_duplicate_bank_rejected(self):
        d = parse_bt1(self.input())
        p = write_raw(d, self.root / "out.raw")
        rows = p.read_text().splitlines()
        rows[4] = rows[4][:40] + f"{1:5d}" + rows[4][45:]
        p.write_text("\n".join(rows) + "\n")
        with self.assertRaisesRegex(FormatError, "duplicate"):
            read_raw(p)

    def test_malformed_temperature_rejected(self):
        with self.assertRaisesRegex(FormatError, "temperature"):
            parse_bt1(self.input(synthetic_bt1().replace("T= 25.000", "T= nan")))


class ExistingFixtureTests(unittest.TestCase):
    """Opt-in real-file checks; portable tests above do not require these files."""

    @unittest.skipUnless(os.environ.get("BT1_REAL_FIXTURE_DIR"), "set BT1_REAL_FIXTURE_DIR for current scans")
    def test_two_complete_current_scans_and_partial(self):
        root = Path(os.environ["BT1_REAL_FIXTURE_DIR"])
        files = [root / f"Mn5Si00{i}.bt1" for i in (1, 2, 3)]
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        for p in files[:2]:
            d = parse_bt1(p)
            self.assertEqual(d.header["recorded_points"], 201)
            self.assertEqual(d.header["wavelength"], 2.0777)
            self.assertEqual(d.header["collimation"], (60, 20))
            self.assertTrue(d.header["complete"])
            self.assertEqual(len(d.banks), 32)
            self.assertTrue(3.1 < d.header["temp_summary"]["mean"] < 3.4)
        with self.assertRaises(FormatError):
            parse_bt1(files[2])
        partial = parse_bt1(files[2], allow_partial=True)
        self.assertEqual(partial.header["recorded_points"], 13)
        self.assertFalse(partial.header["complete"])
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})

    @unittest.skipUnless(os.environ.get("BT1_LEGACY_FIXTURE_DIR"), "set BT1_LEGACY_FIXTURE_DIR for historical files")
    def test_historical_bt1_and_raw(self):
        root = Path(os.environ["BT1_LEGACY_FIXTURE_DIR"])
        paths = [root / name for name in ("test.bt1", "test2.bt1", "test.raw", "test2.raw")]
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        with tempfile.TemporaryDirectory() as tmp:
            for p in paths:
                d = parse_bt1(p) if p.suffix == ".bt1" else read_raw(p)
                self.assertEqual(len(d.banks), 32)
                out = Path(tmp) / (p.name + ".raw")
                write_raw(d, out)
                actual = read_raw(out)
                for a, b in zip(actual.banks, d.banks):
                    np.testing.assert_array_equal(a.counts, b.counts)
                    np.testing.assert_allclose(a.angles, b.angles, rtol=0, atol=1e-12)
                    self.assertAlmostEqual(a.scale, b.scale)
                    self.assertAlmostEqual(a.zero, b.zero)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


if __name__ == "__main__":
    unittest.main()
