#!/usr/bin/env python3
"""Validate serialized CONST ESD files with the installed, complete GSAS-II reader.

Run with the existing GSAS-II environment, without installing anything::

    PYTHONDONTWRITEBYTECODE=1 \
      /Users/williamratcliff/opt/anaconda3/envs/gsasii/bin/python \
      /tmp/bt1-modern/validate_gsasii.py

No GUI is launched. This imports G2pwd_fxye normally, including its real
GSASIIobj/GSASIIpath dependencies, and calls ContentsValidator and Reader.
There are no module stubs, extracted methods, AST rewriting, or monkeypatches.
Comparisons use the serialized values, not the pre-serialization precision.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import platform
import sys
import traceback

sys.dont_write_bytecode = True


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--gsasii-root", type=Path,
                        default=Path("/Users/williamratcliff/GSAS-II/GSASII"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--files", nargs="+", type=Path, help="Explicit published GSAS files to validate instead of the original fixtures")
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output or root / "validation/gsasii_parser_validation.json"
    importer_path = args.gsasii_root.resolve() / "imports/G2pwd_fxye.py"
    interpreter = Path(sys.executable).resolve()
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "interpreter": str(Path(sys.executable)),
        "interpreter_resolved": str(interpreter),
        "interpreter_sha256": sha256(interpreter),
        "python_version": sys.version,
        "platform": platform.platform(),
        "importer": str(importer_path),
        "importer_sha256": sha256(importer_path),
        "harness": str(Path(__file__).resolve()),
        "harness_sha256": sha256(Path(__file__).resolve()),
        "reader_class": "G2pwd_fxye.GSAS_ReaderClass",
        "execution": {
            "normal_module_import": True,
            "real_dependencies": ["GSASIIobj", "GSASIIpath"],
            "module_stubs": False,
            "ast_or_method_extraction": False,
            "monkeypatches": False,
            "gui_launched": False,
            "gui_statement": "No application/frame is created; Reader is called with ParentFrame=None.",
            "original_files_modified": False,
        },
        "comparison_policy": {
            "reference": "bt1modern.gsas.read_gsas on the same serialized files",
            "intensity": "serialized I if I>0, otherwise 0 (GSAS-II ESD behavior)",
            "weights": "1/serialized_sigma**2 for serialized I>0, otherwise 0",
            "angle_atol_degrees": 1e-12,
            "intensity_rtol": 1e-14,
            "intensity_atol": 0.0,
            "weight_rtol": 1e-13,
            "weight_atol": 0.0,
            "precision_note": "Eight-column GSAS field quantization is accounted for by comparing parsed serialized values; original floating-point inputs are not treated as exact GSAS values.",
        },
        "files": [],
        "passed": False,
    }
    try:
        sys.path[:0] = [str(root), str(args.gsasii_root.resolve()),
                        str(args.gsasii_root.resolve() / "imports")]
        import numpy as np
        module = importlib.import_module("G2pwd_fxye")
        assert Path(module.__file__).resolve() == importer_path
        from bt1modern.gsas import read_gsas
        import bt1modern.gsas as own_reader
        report["reference_reader"] = str(Path(own_reader.__file__).resolve())
        report["reference_reader_sha256"] = sha256(Path(own_reader.__file__))
        report["numpy_version"] = np.__version__
        report["dependency_files"] = {
            name: str(Path(sys.modules[name].__file__).resolve())
            for name in ["GSASIIobj", "GSASIIpath"]
        }

        cases = [
            (root / "validation/gsasii_fixture.gsas", [2, 3, 3]),
            (root / "outputs/correct/Mn5Si001.gsas", [3300]),
            (root / "outputs/correct/Mn5Si002.gsas", [3300]),
        ]
        if args.files:
            cases = [(path.resolve(), [len(b.angles) for b in read_gsas(path)])
                     for path in args.files]
        for path, expected_lengths in cases:
            reference = read_gsas(path)
            reader = module.GSAS_ReaderClass()
            assert type(reader) is module.GSAS_ReaderClass
            result = {"path": str(path), "sha256": sha256(path),
                      "expected_bank_lengths": expected_lengths, "banks": []}
            report["files"].append(result)
            valid = bool(reader.ContentsValidator(str(path)))
            result["contents_validator"] = valid
            assert valid, reader.errors
            # Exercise the actual multibank repeat/buffer path, rather than
            # changing each bank selection or extracting the internal parser.
            buffer = {}
            for index, bank in enumerate(reference):
                ok = bool(reader.Reader(str(path), ParentFrame=None, buffer=buffer))
                assert ok, reader.errors
                actual_x, actual_y, actual_w = reader.powderdata[:3]
                expected_y = np.where(bank.intensity > 0, bank.intensity, 0.0)
                positive = bank.intensity > 0
                assert np.all(bank.sigma[positive] > 0), "Positive-I serialized ESD must be positive"
                expected_w = np.zeros_like(bank.sigma)
                expected_w[positive] = 1.0 / bank.sigma[positive]**2
                angles_match = bool(np.allclose(actual_x, bank.angles, rtol=0, atol=1e-12))
                intensity_match = bool(np.allclose(actual_y, expected_y, rtol=1e-14, atol=0))
                weights_match = bool(np.allclose(actual_w, expected_w, rtol=1e-13, atol=0))
                nonpositive = ~positive
                nonpositive_match = bool(np.all(actual_y[nonpositive] == 0) and
                                         np.all(actual_w[nonpositive] == 0))
                entry = {
                    "bank": bank.bank, "actual_bank": int(reader.powderentry[2]),
                    "points": len(bank.angles), "actual_points": len(actual_x),
                    "first_angle_degrees": float(actual_x[0]),
                    "last_angle_degrees": float(actual_x[-1]),
                    "angles_match": angles_match, "intensity_matches": intensity_match,
                    "weights_match": weights_match,
                    "max_absolute_angle_difference": float(np.max(np.abs(actual_x-bank.angles))),
                    "max_absolute_intensity_difference": float(np.max(np.abs(actual_y-expected_y))),
                    "max_absolute_weight_difference": float(np.max(np.abs(actual_w-expected_w))),
                    "positive_intensity_points": int(np.count_nonzero(positive)),
                    "serialized_zero_intensity_points": int(np.count_nonzero(bank.intensity == 0)),
                    "serialized_negative_intensity_points": int(np.count_nonzero(bank.intensity < 0)),
                    "nonpositive_clipped_and_zero_weighted": nonpositive_match,
                    "nonpositive_serialized_intensities": bank.intensity[nonpositive].tolist(),
                    "reader_repeat_after_bank": bool(reader.repeat),
                }
                entry["passed"] = bool(angles_match and intensity_match and weights_match and
                                       nonpositive_match and reader.powderentry[2] == bank.bank)
                result["banks"].append(entry)
                assert entry["passed"], f"Bank {bank.bank} differs from expected GSAS-II behavior"
                assert bool(reader.repeat) == (index < len(reference)-1)
            lengths = [b["actual_points"] for b in result["banks"]]
            assert lengths == expected_lengths, (path, lengths, expected_lengths)
            assert len(reference) == reader.numbanks == len(reader.dnames)
            result["actual_bank_lengths"] = lengths
            result["all_banks_read"] = True
            result["passed"] = all(b["passed"] for b in result["banks"])
            sidecar = path.with_suffix(".json")
            if sidecar.exists():
                gsas = json.loads(sidecar.read_text()).get("gsas", {})
                result["serialization_quantization_from_sidecar"] = {
                    k: gsas[k] for k in ["max_absolute_quantization", "max_relative_quantization"]
                    if k in gsas
                }
        if not args.files:
            fixture = report["files"][0]
            assert sum(b["serialized_zero_intensity_points"] for b in fixture["banks"]) == 1
            assert sum(b["serialized_negative_intensity_points"] for b in fixture["banks"]) == 1
        report["nonpositive_fixture_behavior_confirmed"] = any(
            b["serialized_zero_intensity_points"] + b["serialized_negative_intensity_points"] > 0
            for f in report["files"] for b in f["banks"])
        report["passed"] = all(f["passed"] for f in report["files"])
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error),
                           "traceback": traceback.format_exc()}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "output": str(output),
                      "files": [{"path": f["path"], "bank_lengths":
                                 [b["actual_points"] for b in f["banks"]]}
                                for f in report["files"]],
                      **({"error": report["error"]["message"]} if "error" in report else {})}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
