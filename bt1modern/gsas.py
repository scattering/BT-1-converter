"""GSAS constant-step ESD exchange, with explicit gaps and no invented .inst.

ESD records contain five fixed-width (intensity, standard-deviation) pairs.
The default is one uniform grid: invalid bins are encoded as (I, sigma)=(0, 0)
and their validity mask is returned for the caller's metadata sidecar. These
zero-weight placeholders are not measured zero intensities. Optional ``split``
output omits invalid bins by separating valid runs into banks. Partial final
records are padded with spaces: some readers ignore BANK's count.

GSAS-II's legacy ESD importer sets nonpositive intensities to zero weight even
when their ESD is positive. We preserve such valid measurements in the file and
report that compatibility limitation; CSV/covariance remains authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
import re
from typing import Any

import numpy as np


@dataclass
class GSASBank:
    bank: int
    angles: np.ndarray
    intensity: np.ndarray
    sigma: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)


def _field(value: float, *, positive: bool = False) -> str:
    """Best practical F8.3..F8.0 field, with E notation for small/large values."""
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError("GSAS fields must be finite; valid ESDs must be positive")
    if value == 0:
        return "   0.000"
    # Compare decimal and exponent candidates. F8.3 alone can badly distort a
    # small ESD (e.g. 0.0006 -> 0.001); scientific notation prevents that loss.
    choices = []
    for decimals in range(3, -1, -1):
        candidate = f"{value:.{decimals}f}"
        if len(candidate) <= 8 and float(candidate) != 0:
            choices.append((abs(float(candidate) - value), 0, -decimals, candidate))
    for decimals in range(6, -1, -1):
        candidate = f"{value:.{decimals}E}"
        if len(candidate) <= 8:
            rounded = float(candidate)
            if math.isfinite(rounded) and rounded != 0:
                choices.append((abs(rounded - value), 1, -decimals, candidate))
    if choices:
        return min(choices)[-1].rjust(8)
    raise ValueError(f"Value cannot be represented in a finite GSAS ESD field: {value!r}")


def _title(text: str) -> str:
    text = " ".join(str(text).split())
    text = "".join(c if 32 <= ord(c) < 127 else "?" for c in text)
    return (text or "BT-1 reduced powder pattern")[:80].ljust(80)


def _instrument_reference(reference: str | Path) -> str:
    text = str(reference)
    if not text or len(text) > 54 or any(not 32 <= ord(c) < 127 for c in text):
        raise ValueError("Instrument reference must be 1–54 printable ASCII characters")
    if ":" in text:
        raise ValueError("Instrument reference cannot contain a colon")
    return "Instrument parameter file:" + text


def write_gsas(pattern: Any, path: str | Path, *, title: str | None = None,
               instrument_reference: str | Path | None = None,
               overwrite: bool = False, step: float | None = None,
               gap_policy: str = "uniform") -> dict[str, Any]:
    """Write a ReducedPattern-compatible object; return precision/compatibility notes.

    ``angles`` are degrees, ``sigma`` are standard deviations (not variances).
    A one-point input needs ``step`` or metadata ``output_step``/``output_step_deg``.
    ``gap_policy='uniform'`` writes every angle in one BANK. Invalid input bins
    become (0, 0), denoting missing observations with zero statistical weight;
    no interpolation or artificial observation is introduced. The returned
    ``valid_mask`` must be retained in the caller's sidecar because plain GSAS
    has no separate validity flag. ``gap_policy='split'`` writes only valid
    contiguous runs, with a separate BANK per run.

    GSAS-II's ESD importer gives nonpositive intensities zero weight even when
    sigma is positive. It also cannot reconstruct correlations from adjacent
    interpolated bins. Keep the full-precision data, mask and covariance
    sidecars for scientific analysis; this file is an exchange representation.
    Does not create, change, or validate any referenced instrument parameter file.
    No sidecar file is created implicitly.
    """
    path = Path(path)
    if gap_policy not in ("uniform", "split"):
        raise ValueError("gap_policy must be 'uniform' or 'split'")
    x = np.asarray(pattern.angles, dtype=float)
    y = np.asarray(pattern.intensity, dtype=float)
    sigma = np.asarray(pattern.sigma, dtype=float)
    valid = np.asarray(pattern.valid, dtype=bool)
    if x.ndim != 1 or not x.size or any(v.shape != x.shape for v in (y, sigma, valid)):
        raise ValueError("angles, intensity, sigma and valid must be equal nonempty 1-D arrays")
    if not np.all(np.isfinite(x)):
        raise ValueError("All bin angles must be finite")
    if not np.any(valid):
        raise ValueError("Cannot write a GSAS file without any valid bins")
    if not np.all(np.isfinite(y[valid])) or not np.all(np.isfinite(sigma[valid])) or np.any(sigma[valid] <= 0):
        raise ValueError("Valid bins require finite intensity and strictly positive ESD")
    metadata = dict(getattr(pattern, "metadata", {}) or {})
    if x.size > 1:
        differences = np.diff(x)
        inferred = float(np.median(differences))
        if inferred <= 0 or not np.allclose(differences, inferred, rtol=1e-8, atol=1e-9):
            raise ValueError("CONST ESD requires a strictly increasing, constant-step angle grid")
        if step is not None and not np.isclose(step, inferred, rtol=1e-8, atol=1e-9):
            raise ValueError("Explicit step disagrees with angle grid")
        step = inferred
    elif step is None:
        step = metadata.get("output_step_deg", metadata.get("output_step"))
    if step is None or not math.isfinite(float(step)) or float(step) <= 0:
        raise ValueError("A single-point pattern requires a finite positive step")
    step = float(step)
    if instrument_reference is not None:
        instrument_line = _instrument_reference(instrument_reference)
    else:
        instrument_line = None
    indices = np.flatnonzero(valid)
    runs = ([np.arange(x.size)] if gap_policy == "uniform" else
            np.split(indices, np.flatnonzero(np.diff(indices) != 1) + 1))
    lines = [_title(title if title is not None else metadata.get("title", "BT-1 reduced powder pattern"))]
    if instrument_line:
        lines.append(instrument_line.ljust(80))
    warnings = []
    if instrument_reference is None:
        warnings.append("No calibrated instrument parameter file supplied; no .inst was generated.")
        lines.append("# No calibrated instrument parameter file supplied.".ljust(80))
    nonpositive = int(np.count_nonzero(y[valid] <= 0))
    if nonpositive:
        warnings.append(f"GSAS-II ESD importer discards weight for {nonpositive} valid nonpositive intensity bin(s).")
        lines.append("# GSAS-II sets nonpositive intensities to zero weight; retain CSV.".ljust(80))
    invalid_count = int((~valid).sum())
    if gap_policy == "uniform" and invalid_count:
        warnings.append(f"{invalid_count} invalid bin(s) retained on the uniform grid as (I, sigma)=(0, 0), "
                        "zero-weight placeholders, not measured zero intensities; retain the valid_mask sidecar.")
    elif len(runs) > 1:
        warnings.append("Invalid-bin gaps were split into separate CONST ESD banks.")
    max_abs = {"intensity": 0.0, "sigma": 0.0}
    max_rel = {"intensity": 0.0, "sigma": 0.0}
    bank_records = []
    for number, run in enumerate(runs, 1):
        npoints = len(run)
        records = (npoints + 4) // 5
        # Explicitly separated fields; 12 significant digits exceed the raw
        # angle precision and avoid accidental adjacent full-width numbers.
        start_text = f"{100 * x[run[0]]:.12g}"
        step_text = f"{100 * step:.12g}"
        header = f"BANK {number} {npoints} {records} CONST {start_text} {step_text} 0 0 ESD"
        if len(header) > 80:
            raise ValueError("GSAS bank header exceeds 80 characters")
        lines.append(header.ljust(80))
        fields = []
        for i in run:
            values = (("intensity", y[i]), ("sigma", sigma[i])) if valid[i] else (("intensity", 0.0), ("sigma", 0.0))
            for key, value in values:
                rendered = _field(value, positive=(key == "sigma" and valid[i]))
                difference = abs(float(rendered) - value)
                max_abs[key] = max(max_abs[key], difference)
                if value:
                    max_rel[key] = max(max_rel[key], difference / abs(value))
                fields.append(rendered)
        for i in range(0, len(fields), 10):
            lines.append("".join(fields[i:i + 10]).ljust(80))
        bank_records.append({"bank": number, "points": npoints, "records": records,
                             "first_input_index": int(run[0]), "last_input_index": int(run[-1])})
    if max(max_rel.values()) > 0.005:
        warnings.append("At least one fixed-width value lost over 0.5% precision; inspect quantization metadata.")
    # All validation/rendering happens before exclusive creation. No clobber by default.
    with path.open("w" if overwrite else "x", encoding="ascii", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
    return {"path": str(path), "format": "GSAS CONST ESD", "banks": bank_records,
            "gap_policy": gap_policy, "uniform_grid": gap_policy == "uniform",
            "valid_mask": valid.tolist(), "valid_mask_source": "input_valid_mask",
            "input_points": int(x.size), "valid_points": int(valid.sum()),
            "invalid_points": invalid_count,
            "zero_weight_placeholder_points": invalid_count if gap_policy == "uniform" else 0,
            "omitted_invalid_points": invalid_count if gap_policy == "split" else 0,
            "valid_nonpositive_intensity_points": nonpositive,
            "max_absolute_quantization": max_abs, "max_relative_quantization": max_rel,
            "instrument_reference": str(instrument_reference) if instrument_reference is not None else None,
            "warnings": warnings}


def _number(field: str, *, line: int) -> float:
    try:
        value = float(field.replace("D", "E").replace("d", "e"))
    except ValueError as error:
        raise ValueError(f"Invalid GSAS numeric field on line {line}: {field!r}") from error
    if not math.isfinite(value):
        raise ValueError(f"Nonfinite GSAS numeric field on line {line}")
    return value


def write_legacy_gsas(pattern: Any, path: str | Path, *, title: str | None = None,
                      instrument_reference: str | Path | None = None,
                      overwrite: bool = False) -> dict[str, Any]:
    """Write the historical GFORMAT numeric representation entirely in Python.

    The legacy reducer must supply its final REAL*4 intensity and standard
    deviation. In particular, it must evaluate ``1/SQRT(sum_weight)`` in
    float32 before passing sigma here; computing a float64 variance and taking
    its square root here would not reproduce Fortran's intermediate rounding.
    This writer casts supplied final values to float32, then emits F8.0 fields
    with the trailing decimal point and nearest-even integer rounding used by
    the pinned gfortran oracle. It does not recompute uncertainties.

    ``legacy_tstart_deg`` and ``legacy_step_deg`` metadata preserve the native
    REAL arithmetic scalars. The BANK header uses the original
    ``INT(REAL32(100*TSTART))`` and ``INT(REAL32(100*GSTEP))`` rule. A header that
    changes the intended grid beyond REAL rounding is rejected, since copying
    the historical truncation bug would mislabel the measured angles.

    The output is one uniform BANK. Invalid bins are zero-weight (0,0) rows;
    valid values are rounded only for GSAS exchange, while the input object is
    unchanged. Retain full-precision CSV and the returned validity mask in a
    sidecar. Positive serialized intensity with an ESD rounded to zero is
    rejected because it would create an unusable weighted observation.
    Valid zero/nonpositive intensities may still lose weight in GSAS-II.

    Intentional publication differences from original GFORMAT are blank tail
    pairs beyond NCH, LF/80-column records, and no invented instrument file.
    No native compiler, subprocess, network access or sidecar write is used.
    """
    x = np.asarray(pattern.angles, dtype=float)
    y = np.asarray(pattern.intensity, dtype=float)
    sigma = np.asarray(pattern.sigma, dtype=float)
    valid = np.asarray(pattern.valid, dtype=bool)
    if x.ndim != 1 or not x.size or any(array.shape != x.shape for array in (y, sigma, valid)):
        raise ValueError("angles, intensity, sigma and valid must be equal nonempty 1-D arrays")
    if not np.all(np.isfinite(x)) or not np.any(valid):
        raise ValueError("Legacy GSAS needs finite angles and at least one valid bin")
    if not np.all(np.isfinite(y[valid])) or not np.all(np.isfinite(sigma[valid])) or np.any(sigma[valid] <= 0):
        raise ValueError("Valid legacy bins require finite intensity and strictly positive ESD")
    metadata = dict(getattr(pattern, "metadata", {}) or {})
    nominal_step = metadata.get("legacy_nominal_input_step_deg", metadata.get("output_step_deg", metadata.get("output_step")))
    if nominal_step is None and len(x) > 1:
        nominal_step = float(np.median(np.diff(x)))
    with np.errstate(over="ignore", invalid="ignore"):
        start32 = np.float32(metadata.get("legacy_tstart_deg", x[0]))
        step32 = np.float32(metadata.get("legacy_step_deg", nominal_step if nominal_step is not None else np.nan))
        start_cdeg32 = np.float32(np.float32(100) * start32)
        step_cdeg32 = np.float32(np.float32(100) * step32)
        y32 = y.astype(np.float32)
        sigma32 = sigma.astype(np.float32)
    if nominal_step is None or not math.isfinite(float(nominal_step)) or float(nominal_step) <= 0:
        raise ValueError("Legacy GSAS requires a finite positive nominal input step")
    if not np.isfinite(start_cdeg32) or not np.isfinite(step_cdeg32) or step32 <= 0:
        raise ValueError("Legacy GSAS grid is not representable as finite REAL*4")
    if not np.all(np.isfinite(y32[valid])) or not np.all(np.isfinite(sigma32[valid])) or np.any(sigma32[valid] <= 0):
        raise ValueError("Legacy valid fields overflow or underflow REAL*4")
    start_cdeg, step_cdeg = int(start_cdeg32), int(step_cdeg32)
    header_start, header_step = start_cdeg / 100, step_cdeg / 100
    if step_cdeg <= 0 or not math.isclose(header_step, float(nominal_step), rel_tol=0, abs_tol=1e-9):
        raise ValueError("Historical INT(100*GSTEP) truncation would mislabel the angle step")
    origin_tolerance = max(1e-6, 4 * abs(float(np.spacing(start32))))
    if abs(header_start - float(start32)) > origin_tolerance:
        raise ValueError("Historical INT(100*TSTART) truncation would mislabel the angle start")
    expected = header_start + header_step * np.arange(len(x))
    if not np.allclose(x, expected, rtol=0, atol=1e-8):
        raise ValueError("Legacy GSAS BANK header does not describe the supplied uniform angle grid")
    count = len(x)
    records = (count + 4) // 5
    start_field, step_field = f"{start_cdeg:10.2f}", f"{step_cdeg:10.2f}"
    if len(f"{count:8d}") != 8 or len(f"{records:8d}") != 8 or len(start_field) != 10 or len(step_field) != 10:
        raise ValueError("Legacy GSAS BANK header field overflow")
    lines = [_title(title if title is not None else metadata.get("title", "BT-1 legacy reduced powder pattern"))]
    if instrument_reference is not None:
        lines.append(_instrument_reference(instrument_reference).ljust(80))
    lines.append(f"BANK  1 {count:8d}{records:8d} CONST {start_field}{step_field} 0 0 ESD".ljust(80))
    fields = []
    rendered_values = np.zeros((count, 2))
    max_abs = {"intensity": 0.0, "sigma": 0.0}
    max_rel = {"intensity": 0.0, "sigma": 0.0}
    for index in range(count):
        pair = (y32[index], sigma32[index]) if valid[index] else (0.0, 0.0)
        for column, (key, value) in enumerate(zip(("intensity", "sigma"), pair)):
            # Python's .0f implements nearest-even ties; adding a decimal
            # point reproduces Fortran F8.0 rather than integer I8 output.
            field = (f"{float(value):.0f}" + ".").rjust(8)
            if len(field) != 8:
                raise ValueError(f"Legacy GSAS {key}={float(value)} overflows F8.0")
            rounded = float(field)
            rendered_values[index, column] = rounded
            if valid[index]:
                original = y[index] if column == 0 else sigma[index]
                difference = abs(rounded - original)
                max_abs[key] = max(max_abs[key], difference)
                if original:
                    max_rel[key] = max(max_rel[key], difference / abs(original))
            fields.append(field)
        if valid[index] and rendered_values[index, 0] > 0 and rendered_values[index, 1] == 0:
            raise ValueError(f"Legacy F8.0 rounded positive intensity bin {index} to zero ESD; retain full-precision output instead")
    for offset in range(0, len(fields), 10):
        lines.append("".join(fields[offset:offset + 10]).ljust(80))
    serialized_valid = rendered_values[:, 1] > 0
    invalid_count = int((~valid).sum())
    rounded_zero_esd = int(np.count_nonzero(valid & ~serialized_valid))
    nonpositive = int(np.count_nonzero(valid & (rendered_values[:, 0] <= 0)))
    warnings = ["Legacy F8.0 GSAS rounds REAL*4 intensity and ESD to integers; retain full-precision CSV and validity metadata.",
                "Unused final-record pairs are intentionally blank instead of historical padding beyond NCH."]
    if instrument_reference is None:
        warnings.append("No calibrated instrument parameter file supplied; no .inst was generated.")
    if invalid_count:
        warnings.append(f"{invalid_count} invalid bin(s) remain as uniform-grid (0,0) zero-weight placeholders, not measured zero intensities.")
    if rounded_zero_esd:
        warnings.append(f"{rounded_zero_esd} valid nonpositive serialized intensity bin(s) have ESD rounded to zero; retain original validity and full precision.")
    if nonpositive:
        warnings.append(f"GSAS-II ESD importer discards weight for {nonpositive} valid nonpositive serialized intensity bin(s).")
    path = Path(path)
    with path.open("w" if overwrite else "x", encoding="ascii", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
    return {"path": str(path), "format": "GSAS CONST ESD", "serialization": "legacy-python-F8.0",
            "banks": [{"bank": 1, "points": count, "records": records,
                       "first_input_index": 0, "last_input_index": count - 1}],
            "gap_policy": "uniform", "uniform_grid": True, "input_points": count,
            "valid_points": int(valid.sum()), "invalid_points": invalid_count,
            "omitted_invalid_points": 0, "zero_weight_placeholder_points": invalid_count,
            "valid_mask": valid.tolist(), "valid_mask_source": "input_valid_mask",
            "serialized_valid_mask": serialized_valid.tolist(), "serialized_valid_points": int(serialized_valid.sum()),
            "valid_rounded_zero_esd_points": rounded_zero_esd,
            "valid_nonpositive_intensity_points": nonpositive,
            "max_absolute_quantization": max_abs, "max_relative_quantization": max_rel,
            "quantization_scope": "input floating-point arrays to float32 then nearest-even F8.0 integer fields",
            "historical_header_start_centidegrees": start_cdeg,
            "historical_header_step_centidegrees": step_cdeg,
            "legacy_tstart_deg": float(start32), "legacy_step_deg": float(step32),
            "blanked_undeclared_tail_pairs": records * 5 - count,
            "instrument_reference": str(instrument_reference) if instrument_reference is not None else None,
            "warnings": warnings}


def publish_fortran_gsas(native_bytes: bytes, path: str | Path, *,
                         instrument_reference: str | Path | None = None,
                         overwrite: bool = False) -> dict[str, Any]:
    """Publish one native Fortran CONST ESD BANK without reformatting its values.

    This is a serialization-only operation. Every declared 16-character
    intensity/ESD pair and the native BANK header are copied byte for byte,
    apart from padding short lines to 80 columns. Native gformat normally uses
    F8.0 fields, including their trailing decimal point. Existing integer
    rounding is therefore retained; the reported zero quantization means *no
    additional* numeric rounding here, not lossless recovery of pre-Fortran
    floating-point values. Nonfinite values, overflow stars, negative ESDs,
    missing declared fields, multiple banks and malformed headers are rejected
    before the destination is opened.

    Unused final-record pairs beyond NCH are replaced with spaces. They are
    not observations and some GSAS-II versions otherwise read those legacy
    zero pairs as extra points. All lines are normalized to 80 ASCII columns
    and LF endings. A generated native instrument reference is removed;
    ``instrument_reference`` optionally supplies a caller-selected calibrated
    reference. This function neither creates nor validates that instrument
    file, and the caller should retain the untouched native output separately.

    A native zero ESD denotes zero statistical weight in the exchange file.
    The returned ``valid_mask`` is inferred solely from positive serialized
    ESDs: it cannot distinguish a missing bin from a small positive native
    uncertainty rounded to zero. Preserve the original reduction mask and
    full-precision sidecars when available. Zero-ESD rows remain on the same
    uniform grid and are never promoted to observations. GSAS-II additionally
    drops weight for nonpositive intensities despite positive ESDs, and this
    format cannot represent interpolation covariance. No sidecar is written
    implicitly; return metadata is intended for the caller's JSON sidecar.
    """
    if not isinstance(native_bytes, (bytes, bytearray)):
        raise TypeError("native_bytes must be bytes or bytearray")
    try:
        text = bytes(native_bytes).decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("Native GSAS must contain ASCII text") from error
    if any((ord(c) < 32 and c not in "\r\n") or ord(c) == 127 for c in text):
        raise ValueError("Native GSAS contains unsupported control characters")
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        raise ValueError("Native GSAS requires a nonempty title")
    for number, line in enumerate(lines, 1):
        if len(line) > 80 and line[80:].strip():
            raise ValueError(f"Native GSAS line {number} exceeds 80 columns")
    # Trailing spaces outside the record have no semantic content; preserve
    # every character inside the fixed-width record.
    lines = [line[:80].ljust(80) for line in lines]
    instrument_line = (_instrument_reference(instrument_reference).ljust(80)
                       if instrument_reference is not None else None)
    output = [lines[0]]
    if instrument_line is not None:
        output.append(instrument_line)
    removed_references = []
    i = 1
    while i < len(lines) and not lines[i].startswith("BANK"):
        line = lines[i]
        if line.lower().startswith("instrument parameter file:"):
            removed_references.append(line.split(":", 1)[1].strip())
        elif line.startswith("#"):
            output.append(line)
        elif line.strip():
            raise ValueError(f"Unexpected native GSAS preamble on line {i + 1}")
        i += 1
    if i == len(lines):
        raise ValueError("Native GSAS contains no BANK header")
    header = lines[i]
    tokens = header.split()
    if len(tokens) != 10 or tokens[4] != "CONST" or tokens[-1] != "ESD":
        raise ValueError("Native publisher requires one BANK with CONST ESD format")
    try:
        bank_number, count, records = map(int, tokens[1:4])
    except ValueError as error:
        raise ValueError("Invalid native BANK integer header") from error
    if bank_number <= 0 or count <= 0 or records != (count + 4) // 5:
        raise ValueError("Invalid native BANK number/count/record count")
    start = _number(tokens[5], line=i + 1) / 100
    step = _number(tokens[6], line=i + 1) / 100
    for coefficient in tokens[7:9]:
        _number(coefficient, line=i + 1)
    if step <= 0 or not math.isfinite(start + step * (count - 1)):
        raise ValueError("Native CONST grid must have a finite positive step and finite endpoint")
    output.append(header)
    i += 1
    values = []
    tail_pairs_blank = 0
    for record in range(records):
        if i >= len(lines) or lines[i].startswith(("BANK", "#")):
            raise ValueError(f"Truncated native ESD bank: expected {records} records")
        raw = lines[i]
        declared_pairs = min(5, count - 5 * record)
        for pair_index in range(declared_pairs):
            pair = raw[16 * pair_index:16 * (pair_index + 1)]
            intensity = _number(pair[:8], line=i + 1)
            sigma = _number(pair[8:], line=i + 1)
            if sigma < 0:
                raise ValueError(f"Negative native ESD on line {i + 1}")
            values.append((intensity, sigma))
        tail_pairs_blank += 5 - declared_pairs
        output.append(raw[:16 * declared_pairs].ljust(80))
        i += 1
    if any(line.strip() for line in lines[i:]):
        raise ValueError("Native publisher requires exactly one BANK and no extra data records")
    values = np.asarray(values, dtype=float)
    valid = values[:, 1] > 0
    invalid_count = int((~valid).sum())
    nonpositive = int(np.count_nonzero(valid & (values[:, 0] <= 0)))
    warnings = ["Native fixed-width numeric fields were preserved; existing Fortran rounding was not reversed."]
    if instrument_reference is None:
        warnings.append("No calibrated instrument parameter file supplied; native reference removed and no .inst was generated.")
    if invalid_count:
        warnings.append(f"{invalid_count} zero-ESD bin(s) retained on the uniform grid with zero weight; "
                        "they are not new observations. Validity is inferred from serialized ESDs; retain the reduction mask sidecar.")
    if nonpositive:
        warnings.append(f"GSAS-II ESD importer discards weight for {nonpositive} valid nonpositive intensity bin(s).")
    path = Path(path)
    with path.open("w" if overwrite else "x", encoding="ascii", newline="\n") as stream:
        stream.write("\n".join(output) + "\n")
    return {"path": str(path), "format": "GSAS CONST ESD",
            "banks": [{"bank": bank_number, "points": count, "records": records,
                       "first_input_index": 0, "last_input_index": count - 1}],
            "gap_policy": "uniform", "uniform_grid": True,
            "input_points": count, "valid_points": int(valid.sum()),
            "invalid_points": invalid_count, "omitted_invalid_points": 0,
            "zero_weight_placeholder_points": int(np.count_nonzero((values[:, 0] == 0) & ~valid)),
            "valid_mask": valid.tolist(), "valid_mask_source": "serialized_sigma_positive",
            "valid_nonpositive_intensity_points": nonpositive,
            "max_absolute_quantization": {"intensity": 0.0, "sigma": 0.0},
            "max_relative_quantization": {"intensity": 0.0, "sigma": 0.0},
            "quantization_scope": "additional publication rounding only; existing native rounding retained",
            "native_numeric_pairs_preserved": True, "native_bank_header_preserved": True,
            "blanked_undeclared_tail_pairs": tail_pairs_blank,
            "removed_native_instrument_references": removed_references,
            "instrument_reference": str(instrument_reference) if instrument_reference is not None else None,
            "warnings": warnings}


def read_gsas(path: str | Path) -> list[GSASBank]:
    """Read GSAS CONST ESD banks, honoring declared counts and blank padding.

    Preserves finite nonpositive intensities. Sigma must be nonnegative so
    explicit zero-weight bins can be read. Zero sigma is not a measurement with
    perfect precision: it marks zero statistical weight in this exchange format.
    Older gformat's extra zero pairs beyond NCH are accepted and ignored.
    """
    path = Path(path)
    lines = path.read_text(encoding="latin-1").splitlines()
    if not lines:
        raise ValueError("Empty GSAS file")
    title = lines[0].rstrip()
    instrument = None
    comments = []
    banks = []
    seen = set()
    i = 1
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("#"):
            comments.append(line[1:].strip())
            i += 1
            continue
        if line.lower().startswith("instrument parameter file:"):
            instrument = line.split(":", 1)[1].strip()
            i += 1
            continue
        if not line.startswith("BANK"):
            raise ValueError(f"Expected BANK header on line {i + 1}")
        tokens = line.split()
        if len(tokens) < 10 or tokens[4] != "CONST" or tokens[-1] != "ESD":
            raise ValueError(f"Only GSAS CONST ESD banks are supported (line {i + 1})")
        try:
            number, count, records = map(int, tokens[1:4])
        except ValueError as error:
            raise ValueError(f"Invalid BANK integer header on line {i + 1}") from error
        if number <= 0 or number in seen or count <= 0 or records != (count + 4) // 5:
            raise ValueError(f"Invalid bank number/count/record count on line {i + 1}")
        start = _number(tokens[5], line=i + 1) / 100
        step = _number(tokens[6], line=i + 1) / 100
        if step <= 0:
            raise ValueError("GSAS CONST angle step must be positive")
        seen.add(number)
        values = []
        i += 1
        for record in range(records):
            if i >= len(lines) or lines[i].startswith(("BANK", "#")):
                raise ValueError(f"Truncated ESD bank {number}: expected {records} data records")
            raw = lines[i]
            if len(raw) > 80 and raw[80:].strip():
                raise ValueError(f"ESD record exceeds 80 columns on line {i + 1}")
            raw = raw[:80].ljust(80)
            blank_seen = False
            for column in range(0, 80, 16):
                pair = raw[column:column + 16]
                if not pair.strip():
                    blank_seen = True
                    continue
                if blank_seen:
                    raise ValueError(f"Nonblank data follow padding on line {i + 1}")
                intensity = _number(pair[:8], line=i + 1)
                sigma = _number(pair[8:], line=i + 1)
                if sigma < 0:
                    raise ValueError(f"Negative ESD on line {i + 1}")
                values.append((intensity, sigma))
            i += 1
        if len(values) < count:
            raise ValueError(f"Truncated ESD bank {number}: declared {count}, found {len(values)} pairs")
        values = np.asarray(values[:count], dtype=float)
        banks.append(GSASBank(number, start + step * np.arange(count), values[:, 0], values[:, 1],
            {"title": title, "instrument_reference": instrument, "comments": list(comments),
             "source": str(path), "step_deg": step, "record_count": records,
             "valid_mask": (values[:, 1] > 0).tolist(),
             "valid_mask_source": "serialized_sigma_positive"}))
    if not banks:
        raise ValueError("No GSAS banks found")
    return banks
