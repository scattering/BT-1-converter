"""Numerical preparation/serialization contracts without native executables."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import numpy as np

from bt1modern.io import Bank, RawDataset, FormatError, prepare_bt1, prepare_legacy_bt1, read_raw, write_raw
from bt1modern.gsas import read_gsas, write_legacy_gsas


def acquisition(*, start=0.0, step=.1, count=11):
    header = {"title": "Synthetic legacy acquisition", "a4_start": start, "a4_step": step,
              "wavelength": 2.0777, "collimation": (60, 20), "monochromator": "GE311",
              "monitor": 2000, "monitor_base": 1000, "monitor_prefactor": 2,
              "experimental_parameters": (0, 0, 0, 1, 1), "date": "Jan  1 2026",
              "complete": True, "recorded_points": count, "planned_points": count}
    banks = [Bank(detector, start + step*np.arange(count) + (detector-1)*5,
                  np.arange(count, dtype=np.int64)+100, 1.0, .0123) for detector in range(1, 33)]
    return RawDataset(header, banks, "synthetic.bt1")


def pattern(intensity, sigma=None, valid=None, *, start=1.3, step=.05):
    intensity = np.asarray(intensity, dtype=float)
    return SimpleNamespace(
        angles=start+step*np.arange(len(intensity)), intensity=intensity,
        sigma=np.asarray(sigma if sigma is not None else np.full(len(intensity), 2.0), dtype=float),
        valid=np.asarray(valid if valid is not None else np.ones(len(intensity)), dtype=bool),
        metadata={"title": "Legacy serialization test", "legacy_tstart_deg": float(np.float32(start)),
                  "legacy_step_deg": float(np.float32(step)), "legacy_nominal_input_step_deg": step})


class LegacyPreparationTests(unittest.TestCase):
    def test_nongrid_cutoff_reproduces_nearest_index_and_correct_mode_is_unchanged(self):
        original = acquisition()
        historical = prepare_legacy_bt1(original, cutoff=.24)
        corrected = prepare_bt1(original, cutoff=.24)
        np.testing.assert_allclose(historical.banks[0].angles[:2], [.2, .3])
        np.testing.assert_allclose(corrected.banks[0].angles[:2], [.3, .4])
        self.assertEqual(historical.header["preparation"]["removed_leading_points"]["1"], 2)
        self.assertEqual(historical.header["preparation"]["mode"], "legacy-proprep-python")
        self.assertEqual(historical.banks[3].angles[0], 15)
        self.assertEqual(original.banks[0].angles[0], 0)

    def test_nint_exact_half_rounds_away_from_zero(self):
        output = prepare_legacy_bt1(acquisition(), cutoff=.25)
        self.assertEqual(output.header["preparation"]["removed_leading_points"]["1"], 3)
        self.assertEqual(output.banks[0].angles[0], .3)

    def test_cutoff_before_start_keeps_all_points(self):
        original = acquisition(start=1.3, step=.05)
        output = prepare_legacy_bt1(original)
        self.assertEqual([len(bank.counts) for bank in output.banks], [11]*32)

    def test_keep_detector1_and_original_negative_drop_rule(self):
        original = acquisition()
        original.banks[1].counts[0] = -99
        output = prepare_legacy_bt1(original, cutoff=0, drop_detectors=[2], keep_detector1=True)
        self.assertEqual(output.banks[1].counts[0], -1)
        np.testing.assert_array_equal(output.banks[1].counts[1:], -original.banks[1].counts[1:])
        self.assertEqual(original.banks[1].counts[0], -99)
        output = prepare_legacy_bt1(original, cutoff=.24, keep_detector1=True)
        self.assertEqual(output.banks[0].angles[0], 0)
        self.assertEqual(output.banks[1].angles[0], 5.2)

    def test_real32_and_raw_field_precision_explicit_and_roundtrips(self):
        original = acquisition(start=.012, step=.1)
        original.header["title"] = "X"*60
        original.header["wavelength"] = 2.077749
        original.header["monitor_base"] = 1234567.89
        original.header["monitor_prefactor"] = 3
        original.banks[0].scale = 1.23455
        original.banks[0].zero = .012345678
        output = prepare_legacy_bt1(original, cutoff=0)
        self.assertEqual(output.header["title"], "X"*50)
        self.assertEqual(output.header["preparation"]["source_header_fields"]["title"], "X"*60)
        self.assertEqual(output.header["wavelength"], float(f"{float(np.float32(2.077749)):.4f}"))
        self.assertEqual(output.banks[0].scale, float(f"{float(np.float32(1.23455)):.4f}"))
        self.assertEqual(output.banks[0].zero, float(f"{float(np.float32(.012345678*100)):.4f}")/100)
        self.assertAlmostEqual(output.header["preparation"]["raw_angle_max_absolute_rounding_deg"]["1"], .002)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"prepared.raw"
            write_raw(output, path)
            restored = read_raw(path)
            for expected, actual in zip(output.banks, restored.banks):
                np.testing.assert_array_equal(expected.angles, actual.angles)
                np.testing.assert_array_equal(expected.counts, actual.counts)
                self.assertEqual(expected.scale, actual.scale)
                self.assertEqual(expected.zero, actual.zero)
            self.assertEqual(restored.header["monitor"], output.header["monitor"])

    def test_empty_banks_irregular_grid_and_inconsistent_rounded_endpoint_rejected(self):
        with self.assertRaisesRegex(FormatError, "every point"):
            prepare_legacy_bt1(acquisition(), cutoff=10)
        irregular = acquisition(); irregular.banks[4].angles[2] += .01
        with self.assertRaisesRegex(FormatError, "header grid"):
            prepare_legacy_bt1(irregular, cutoff=0)
        with self.assertRaisesRegex(FormatError, "endpoint"):
            prepare_legacy_bt1(acquisition(step=.014, count=3), cutoff=0)

    def test_fixed_width_overflow_and_unsupported_bank_set_rejected(self):
        overflowing = acquisition(); overflowing.banks[5].counts[0] = 1000000
        with self.assertRaisesRegex(FormatError, "overflows"):
            prepare_legacy_bt1(overflowing, cutoff=0)
        too_negative = acquisition(); too_negative.banks[5].counts[0] = 100000
        with self.assertRaisesRegex(FormatError, "overflows"):
            prepare_legacy_bt1(too_negative, cutoff=0, drop_detectors=[6])
        missing = acquisition(); missing.banks.pop()
        with self.assertRaisesRegex(FormatError, "exactly detectors"):
            prepare_legacy_bt1(missing)
        scale_zero = acquisition(); scale_zero.banks[0].scale = .000001
        with self.assertRaisesRegex(FormatError, "nonpositive detector scale"):
            prepare_legacy_bt1(scale_zero)

    def test_input_metadata_and_arrays_are_not_modified(self):
        original = acquisition()
        saved = deepcopy(original)
        prepare_legacy_bt1(original, cutoff=.24, drop_detectors=[1])
        self.assertEqual(original.header, saved.header)
        for old, new in zip(saved.banks, original.banks):
            np.testing.assert_array_equal(old.angles, new.angles)
            np.testing.assert_array_equal(old.counts, new.counts)


class RawFortranFieldTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/"fields.raw"
        write_raw(prepare_legacy_bt1(acquisition(), cutoff=0), self.path)
        self.base_lines = self.path.read_text().splitlines()

    def read_monitor(self, token):
        lines = list(self.base_lines)
        lines[0] = lines[0][:70] + token.rjust(14)
        self.path.write_text("\n".join(lines) + "\n")
        return read_raw(self.path)

    def test_g14_1_implied_vs_explicit_decimal_changes_float32_monitor_ratio(self):
        implied = [self.read_monitor(token) for token in ("16777217", "33554435")]
        explicit = [self.read_monitor(token) for token in ("16777217.", "33554435.")]
        self.assertEqual([d.header["monitor"] for d in implied], [1677721.7, 3355443.5])
        self.assertEqual([d.header["monitor"] for d in explicit], [16777217., 33554435.])
        implied_ratio = np.float32(implied[0].header["monitor"]) / np.float32(implied[1].header["monitor"])
        explicit_ratio = np.float32(explicit[0].header["monitor"]) / np.float32(explicit[1].header["monitor"])
        self.assertEqual(float(implied_ratio), .5)
        self.assertEqual(float(explicit_ratio), .4999999403953552)
        self.assertEqual(implied[0].header["raw_numeric_fields"]["monitor"],
                         {"descriptor": "G14.1", "text": "16777217".rjust(14)})

    def test_exponents_do_not_override_implied_decimal_and_embedded_blanks_are_null(self):
        for token, expected in (("123E2", 1230), ("123D2", 1230), ("123+02", 1230),
                                ("123E-2", .123), ("123.E2", 12300), ("1 2 3", 12.3)):
            with self.subTest(token=token):
                self.assertEqual(self.read_monitor(token).header["monitor"], expected)

    def test_scale_and_zero_follow_f8_4_while_wavelength_uses_f8_0(self):
        for scale, zero, expected_scale, expected_zero in (
                ("10000", "12300", 1.0, .0123),
                ("123E2", "123E-2", 1.23, .00000123),
                ("123.E2", "123.E-2", 12300, .0123)):
            with self.subTest(scale=scale):
                lines = list(self.base_lines)
                lines[0] = lines[0][:50] + "2".rjust(8) + lines[0][58:]
                lines[2] = lines[2][:24] + scale.rjust(8) + zero.rjust(8) + lines[2][40:]
                self.path.write_text("\n".join(lines) + "\n")
                dataset = read_raw(self.path)
                self.assertEqual(dataset.header["wavelength"], 2.0)
                self.assertEqual(dataset.banks[0].scale, expected_scale)
                self.assertAlmostEqual(dataset.banks[0].zero, expected_zero, places=15)
                self.assertEqual(dataset.header["raw_numeric_fields"]["banks"]["1"]["scale"]["text"], scale.rjust(8))

    def test_monitor_writer_always_supplies_decimal_in_mantissa(self):
        dataset = prepare_legacy_bt1(acquisition(), cutoff=0)
        for monitor in (137., 2000., 1e7, 1e20, .0000001):
            with self.subTest(monitor=monitor):
                dataset.header["monitor"] = monitor
                write_raw(dataset, self.path, overwrite=True)
                field = self.path.read_text().splitlines()[0][70:84]
                self.assertEqual(len(field), 14)
                self.assertIn(".", field.split("E")[0])
                self.assertEqual(read_raw(self.path).header["monitor"], monitor)


class LegacyGSASTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/"legacy.gsas"

    def test_half_integer_nearest_even_rounding_and_trailing_decimal_points(self):
        original = pattern([-2.5, -1.5, -.5, .5, 1.5, 2.5], [2.5]*6)
        info = write_legacy_gsas(original, self.path)
        bank, = read_gsas(self.path)
        np.testing.assert_array_equal(bank.intensity, [-2, -2, 0, 0, 2, 2])
        np.testing.assert_array_equal(bank.sigma, [2]*6)
        lines = self.path.read_text().splitlines()
        self.assertEqual(lines[1], "BANK  1        6       2 CONST     130.00      5.00 0 0 ESD".ljust(80))
        self.assertIn("     -0.", lines[2])
        self.assertEqual(lines[-1][16:], " "*64)
        self.assertTrue(all(len(line) == 80 for line in lines))
        self.assertEqual(info["max_absolute_quantization"]["sigma"], .5)
        np.testing.assert_array_equal(original.intensity, [-2.5, -1.5, -.5, .5, 1.5, 2.5])

    def test_final_float32_sigma_cast_precedes_f8_rounding_without_extra_sqrt(self):
        sigma = np.nextafter(1.5, 0)
        self.assertEqual(round(sigma), 1)
        write_legacy_gsas(pattern([10], [sigma]), self.path)
        bank, = read_gsas(self.path)
        self.assertEqual(bank.sigma[0], 2)

    def test_uniform_gaps_and_original_mask_are_preserved(self):
        original = pattern([np.nan, 10.25, np.inf, 20.75, np.nan],
                           [np.nan, 3.1, np.inf, 4.9, np.nan], [0, 1, 0, 1, 0])
        info = write_legacy_gsas(original, self.path)
        bank, = read_gsas(self.path)
        np.testing.assert_array_equal(bank.intensity, [0, 10, 0, 21, 0])
        np.testing.assert_array_equal(bank.sigma, [0, 3, 0, 5, 0])
        np.testing.assert_allclose(bank.angles, original.angles)
        self.assertEqual(info["valid_mask"], original.valid.tolist())
        self.assertEqual(info["serialized_valid_mask"], original.valid.tolist())
        self.assertEqual(info["zero_weight_placeholder_points"], 3)

    def test_high_count_f8_limit_and_overflow_rejection(self):
        write_legacy_gsas(pattern([9999999], [3162]), self.path)
        self.assertIn("9999999.   3162.", self.path.read_text())
        saved = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "overflows F8.0"):
            write_legacy_gsas(pattern([10000000], [3162]), self.path, overwrite=True)
        self.assertEqual(saved, self.path.read_bytes())

    def test_positive_rounded_intensity_zero_esd_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "zero ESD"):
            write_legacy_gsas(pattern([10], [.5]), self.path)
        self.assertFalse(self.path.exists())
        info = write_legacy_gsas(pattern([.1], [.1]), self.path)
        self.assertEqual(info["valid_mask"], [True])
        self.assertEqual(info["serialized_valid_mask"], [False])
        self.assertEqual(info["valid_rounded_zero_esd_points"], 1)

    def test_historical_header_truncation_cannot_mislabel_step_or_origin(self):
        with self.assertRaisesRegex(ValueError, "GSTEP"):
            write_legacy_gsas(pattern([10, 20], step=.055), self.path)
        original = pattern([10, 20]); original.metadata["legacy_tstart_deg"] = .53999996
        with self.assertRaisesRegex(ValueError, "TSTART"):
            write_legacy_gsas(original, self.path)
        original = pattern([10, 20]); original.angles[1] += .001
        with self.assertRaisesRegex(ValueError, "supplied uniform"):
            write_legacy_gsas(original, self.path)

    def test_nonfinite_valid_values_and_no_clobber(self):
        write_legacy_gsas(pattern([1, 2]), self.path)
        saved = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            write_legacy_gsas(pattern([3, 4]), self.path)
        for intensity, sigma in [([np.nan], [1]), ([1], [np.inf]), ([1], [-1]), ([1e50], [1])]:
            with self.subTest(intensity=intensity, sigma=sigma):
                with self.assertRaises(ValueError):
                    write_legacy_gsas(pattern(intensity, sigma), self.path, overwrite=True)
        self.assertEqual(self.path.read_bytes(), saved)

    def test_calibrated_reference_only_when_explicit(self):
        write_legacy_gsas(pattern([1]), self.path, instrument_reference="calibrated_60min.instprm")
        bank, = read_gsas(self.path)
        self.assertEqual(bank.metadata["instrument_reference"], "calibrated_60min.instprm")
        self.assertFalse(self.path.with_suffix(".inst").exists())


if __name__ == "__main__":
    unittest.main()
