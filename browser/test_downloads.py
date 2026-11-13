"""Regression checks for selected-input provenance at the browser boundary."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from browser.downloads import finalize_outputs


def fingerprint(path):
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / "outputs"
        self.output.mkdir()
        self.inputs = [self.root / "first.bt1", self.root / "second.bt1"]
        for index, path in enumerate(self.inputs):
            path.write_text(f"selected source {index}\n")

    def pattern(self, name, members):
        gsas = self.output / (name + ".gsas")
        gsas.write_text("existing GSAS output remains byte-identical\n")
        csv = self.output / (name + ".csv")
        csv.write_text("two_theta_deg,intensity,sigma,valid,contributions\n"
                       "10,100,3,1,2\n10.05,0,0,0,0\n10.1,120,4,1,2\n")
        record = {"inputs": [{"path": str(p), **fingerprint(p)} for p in members],
                  "outputs": {p.name: fingerprint(p) for p in (gsas, csv)},
                  "warnings": ["original warning"]}
        (self.output / (name + ".json")).write_text(json.dumps(record))
        return {"name": name, "output": str(gsas), "valid_points": 2,
                "warnings": record["warnings"]}

    def finalize(self, summaries, name="combined"):
        return finalize_outputs(self.output, self.inputs, summaries, combine_name=name)

    def test_combined_export_preserves_inputs_gsas_csv_and_records_dat_hash(self):
        summary = self.pattern("combined", self.inputs)
        protected = self.inputs + [self.output / "combined.gsas", self.output / "combined.csv"]
        before = [p.read_bytes() for p in protected]
        result = self.finalize([summary])
        self.assertEqual([p.read_bytes() for p in protected], before)
        self.assertNotIn("fullprof", summary)
        record = json.loads((self.output / "combined.json").read_text())
        self.assertEqual(record["fullprof"], result[0]["fullprof"])
        self.assertEqual(record["outputs"]["combined.dat"], fingerprint(self.output / "combined.dat"))
        self.assertEqual(record["fullprof"]["points"], 2)
        self.assertEqual(record["fullprof"]["omitted_invalid_points"], 1)
        self.assertEqual(record["fullprof"]["source_csv_sha256"], record["outputs"]["combined.csv"]["sha256"])
        self.assertEqual(result[0]["warnings"][0], "original warning")

    def test_separate_conversion_checks_each_input_group(self):
        summaries = [self.pattern(p.stem, [p]) for p in self.inputs]
        result = self.finalize(summaries, name=None)
        self.assertEqual([r["name"] for r in result], ["first", "second"])
        self.assertTrue(all((self.output / (p.stem + ".dat")).is_file() for p in self.inputs))

    def test_first_input_only_cannot_be_presented_as_combined(self):
        summary = self.pattern("combined", self.inputs[:1])
        with self.assertRaisesRegex(ValueError, "every selected input"):
            self.finalize([summary])
        self.assertFalse((self.output / "combined.dat").exists())

    def test_normalization_order_must_match(self):
        summary = self.pattern("combined", self.inputs[::-1])
        with self.assertRaisesRegex(ValueError, "every selected input"):
            self.finalize([summary])

    def test_per_scan_cutoffs_must_match_prepared_input_metadata(self):
        summary = self.pattern("combined", self.inputs)
        metadata = self.output / "combined.json"
        record = json.loads(metadata.read_text())
        cutoffs = {str(self.inputs[0]): 1.0, str(self.inputs[1]): 1.9}
        for source in record["inputs"]:
            source["header"] = {"preparation": {"cutoff_m4_degrees": cutoffs[source["path"]]}}
        metadata.write_text(json.dumps(record))
        result = finalize_outputs(self.output, self.inputs, [summary],
                                  combine_name="combined", expected_cutoffs=cutoffs)
        self.assertEqual(result[0]["fullprof"]["points"], 2)

    def test_ignored_or_wrong_cutoff_prevents_downloads(self):
        summary = self.pattern("combined", self.inputs)
        metadata = self.output / "combined.json"
        record = json.loads(metadata.read_text())
        cutoffs = {str(p): 1.9 for p in self.inputs}
        for source in record["inputs"]:
            source["header"] = {"preparation": {"cutoff_m4_degrees": 1.0}}
        metadata.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "applied M4 cutoff"):
            finalize_outputs(self.output, self.inputs, [summary],
                             combine_name="combined", expected_cutoffs=cutoffs)
        self.assertFalse((self.output / "combined.dat").exists())

    def test_cutoff_request_rejects_missing_extra_and_invalid_values(self):
        summary = self.pattern("combined", self.inputs)
        valid = {str(p): 1.9 for p in self.inputs}
        invalid_requests = [{}, {**valid, str(self.root / "other.bt1"): 1.9}]
        invalid_requests.extend({**valid, str(self.inputs[0]): value}
                                for value in (None, "1.9", True, -1, float("nan"), float("inf")))
        for request in invalid_requests:
            with self.subTest(request=request), self.assertRaisesRegex(ValueError, "Cutoff request"):
                finalize_outputs(self.output, self.inputs, [summary],
                                 combine_name="combined", expected_cutoffs=request)

    def test_changed_input_is_rejected(self):
        summary = self.pattern("combined", self.inputs)
        self.inputs[1].write_text("changed input")
        with self.assertRaisesRegex(ValueError, "matching hashes"):
            self.finalize([summary])

    def test_missing_group_is_rejected_before_any_dat(self):
        summary = self.pattern("first", self.inputs[:1])
        with self.assertRaisesRegex(ValueError, "selected input groups"):
            self.finalize([summary], name=None)
        self.assertFalse(list(self.output.glob("*.dat")))

    def test_changed_output_is_rejected(self):
        summary = self.pattern("combined", self.inputs)
        (self.output / "combined.csv").write_text("changed")
        with self.assertRaisesRegex(ValueError, "hash/size mismatch"):
            self.finalize([summary])

    def test_missing_output_is_rejected(self):
        summary = self.pattern("combined", self.inputs)
        (self.output / "combined.gsas").unlink()
        with self.assertRaisesRegex(ValueError, "regular conversion file"):
            self.finalize([summary])

    def test_existing_dat_and_dangling_symlink_are_not_replaced(self):
        summary = self.pattern("combined", self.inputs)
        dat = self.output / "combined.dat"
        dat.write_text("keep")
        with self.assertRaises(FileExistsError):
            self.finalize([summary])
        self.assertEqual(dat.read_text(), "keep")
        dat.unlink()
        dat.symlink_to(self.root / "absent")
        with self.assertRaises(FileExistsError):
            self.finalize([summary])
        self.assertTrue(dat.is_symlink())

    def test_mismatched_valid_count_is_not_returned_as_success(self):
        summary = self.pattern("combined", self.inputs)
        summary["valid_points"] = 3
        with self.assertRaisesRegex(ValueError, "point count differs"):
            self.finalize([summary])

    def test_linked_metadata_cannot_modify_source(self):
        summary = self.pattern("combined", self.inputs)
        metadata = self.output / "combined.json"
        source = self.root / "original.json"
        metadata.rename(source)
        before = source.read_bytes()
        metadata.symlink_to(source)
        with self.assertRaisesRegex(ValueError, "regular conversion file"):
            self.finalize([summary])
        self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
