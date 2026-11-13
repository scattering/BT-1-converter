"""FullProf serialization and input-protection tests; no scientific kernel mocks."""

import hashlib
from pathlib import Path
import tempfile
import unittest

from browser.fullprof import write_fullprof_from_csv


class FullProfTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.csv = self.root / "pattern.csv"
        self.dat = self.root / "pattern.dat"

    def source(self, rows):
        text = "two_theta_deg,intensity,sigma,valid,contributions\n" + "\n".join(rows) + "\n"
        self.csv.write_text(text)
        return self.csv.read_bytes()

    def test_preserves_full_precision_tokens_and_six_line_header(self):
        original = self.source([
            "1.0000000000000002,1234567.8901234567,0.12345678901234566,1,3",
            "1.05,1.2345678901234568e-12,9.8765432109876543e-08,1,1",
        ])
        report = write_fullprof_from_csv(self.csv, self.dat)
        lines = self.dat.read_text().splitlines()
        self.assertTrue(lines[0].startswith("XYDATA"))
        self.assertEqual(lines[2], "INTER 1 1 0 0")
        self.assertEqual(lines[6:], [
            "1.0000000000000002 1234567.8901234567 0.12345678901234566",
            "1.05 1.2345678901234568e-12 9.8765432109876543e-08",
        ])
        self.assertEqual(self.csv.read_bytes(), original)
        self.assertEqual(report["source_csv_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(report["columns"], ["two_theta_deg", "intensity", "sigma"])
        self.assertEqual(report["format"], "FullProf XYDATA")
        self.assertEqual(report["instrument_code"], 10)
        self.assertEqual(report["interpolation"], 0)
        self.assertEqual(report["filename"], "pattern.dat")

    def test_omits_only_explicit_invalid_bins_without_closing_gaps(self):
        self.source([
            "20,100,10,1,1", "20.125,0,0,0,0", "20.25,nan,nan,0,0",
            "20.375,0,7,1,1", "20.5,-5,8,1,1", "20.625,105,10,1,1",
        ])
        report = write_fullprof_from_csv(self.csv, self.dat)
        self.assertEqual(self.dat.read_text().splitlines()[6:], [
            "20 100 10", "20.375 0 7", "20.5 -5 8", "20.625 105 10",
        ])
        self.assertEqual(report["points"], 4)
        self.assertEqual(report["omitted_invalid_points"], 2)
        self.assertEqual(report["nonpositive_intensity_points"], 2)
        self.assertTrue(any("variance to 1" in item for item in report["warnings"]))
        self.assertTrue(any("do not interpolate across gaps" in item for item in report["warnings"]))

    def test_reader_probe_fixture_has_exact_omitted_coordinates(self):
        # The actual FullProf 8.50 probe uses these synthetic values. Its .out
        # confirms 98 surviving positions and positive-I variances; it also
        # exposes the nonpositive-I reader behavior described in the warning.
        rows = []
        for i in range(101):
            intensity, sigma = (0, 7) if i == 10 else (-5, 8) if i == 20 else (100 + i, 10)
            rows.append(f"{20 + i / 8},{intensity},{sigma},{int(i not in (40, 41, 42))},1")
        self.source(rows)
        report = write_fullprof_from_csv(self.csv, self.dat)
        triples = [list(map(float, line.split())) for line in self.dat.read_text().splitlines()[6:]]
        self.assertEqual(len(triples), 98)
        self.assertEqual([row[0] for row in triples], [20 + i / 8 for i in range(101) if i not in (40, 41, 42)])
        self.assertEqual(triples[10], [21.25, 0, 7])
        self.assertEqual(triples[20], [22.5, -5, 8])
        self.assertEqual(report["nonpositive_intensity_points"], 2)

    def test_rejects_invalid_observations_before_creating_output(self):
        for row in [
            "1,nan,1,1,1", "1,inf,1,1,1", "1,1,nan,1,1", "1,1,0,1,1",
            "1,1,-1,1,1", "nan,1,1,1,1", "1,1e999,1,1,1", "1,1,1,true,1",
            "1,1_000,1,1,1", "1,1,1,1", "1,1,1,1,1,extra",
        ]:
            with self.subTest(row=row):
                original = self.source([row])
                with self.assertRaises(ValueError):
                    write_fullprof_from_csv(self.csv, self.dat)
                self.assertFalse(self.dat.exists())
                self.assertEqual(self.csv.read_bytes(), original)

    def test_rejects_nonincreasing_angles_including_invalid_rows(self):
        for angle in ("1", "0.5", "nan"):
            with self.subTest(angle=angle):
                self.source(["1,2,1,1,1", f"{angle},0,0,0,0", "2,3,1,1,1"])
                with self.assertRaises(ValueError):
                    write_fullprof_from_csv(self.csv, self.dat)
                self.assertFalse(self.dat.exists())

    def test_rejects_no_valid_observations(self):
        self.source(["1,0,0,0,0"])
        with self.assertRaisesRegex(ValueError, "no valid observations"):
            write_fullprof_from_csv(self.csv, self.dat)
        self.assertFalse(self.dat.exists())

    def test_rejects_missing_mask_and_duplicate_headers(self):
        for header in ("two_theta_deg,intensity,sigma", "two_theta_deg,intensity,sigma,valid,valid"):
            with self.subTest(header=header):
                self.csv.write_text(header + "\n1,2,3,1,1\n")
                with self.assertRaisesRegex(ValueError, "unique"):
                    write_fullprof_from_csv(self.csv, self.dat)
                self.assertFalse(self.dat.exists())

    def test_refuses_existing_destination_and_input_aliases(self):
        original = self.source(["1,2,1,1,1", "2,3,1,1,1"])
        self.dat.write_bytes(b"existing result")
        for destination in (self.dat, self.csv):
            with self.subTest(destination=destination):
                with self.assertRaises(FileExistsError):
                    write_fullprof_from_csv(self.csv, destination)
        self.assertEqual(self.dat.read_bytes(), b"existing result")
        self.assertEqual(self.csv.read_bytes(), original)

    def test_refuses_symlinks_and_hardlinks_without_touching_source(self):
        original = self.source(["1,2,1,1,1", "2,3,1,1,1"])
        aliases = [self.root / name for name in ("symlink.dat", "dangling.dat", "hardlink.dat")]
        aliases[0].symlink_to(self.csv)
        aliases[1].symlink_to(self.root / "absent")
        aliases[2].hardlink_to(self.csv)
        for destination in aliases:
            with self.subTest(destination=destination):
                with self.assertRaises(FileExistsError):
                    write_fullprof_from_csv(self.csv, destination)
        self.assertEqual(self.csv.read_bytes(), original)
        self.assertTrue(aliases[1].is_symlink())
        self.assertFalse((self.root / "absent").exists())


if __name__ == "__main__":
    unittest.main()
