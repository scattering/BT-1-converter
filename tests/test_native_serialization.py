"""Byte-level GSAS compatibility checks independent of native execution."""
import tempfile
import unittest
from pathlib import Path

import numpy as np

from bt1modern.gsas import publish_fortran_gsas, read_gsas


def native_fixture(pairs=None, *, count=None, instrument=True, newline="\r\n"):
    """Use gformat's actual header spacing and F8.0 fields with decimal points."""
    pairs = pairs or [(2806, 40), (2537, 38), (0, 0), (1909, 33), (1536, 29), (1384, 28), (1132, 25)]
    count = len(pairs) if count is None else count
    records = (count + 4) // 5
    lines = ["Mn5Si  Mn5Si2 4g Acan Ge311 60min 2.5K                             second first "]
    if instrument:
        lines.append("Instrument parameter file:first.inst".ljust(79))
    lines.append(f"BANK  1 {count:8d}{records:8d} CONST     130.00      5.00 0 0 ESD".ljust(76))
    padded = list(pairs) + [(0, 0)] * (records * 5 - len(pairs))
    for start in range(0, len(padded), 5):
        lines.append("".join(f"{str(int(v)) + '.':>8s}" for pair in padded[start:start + 5] for v in pair))
    return (newline.join(lines) + newline).encode("ascii")


def native_bank_and_pairs(data):
    lines = data.decode("ascii").splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("BANK"))
    count = int(lines[index].split()[2])
    records = (count + 4) // 5
    pairs = "".join(lines[index + 1:index + 1 + records])[:count * 16]
    return lines[index], pairs


class NativeSerializationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "published.gsas"

    def tearDown(self):
        self.directory.cleanup()

    def test_actual_native_shaped_fixture_preserves_header_and_all_declared_pairs(self):
        native = native_fixture()
        header, pairs = native_bank_and_pairs(native)
        info = publish_fortran_gsas(native, self.path)
        result = self.path.read_bytes()
        published_header, published_pairs = native_bank_and_pairs(result)
        self.assertEqual(published_header, header.ljust(80))
        self.assertEqual(published_pairs, pairs)
        self.assertTrue(info["native_numeric_pairs_preserved"])
        self.assertTrue(info["native_bank_header_preserved"])
        self.assertEqual(info["max_absolute_quantization"], {"intensity": 0.0, "sigma": 0.0})
        self.assertIn("native rounding retained", info["quantization_scope"])
        self.assertTrue(all(len(line) == 80 for line in result.splitlines()))
        self.assertNotIn(b"\r", result)
        self.assertNotIn(b"Instrument parameter file:", result)
        self.assertEqual(info["removed_native_instrument_references"], ["first.inst"])
        self.assertFalse(self.path.with_suffix(".inst").exists())

    def test_non_multiple_of_five_tail_pairs_are_blank_not_extra_observations(self):
        info = publish_fortran_gsas(native_fixture(), self.path)
        self.assertEqual(self.path.read_text().splitlines()[-1][32:], " " * 48)
        self.assertEqual(info["blanked_undeclared_tail_pairs"], 3)
        bank, = read_gsas(self.path)
        self.assertEqual(bank.angles.size, 7)
        np.testing.assert_allclose(bank.angles, 1.3 + .05 * np.arange(7))

    def test_uniform_zero_weight_gap_remains_exact_and_mask_is_reported(self):
        info = publish_fortran_gsas(native_fixture(), self.path)
        bank, = read_gsas(self.path)
        np.testing.assert_array_equal(bank.intensity, [2806, 2537, 0, 1909, 1536, 1384, 1132])
        np.testing.assert_array_equal(bank.sigma, [40, 38, 0, 33, 29, 28, 25])
        self.assertEqual(info["valid_mask"], [True, True, False, True, True, True, True])
        self.assertEqual(info["valid_mask_source"], "serialized_sigma_positive")
        self.assertEqual(info["invalid_points"], 1)
        self.assertEqual(info["zero_weight_placeholder_points"], 1)
        self.assertEqual(info["omitted_invalid_points"], 0)
        self.assertEqual(len(info["banks"]), 1)
        self.assertTrue(any("not new observations" in warning for warning in info["warnings"]))

    def test_high_counts_fill_f8_point_zero_without_scientific_reformatting(self):
        native = native_fixture([(9999999, 3162), (1234567, 1111), (-999999, 1000)])
        publish_fortran_gsas(native, self.path)
        self.assertIn(b"9999999.   3162.", self.path.read_bytes())
        self.assertIn(b"1234567.   1111.", self.path.read_bytes())
        self.assertEqual(native_bank_and_pairs(native)[1], native_bank_and_pairs(self.path.read_bytes())[1])
        bank, = read_gsas(self.path)
        np.testing.assert_array_equal(bank.intensity, [9999999, 1234567, -999999])

    def test_explicit_instrument_reference_replaces_generated_one(self):
        info = publish_fortran_gsas(native_fixture(), self.path, instrument_reference="measured_60min.instprm")
        bank, = read_gsas(self.path)
        self.assertEqual(bank.metadata["instrument_reference"], "measured_60min.instprm")
        self.assertEqual(info["instrument_reference"], "measured_60min.instprm")
        self.assertNotIn("first.inst", self.path.read_text())

    def test_valid_nonpositive_intensity_is_preserved_and_gsasii_limit_reported(self):
        info = publish_fortran_gsas(native_fixture([(0, 2), (-3, 1), (10, 3)]), self.path)
        self.assertEqual(info["valid_points"], 3)
        self.assertEqual(info["valid_nonpositive_intensity_points"], 2)
        self.assertTrue(any("GSAS-II" in warning for warning in info["warnings"]))

    def test_zero_sigma_positive_intensity_is_not_changed_or_given_weight(self):
        info = publish_fortran_gsas(native_fixture([(1, 0), (10, 3)]), self.path)
        bank, = read_gsas(self.path)
        self.assertEqual(bank.intensity[0], 1)
        self.assertEqual(bank.sigma[0], 0)
        self.assertEqual(info["valid_mask"], [False, True])
        self.assertEqual(info["zero_weight_placeholder_points"], 0)

    def test_declared_nonfinite_overflow_negative_and_blank_fields_rejected(self):
        base = native_fixture()
        for field in (b"********", b"     NaN", b"     Inf", b"        "):
            with self.subTest(field=field):
                bad = base.replace(b"   2806.", field, 1)
                with self.assertRaises(ValueError):
                    publish_fortran_gsas(bad, self.path)
                self.assertFalse(self.path.exists())
        with self.assertRaisesRegex(ValueError, "Negative"):
            publish_fortran_gsas(base.replace(b"     40.", b"     -1.", 1), self.path)
        self.assertFalse(self.path.exists())

    def test_unused_tail_values_are_discarded_without_interpretation(self):
        lines = native_fixture().splitlines()
        lines[-1] = lines[-1][:32] + b"*" * 48
        info = publish_fortran_gsas(b"\n".join(lines) + b"\n", self.path)
        self.assertEqual(info["blanked_undeclared_tail_pairs"], 3)
        self.assertNotIn("*", self.path.read_text())

    def test_invalid_input_never_clobbers_existing_destination(self):
        publish_fortran_gsas(native_fixture(), self.path)
        previous = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            publish_fortran_gsas(native_fixture(), self.path)
        with self.assertRaises(ValueError):
            publish_fortran_gsas(native_fixture().replace(b"   2806.", b"********", 1), self.path, overwrite=True)
        self.assertEqual(previous, self.path.read_bytes())

    def test_header_grid_and_record_validation(self):
        base = native_fixture()
        invalid = [
            base.replace(b"       2 CONST", b"       3 CONST", 1),
            base.replace(b"      5.00", b"      0.00", 1),
            base.replace(b"      5.00", b"       NaN", 1),
            base.replace(b"CONST", b"LOG  ", 1),
            base.replace(b" ESD", b" STD", 1),
            b"\n".join(base.splitlines()[:-1]) + b"\n",
            base + b"BANK 2 1 1 CONST 10 5 0 0 ESD\n" + b"      1.      1.\n",
            base + b"      1.      1.\n",
        ]
        for candidate in invalid:
            with self.subTest(candidate=candidate[:100]):
                with self.assertRaises(ValueError):
                    publish_fortran_gsas(candidate, self.path)
                self.assertFalse(self.path.exists())

    def test_control_characters_overwide_records_and_reference_injection_rejected(self):
        base = native_fixture()
        candidates = [base.replace(b"Mn5Si", b"Mn\tSi", 1), base + b"\xff", b"", b"\n"]
        lines = base.splitlines(); lines[-1] += b"x"
        candidates.append(b"\n".join(lines) + b"\n")
        for candidate in candidates:
            with self.subTest(candidate=candidate[:20]):
                with self.assertRaises(ValueError):
                    publish_fortran_gsas(candidate, self.path)
        with self.assertRaises(ValueError):
            publish_fortran_gsas(base, self.path, instrument_reference="x.inst\nBANK fake")
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
