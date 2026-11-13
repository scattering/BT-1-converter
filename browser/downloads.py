"""Verify a private browser job and add FullProf downloads without reducing again.

Called synchronously after the CLI, before any output leaves MEMFS.
This is for the worker's private output directory, not acquisition directories.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

from browser.fullprof import write_fullprof_from_csv


def _regular(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular conversion file: {path.name}")
    return path


def _fingerprint(path: Path) -> dict:
    data = _regular(path).read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError("Invalid conversion output name")
    return value


def finalize_outputs(output_dir, input_paths, summaries, *, combine_name=None, expected_cutoffs=None):
    """Refuse missing/replaced inputs or outputs before offering any downloads.

    Provenance must identify every selected input, in normalization order, with
    matching bytes. This catches a single-scan result masquerading as a combined
    result. The .dat file uses existing CSV values; GSAS/CSV are never rewritten.
    """
    output = Path(output_dir).resolve(strict=True)
    inputs = [_regular(Path(p)).resolve(strict=True) for p in input_paths]
    if not inputs or len(set(inputs)) != len(inputs):
        raise ValueError("Expected distinct selected inputs")
    if any(p.is_relative_to(output) for p in inputs):
        raise ValueError("Download output directory must be separate from inputs")
    if expected_cutoffs is not None:
        bt1_paths = {str(p) for p in inputs if p.suffix.lower() == ".bt1"}
        if (not isinstance(expected_cutoffs, dict) or set(expected_cutoffs) != bt1_paths
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or v < 0 for v in expected_cutoffs.values())):
            raise ValueError("Cutoff request must contain one finite nonnegative value for every BT-1 input")
    groups = ([(_name(combine_name), inputs)] if combine_name is not None else
              [(_name(p.stem), [p]) for p in inputs])
    names = [name for name, _ in groups]
    if (len(set(names)) != len(names) or not isinstance(summaries, list)
            or [s.get("name") for s in summaries] != names):
        raise ValueError("Conversion results do not match the selected input groups")

    # Validate the entire job before creating any additional outputs.
    records = []
    for (name, members), summary in zip(groups, summaries):
        metadata_path = _regular(output / (name + ".json"))
        record = json.loads(metadata_path.read_text())
        expected_sources = [{"path": str(p), **_fingerprint(p)} for p in members]
        actual_sources = [{k: source.get(k) for k in ("path", "sha256", "bytes")}
                          for source in record.get("inputs", [])]
        if actual_sources != expected_sources:
            raise ValueError(f"{name}: result does not include every selected input in order with matching hashes")
        if expected_cutoffs is not None:
            for source in record["inputs"]:
                if source["path"] in expected_cutoffs:
                    actual = source.get("header", {}).get("preparation", {}).get("cutoff_m4_degrees")
                    if isinstance(actual, bool) or actual != expected_cutoffs[source["path"]]:
                        raise ValueError(f"{name}: applied M4 cutoff does not match the request for {Path(source['path']).name}")
        if summary.get("output") != str(output / (name + ".gsas")):
            raise ValueError(f"{name}: result points to an unexpected GSAS file")
        outputs = record.get("outputs", {})
        if not {name + ".gsas", name + ".csv"}.issubset(outputs):
            raise ValueError(f"{name}: required conversion outputs are missing")
        for filename, expected in outputs.items():
            if Path(filename).name != filename or not filename.startswith(name + "."):
                raise ValueError(f"{name}: unexpected conversion output path")
            if _fingerprint(output / filename) != expected:
                raise ValueError(f"{name}: output hash/size mismatch: {filename}")
        dat_path = output / (name + ".dat")
        if dat_path.exists() or dat_path.is_symlink():
            raise FileExistsError(f"FullProf output already exists: {dat_path.name}")
        records.append((metadata_path, record))

    updated = []
    for (name, _), summary, (metadata_path, record) in zip(groups, summaries, records):
        csv_path, dat_path = output / (name + ".csv"), output / (name + ".dat")
        fullprof = write_fullprof_from_csv(csv_path, dat_path)
        if fullprof["points"] != summary["valid_points"]:
            raise ValueError(f"{name}: FullProf point count differs from the valid conversion observations")
        fullprof["source_csv_sha256"] = record["outputs"][csv_path.name]["sha256"]
        record["fullprof"] = fullprof
        record["outputs"][dat_path.name] = _fingerprint(dat_path)
        warnings = list(record.get("warnings", [])) + fullprof.get("warnings", [])
        record["warnings"] = warnings
        metadata_path.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n")
        updated.append({**summary, "fullprof": fullprof, "warnings": warnings})
    return updated
