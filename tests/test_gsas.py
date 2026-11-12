import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from bt1modern.gsas import read_gsas, write_gsas


def pattern(values, sigma=None, valid=None, start=10.0, step=0.05):
    y = np.asarray(values, dtype=float)
    return SimpleNamespace(angles=start + step * np.arange(len(y)), intensity=y,
        sigma=np.asarray(sigma if sigma is not None else np.sqrt(np.maximum(abs(y), 1)), float),
        valid=np.asarray(valid if valid is not None else np.ones(len(y)), bool),
        contributions=np.ones(len(y)), metadata={"title": "BT-1 test", "output_step": step})


class GSASTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "pattern.gsas"

    def tearDown(self):
        self.directory.cleanup()

    def test_fractional_values_and_blank_final_record(self):
        original = pattern([1.2345, 245.6781, 90.0011, 2.4, 100.1, 101.2, 20.9])
        info = write_gsas(original, self.path)
        bank, = read_gsas(self.path)
        np.testing.assert_allclose(bank.angles, original.angles, atol=1e-11)
        np.testing.assert_allclose(bank.intensity, original.intensity, atol=0.00051)
        np.testing.assert_allclose(bank.sigma, original.sigma, atol=0.00051)
        self.assertEqual(len(bank.angles), 7)
        self.assertTrue(all(len(line) == 80 for line in self.path.read_text().splitlines()))
        self.assertEqual(self.path.read_text().splitlines()[-1][32:], " " * 48)
        self.assertEqual(info["valid_points"], 7)
        self.assertFalse(self.path.with_suffix(".inst").exists())

    def test_holes_are_omitted_not_interpolated_or_fake_zeroes(self):
        original = pattern([11, 12, np.nan, 14, 15, 16, 17],
                           [1, 2, np.nan, 4, 5, 6, 7], [1, 1, 0, 1, 1, 0, 1])
        info = write_gsas(original, self.path, gap_policy="split")
        banks = read_gsas(self.path)
        self.assertEqual([len(b.angles) for b in banks], [2, 2, 1])
        np.testing.assert_allclose(np.concatenate([b.angles for b in banks]), original.angles[original.valid])
        self.assertEqual(info["omitted_invalid_points"], 2)
        self.assertEqual(info["gap_policy"], "split")
        self.assertEqual(info["valid_mask"], original.valid.tolist())

    def test_default_uniform_grid_retains_internal_and_edge_gaps_with_zero_weight(self):
        original = pattern([np.nan, 12, np.inf, 14, 15, 16, np.nan],
                           [np.nan, 2, np.inf, 4, 5, 6, -1], [0, 1, 0, 1, 1, 0, 0])
        info = write_gsas(original, self.path)
        bank, = read_gsas(self.path)
        np.testing.assert_allclose(bank.angles, original.angles, atol=1e-11)
        np.testing.assert_array_equal(bank.intensity, [0, 12, 0, 14, 15, 0, 0])
        np.testing.assert_array_equal(bank.sigma, [0, 2, 0, 4, 5, 0, 0])
        self.assertEqual(info["gap_policy"], "uniform")
        self.assertTrue(info["uniform_grid"])
        self.assertEqual(info["valid_mask"], original.valid.tolist())
        self.assertEqual(bank.metadata["valid_mask"], original.valid.tolist())
        self.assertEqual(info["invalid_points"], 4)
        self.assertEqual(info["zero_weight_placeholder_points"], 4)
        self.assertEqual(info["omitted_invalid_points"], 0)
        self.assertEqual(info["banks"][0]["points"], 7)
        self.assertTrue(any("not measured zero" in note for note in info["warnings"]))
        self.assertEqual(self.path.read_text().splitlines()[-1][32:], " " * 48)

    def test_uniform_and_split_preserve_identical_valid_measurements(self):
        original = pattern([1.234, 2.345, np.nan, 4.567], [1, 2, 0, 4], [1, 1, 0, 1])
        write_gsas(original, self.path)
        uniform, = read_gsas(self.path)
        write_gsas(original, self.path, gap_policy="split", overwrite=True)
        split = read_gsas(self.path)
        np.testing.assert_array_equal(uniform.intensity[original.valid], np.concatenate([b.intensity for b in split]))
        np.testing.assert_array_equal(uniform.sigma[original.valid], np.concatenate([b.sigma for b in split]))

    def test_unknown_gap_policy_rejected_before_creation(self):
        with self.assertRaisesRegex(ValueError, "gap_policy"):
            write_gsas(pattern([1, 2]), self.path, gap_policy="interpolate")
        self.assertFalse(self.path.exists())

    def test_nonpositive_valid_measurements_preserved_with_warning(self):
        original = pattern([0, -10.25, 5.125], [1, 2, 3])
        info = write_gsas(original, self.path)
        bank, = read_gsas(self.path)
        np.testing.assert_array_equal(bank.intensity, original.intensity)
        np.testing.assert_array_equal(bank.sigma, original.sigma)
        self.assertEqual(info["valid_nonpositive_intensity_points"], 2)
        self.assertTrue(any("GSAS-II" in note for note in info["warnings"]))

    def test_exponents_for_tiny_and_large_values_never_zero_positive_sigma(self):
        original = pattern([1e-12, 1e12, -1e20, 1e100], [1e-9, 123.4567, 1e15, 1e-100])
        write_gsas(original, self.path)
        bank, = read_gsas(self.path)
        self.assertTrue(np.isfinite(bank.intensity).all())
        self.assertTrue(np.isfinite(bank.sigma).all())
        self.assertTrue((bank.sigma > 0).all())
        np.testing.assert_allclose(bank.intensity, original.intensity, rtol=1e-3, atol=0)

    def test_small_sigma_does_not_suffer_fixed_decimal_distortion(self):
        write_gsas(pattern([1, 2], [0.0006, 0.0014]), self.path)
        bank, = read_gsas(self.path)
        np.testing.assert_allclose(bank.sigma, [0.0006, 0.0014], rtol=1e-12)

    def test_no_clobber_and_bad_values_fail_before_creation(self):
        write_gsas(pattern([1, 2]), self.path)
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            write_gsas(pattern([3, 4]), self.path)
        self.assertEqual(self.path.read_bytes(), before)
        for sigma in ([0, 1], [-1, 1], [np.nan, 1]):
            with self.assertRaises(ValueError):
                write_gsas(pattern([1, 2], sigma), self.path, overwrite=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_header_injection_rejected_and_title_sanitized(self):
        with self.assertRaises(ValueError):
            write_gsas(pattern([1, 2]), self.path, instrument_reference="x.inst\nBANK 99")
        self.assertFalse(self.path.exists())
        write_gsas(pattern([1, 2]), self.path, title="sample\nBANK fake μ", instrument_reference="user.inst")
        bank, = read_gsas(self.path)
        self.assertEqual(bank.metadata["instrument_reference"], "user.inst")
        self.assertEqual(bank.metadata["title"], "sample BANK fake ?")

    def test_nonuniform_grid_and_no_valid_points_rejected(self):
        p = pattern([1, 2, 3]); p.angles[2] += 0.01
        with self.assertRaises(ValueError):
            write_gsas(p, self.path)
        with self.assertRaises(ValueError):
            write_gsas(pattern([1, 2], valid=[0, 0]), self.path)

    def test_reader_honors_legacy_count_instead_of_extra_zero_pairs(self):
        title = "legacy".ljust(80)
        header = "BANK 1 2 1 CONST 1000 5 0 0 ESD".ljust(80)
        record = "".join(f"{v:8.0f}" for v in [101, 10, 202, 14, 0, 0, 0, 0, 0, 0])
        self.path.write_text(title + "\n" + header + "\n" + record + "\n")
        bank, = read_gsas(self.path)
        self.assertEqual(len(bank.angles), 2)
        np.testing.assert_array_equal(bank.intensity, [101, 202])

    def test_reader_rejects_truncation_and_negative_sigma(self):
        write_gsas(pattern([1, 2]), self.path)
        lines = self.path.read_text().splitlines()
        self.path.write_text("\n".join(lines[:-1]) + "\n")
        with self.assertRaises(ValueError):
            read_gsas(self.path)
        self.path.write_text("\n".join(lines[:-1] + [f'{1:8.3f}{-2:8.3f}'.ljust(80)]) + "\n")
        with self.assertRaises(ValueError):
            read_gsas(self.path)


if __name__ == "__main__":
    unittest.main()
