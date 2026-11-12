"""Strict BT-1 acquisition and legacy intermediate RAW input/output.

Angles and detector zero corrections are degrees in memory.  RAW bank angles
and its zero field are centidegrees on disk.  Counts remain unscaled integers;
negative counts are masks, never observations.  No reduction occurs here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import math
import re
import shlex

import numpy as np


class FormatError(ValueError):
    """Malformed, incomplete, unsupported, or unrepresentable input."""


@dataclass
class Bank:
    detector: int
    angles: np.ndarray
    counts: np.ndarray
    scale: float
    zero: float


@dataclass
class RawDataset:
    header: dict
    banks: list[Bank]
    source: str


_ANGLE_TOL = 1e-6
_FLOAT = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?"
_POINT = re.compile(r"^\s*\$\s*M4\s*=\s*(" + _FLOAT + r")(?=\s|$)")
_TEMP = re.compile(r"(?:^|\s)T\s*=\s*(" + _FLOAT + r")(?:\s|$)")
_INT = re.compile(r"^[+-]?\d+$")
_FORTRAN_REAL = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))(?:(?:[EeDd]([+-]?\d+))|([+-]\d+))?$")
_FOOTER = re.compile(r"^\s*\$\s+\d{1,2}-[A-Za-z]{3}-\d{4}\s+\d{2}:\d{2}:\d{2}\s*$")


def _number(value: str, label: str) -> float:
    try:
        result = float(value.strip().replace("D", "E").replace("d", "e"))
    except (TypeError, ValueError) as exc:
        raise FormatError(f"Invalid {label}: {value!r}") from exc
    if not math.isfinite(result):
        raise FormatError(f"Nonfinite {label}: {value!r}")
    return result


def _integer(value: str, label: str) -> int:
    if not _INT.fullmatch(value.strip()):
        raise FormatError(f"Invalid integer {label}: {value!r}")
    result = int(value)
    if not -(2**63) <= result < 2**63:
        raise FormatError(f"Integer {label} exceeds int64: {value!r}")
    return result


def _fortran_real(field: str, decimals: int, label: str) -> float:
    """Decode a fixed-width F/G input field, including its implied decimal.

    Fortran's default blank-null input ignores embedded spaces. If the
    mantissa has no decimal point, the descriptor's ``d`` digits are implied
    even when an E/D exponent is present. For example, G14.1 reads ``123E2``
    as 1230, while ``123.E2`` is 12300. A sign-only exponent such as ``123+02``
    is also legal. Apply the decimal shift in the textual exponent before
    float conversion, avoiding an extra binary division/rounding operation.
    Empty fields remain rejected instead of being silently treated as zero.
    """
    token = field.replace(" ", "")
    match = _FORTRAN_REAL.fullmatch(token)
    if match is None:
        raise FormatError(f"Invalid Fortran {label} field: {field!r}")
    mantissa = match.group(1)
    exponent = int(match.group(2) or match.group(3) or "0")
    if "." not in mantissa:
        exponent -= decimals
    return _number(f"{mantissa}E{exponent}", label)


def _read_text(path) -> tuple[Path, list[str]]:
    p = Path(path)
    try:
        return p, p.read_text(encoding="ascii").splitlines()
    except UnicodeError as exc:
        raise FormatError(f"Expected ASCII input: {p}") from exc


def _calibration(lines, start, marker, n):
    values = []
    index = start
    while index < len(lines) and marker not in lines[index]:
        if not lines[index].strip():
            raise FormatError(f"Blank line inside {marker} block")
        values.extend(_number(v, marker) for v in lines[index].split())
        if len(values) > n:
            raise FormatError(f"Too many values in {marker}: expected {n}")
        index += 1
    if index == len(lines) or len(values) != n:
        raise FormatError(f"Missing or truncated {marker}: expected {n} values")
    words = lines[index].split()
    mono = words[-1] if len(words) > len(marker.split()) else ""
    return values, mono, index + 1


def parse_bt1(path, allow_partial: bool = False) -> RawDataset:
    """Read 32-detector ICP BT-1 files, using recorded M4 positions.

    Strict mode requires the complete planned grid. ``allow_partial`` accepts
    a contiguous acquired prefix, including a truncated final detector record;
    malformed counts, missing interior angles, and extra records still fail.
    A final timestamp is recorded as metadata, not required for completeness:
    a live file may contain all detector records before its footer is appended.
    """
    p, lines = _read_text(path)
    if len(lines) < 17:
        raise FormatError("BT-1 header is truncated")
    try:
        first = shlex.split(lines[0])
    except ValueError as exc:
        raise FormatError("Malformed quoted BT-1 first header line") from exc
    if len(first) != 13:
        raise FormatError(f"Expected 13 fields in BT-1 first header, found {len(first)}")
    kind = _integer(first[2], "file type")
    experimental = tuple(_integer(v, "experimental parameter") for v in first[3:8])
    monbase = _number(first[8], "monitor base")
    monprefactor = _integer(first[9], "monitor prefactor")
    monitor = monbase * monprefactor
    planned = _integer(first[11], "planned points")
    if planned < 1 or monitor <= 0 or not math.isfinite(monitor):
        raise FormatError("Planned points and monitor preset must be positive")
    if first[12].upper() != "RAW":
        raise FormatError(f"Unsupported BT-1 data type {first[12]!r}; expected RAW")
    exp = lines[3].split()
    if len(exp) != 12:
        raise FormatError("Expected 12 collimation/mosaic/wavelength fields")
    collimation4 = tuple(_integer(v, "collimation") for v in exp[:4])
    mosaic = tuple(_integer(v, "mosaic") for v in exp[4:7])
    wavelength = _number(exp[7], "wavelength")
    n = _integer(exp[11], "number of detectors")
    if n != 32:
        raise FormatError(f"Only 32-detector BT-1 files are supported; found {n} (legacy five-detector format is unsupported)")
    if wavelength <= 0:
        raise FormatError("Wavelength must be positive")
    motors = {}
    for line in lines[5:7]:
        parts = line.split()
        if len(parts) != 4:
            raise FormatError("Malformed motor range header")
        motor = _integer(parts[0], "motor number")
        if motor not in (3, 4) or motor in motors:
            raise FormatError("Expected one motor-3 and one motor-4 range")
        motors[motor] = tuple(_number(v, "motor range") for v in parts[1:])
    if set(motors) != {3, 4}:
        raise FormatError("Motor-4 range is missing")
    start, step, end = motors[4]
    if step <= 0 or end < start:
        raise FormatError("BT-1 motor-4 range must have a positive increasing step")
    if not math.isclose(start + (planned - 1) * step, end, rel_tol=0, abs_tol=_ANGLE_TOL):
        raise FormatError("Planned point count disagrees with motor-4 start/step/end")
    scales, mono, pos = _calibration(lines, 8, "Detector Relative Scalefactors", n)
    zeros, mono2, pos = _calibration(lines, pos, "Detector Relative Zero Angles", n)
    if mono and mono2 and mono != mono2:
        raise FormatError("Monochromator labels disagree between calibration blocks")
    mono = mono or mono2
    if not mono:
        raise FormatError("Monochromator label is missing from calibration blocks")
    if any(scale <= 0 for scale in scales):
        raise FormatError("Detector scale factors must be positive")
    if pos >= len(lines) or "Data in" not in lines[pos]:
        raise FormatError("BT-1 detector-data descriptor is missing")
    pos += 1
    angles, rows, temperatures = [], [], []
    pending_angle = pending_temp = None
    pending_counts = []
    footer = None
    trailing_incomplete = None

    def finish(final=False):
        nonlocal pending_angle, pending_temp, pending_counts, trailing_incomplete
        if pending_angle is None:
            return
        if len(pending_counts) != n:
            if final and allow_partial and len(pending_counts) < n:
                trailing_incomplete = {"angle": pending_angle, "detector_counts_present": len(pending_counts)}
                pending_angle = pending_temp = None
                pending_counts = []
                return
            raise FormatError(f"Truncated detector record at M4={pending_angle}: {len(pending_counts)}/{n} counts")
        angles.append(pending_angle)
        rows.append(pending_counts)
        temperatures.append(pending_temp)
        pending_angle = pending_temp = None
        pending_counts = []

    for lineno, line in enumerate(lines[pos:], pos + 1):
        if not line.strip():
            continue
        match = _POINT.match(line)
        if match:
            if footer is not None:
                raise FormatError(f"Detector data follows terminal timestamp at line {lineno}")
            finish()
            pending_angle = _number(match.group(1), "recorded M4")
            temp = _TEMP.search(line)
            if re.search(r"(?:^|\s)T\s*=", line) and temp is None:
                raise FormatError(f"Malformed temperature at line {lineno}")
            pending_temp = _number(temp.group(1), "temperature") if temp else None
            continue
        if line.lstrip().startswith("$"):
            if _FOOTER.match(line):
                finish()
                footer = line.strip().lstrip("$").strip()
            continue
        if pending_angle is None:
            raise FormatError(f"Counts without a preceding M4 record at line {lineno}")
        # Comma-separated counts may wrap at any detector; whitespace separators
        # are also valid list-directed Fortran input. Never truncate extra counts.
        tokens = line.replace(",", " ").split()
        pending_counts.extend(_integer(v, f"detector count at line {lineno}") for v in tokens)
        if len(pending_counts) > n:
            raise FormatError(f"Too many detector counts at M4={pending_angle}: {len(pending_counts)}")
    finish(final=True)
    if not rows:
        raise FormatError("No complete detector records are present")
    recorded = len(rows)
    if recorded > planned:
        raise FormatError(f"More recorded than planned points: {recorded}>{planned}")
    if trailing_incomplete is not None and recorded >= planned:
        raise FormatError("An extra incomplete detector record follows all planned points")
    a4 = np.asarray(angles, dtype=float)
    expected = start + np.arange(recorded) * step
    if not np.allclose(a4, expected, rtol=0, atol=_ANGLE_TOL):
        bad = int(np.flatnonzero(np.abs(a4 - expected) > _ANGLE_TOL)[0])
        raise FormatError(f"Missing or irregular angle at point {bad + 1}: recorded {a4[bad]}, expected {expected[bad]}")
    complete = recorded == planned and trailing_incomplete is None
    if not complete and not allow_partial:
        raise FormatError(f"Incomplete BT-1 scan: {recorded}/{planned} points; use allow_partial=True explicitly")
    available_t = [t for t in temperatures if t is not None]
    temp_summary = ({"count": len(available_t), "min": min(available_t), "max": max(available_t),
                     "mean": float(np.mean(available_t)), "unit": "K"} if available_t else None)
    header = {
        "title": lines[2].rstrip(), "wavelength": wavelength,
        "collimation": collimation4[:2], "collimation_all": collimation4,
        "mosaic": mosaic, "monochromator": mono, "monitor": monitor,
        "monitor_base": monbase, "monitor_prefactor": monprefactor,
        "count_type": first[10], "file_type": kind,
        "experimental_parameters": experimental, "date": first[1],
        "original_filename": first[0], "planned_points": planned,
        "recorded_points": recorded, "complete": complete,
        "temp_summary": temp_summary, "temperatures": temperatures,
        "temperature_start": _number(exp[8], "temperature start"),
        "temperature_increment": _number(exp[9], "temperature increment"),
        "magnetic_field": _number(exp[10], "magnetic field"),
        "a3_range": motors[3], "a4_start": start, "a4_step": step, "a4_end": end,
        "detector_separation": 5.0, "number_of_detectors": n,
        "terminal_timestamp": footer, "trailing_incomplete_record": trailing_incomplete,
        "format": "bt1", "raw_header_lines": lines[:pos],
    }
    counts = np.asarray(rows, dtype=np.int64)
    banks = [Bank(j + 1, a4.copy() + 5.0 * j, counts[:, j].copy(), scales[j], zeros[j] / 100.0)
             for j in range(n)]
    return RawDataset(header, banks, str(p))


def _bank_arrays(bank):
    if not isinstance(bank.detector, (int, np.integer)) or not 1 <= bank.detector <= 32:
        raise FormatError(f"Invalid detector ID: {bank.detector!r}")
    angles = np.asarray(bank.angles, dtype=float)
    counts = np.asarray(bank.counts)
    if angles.ndim != 1 or counts.ndim != 1 or len(angles) != len(counts) or not len(angles):
        raise FormatError(f"Detector {bank.detector}: angles/counts must be nonempty matching 1-D arrays")
    if not np.all(np.isfinite(angles)) or (len(angles) > 1 and not np.all(np.diff(angles) > 0)):
        raise FormatError(f"Detector {bank.detector}: angles must be finite and strictly increasing")
    if not np.issubdtype(counts.dtype, np.integer):
        # Numeric integer-valued arrays are accepted, but no rounding is done.
        try:
            vals = counts.astype(float)
        except (ValueError, TypeError) as exc:
            raise FormatError("Counts must be integers") from exc
        if not np.all(np.isfinite(vals)) or not np.all(vals == np.floor(vals)) or np.any(vals < -(2**63)) or np.any(vals >= 2**63):
            raise FormatError("Counts must be finite int64-valued integers")
    elif np.issubdtype(counts.dtype, np.unsignedinteger) and np.any(counts >= 2**63):
        raise FormatError("Counts exceed int64")
    counts = counts.astype(np.int64)
    if not math.isfinite(float(bank.scale)) or bank.scale <= 0 or not math.isfinite(float(bank.zero)):
        raise FormatError(f"Detector {bank.detector}: invalid scale or zero")
    return angles, counts


def prepare_bt1(dataset: RawDataset, cutoff: float, drop_detectors=None,
                keep_detector1: bool = False) -> RawDataset:
    """Copy a dataset and apply explicit leading-angle cuts and detector masks.

    ``cutoff`` is in the original M4 coordinate for detectors 1–3. Negative
    counts are preserved, and dropped detectors remain as negative-count banks
    so interpolation cannot bridge masks and 32-bank RAW stays interoperable.
    Unlike PROPREP's nearest-index rounding, a cut keeps only M4 >= cutoff.
    """
    cutoff = float(cutoff)
    if not math.isfinite(cutoff) or cutoff < 0:
        raise FormatError("Cutoff must be a finite nonnegative angle")
    drop = set(drop_detectors or [])
    if any(not isinstance(d, (int, np.integer)) or not 1 <= d <= 32 for d in drop):
        raise FormatError("Dropped detector IDs must be integers from 1 to 32")
    ids = [b.detector for b in dataset.banks]
    if len(ids) != len(set(ids)):
        raise FormatError("Duplicate detector IDs")
    if not drop.issubset(ids):
        raise FormatError(f"Dropped detector IDs are absent: {sorted(drop.difference(ids))}")
    separation = float(dataset.header.get("detector_separation", 5.0))
    if not math.isfinite(separation) or separation <= 0:
        raise FormatError("Invalid detector separation")
    banks = []
    removed = {}
    for bank in dataset.banks:
        angles, counts = _bank_arrays(bank)
        keep = np.ones(len(angles), dtype=bool)
        if bank.detector <= 3 and not (bank.detector == 1 and keep_detector1):
            m4 = angles - (bank.detector - 1) * separation
            keep = m4 >= cutoff - _ANGLE_TOL
        removed[str(bank.detector)] = int(np.count_nonzero(~keep))
        if not np.any(keep):
            raise FormatError(f"Cutoff {cutoff} removes every point from detector {bank.detector}; empty RAW banks are unsupported")
        angles, counts = angles[keep].copy(), counts[keep].copy()
        if bank.detector in drop:
            counts = np.where(counts >= 0, -np.maximum(1, counts), counts)
        banks.append(Bank(bank.detector, angles, counts, bank.scale, bank.zero))
    header = deepcopy(dataset.header)
    header["preparation"] = {"cutoff_m4_degrees": cutoff, "drop_detectors": sorted(drop),
                             "keep_detector1": bool(keep_detector1), "removed_leading_points": removed,
                             "negative_counts": "preserved as masks"}
    return RawDataset(header, banks, dataset.source)


def _legacy_real32(value, label):
    """Round one legacy REAL assignment, rejecting overflow rather than stars."""
    with np.errstate(over="ignore", invalid="ignore"):
        result = np.float32(value)
    if not np.isfinite(result):
        raise FormatError(f"{label} is not representable as finite legacy REAL*4")
    return result


def _legacy_nint(value, label):
    """Fortran NINT: nearest integer, exact halves away from zero."""
    value = float(_legacy_real32(value, label))
    rounded = math.floor(abs(value) + .5)
    result = -rounded if value < 0 else rounded
    if not -(2**31) <= result < 2**31:
        raise FormatError(f"{label} exceeds legacy INTEGER*4")
    return result


def _legacy_decimal(value, width, decimals, label):
    """Emulate the numeric result of writing a REAL*4 Fw.d RAW field."""
    value = float(_legacy_real32(value, label))
    rendered = f"{value:{width}.{decimals}f}"
    if len(rendered) != width:
        raise FormatError(f"{label}={value} overflows legacy RAW F{width}.{decimals}")
    return float(rendered)


def prepare_legacy_bt1(dataset: RawDataset, cutoff: float = 1,
                       drop_detectors=(), keep_detector1: bool = False) -> RawDataset:
    """Reproduce PROPREP's single-file numerical preparation in Python.

    This deliberately differs from :func:`prepare_bt1`: the first retained
    index in detectors 1–3 is ``max(0, NINT((cutoff-start)/step))``, with REAL*4
    rounding after each arithmetic operation and NINT ties away from zero.
    Thus the first retained angle can be below the requested cutoff. Counts
    from a dropped detector follow the original ``-max(1,count)`` rule, which
    also changes pre-existing negative masks to -1. Detector 1 can be exempted
    from the leading cut. All 32 banks remain present.

    PROPREP derives angles from the acquisition header, then rounds start,
    step and endpoint to integer centidegrees. Calibration values, wavelength
    and monitor are rounded through their historical RAW text fields. Returned
    values describe that RAW representation, with original acquisition fields
    and the exact preparation choices retained in metadata. No file or native
    process is used. ``write_raw`` can serialize the returned object.

    Unlike unchecked Fortran formatting, this function rejects empty banks,
    RAW field overflow and rounded start/step/end inconsistencies. It does not
    silently reproduce malformed output or reinterpret irregular acquisition
    angles. Multi-file summation is the reducer's responsibility.
    """
    cutoff = float(cutoff)
    if not math.isfinite(cutoff) or cutoff < 0:
        raise FormatError("Cutoff must be a finite nonnegative angle")
    cut32 = _legacy_real32(cutoff, "cutoff")
    drop = set(drop_detectors or ())
    if any(not isinstance(d, (int, np.integer)) or not 1 <= d <= 32 for d in drop):
        raise FormatError("Dropped detector IDs must be integers from 1 to 32")
    by_id = {bank.detector: bank for bank in dataset.banks}
    if len(dataset.banks) != 32 or set(by_id) != set(range(1, 33)):
        raise FormatError("Legacy BT-1 preparation requires exactly detectors 1–32")
    try:
        original_start = float(dataset.header["a4_start"])
        original_step = float(dataset.header["a4_step"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FormatError("Legacy BT-1 preparation requires acquisition a4_start/a4_step") from exc
    separation = float(dataset.header.get("detector_separation", 5.0))
    start = _legacy_real32(original_start, "acquisition start")
    step = _legacy_real32(original_step, "acquisition step")
    delta = _legacy_real32(separation, "detector separation")
    if step <= 0 or delta <= 0:
        raise FormatError("Legacy acquisition step and detector separation must be positive")
    dang = _legacy_nint(np.float32(step * np.float32(100)), "RAW step")
    if dang <= 0:
        raise FormatError("Legacy rounded RAW step must be positive")
    first = max(0, _legacy_nint(np.float32(np.float32(cut32 - start) / step), "cutoff index"))
    banks = []
    removed = {}
    angle_rounding = {}
    npts = None
    for detector in range(1, 33):
        bank = by_id[detector]
        angles, counts = _bank_arrays(bank)
        if npts is None:
            npts = len(counts)
        if len(counts) != npts:
            raise FormatError("Legacy BT-1 acquisition banks must have equal point counts")
        expected = original_start + np.arange(npts) * original_step + (detector - 1) * separation
        if not np.allclose(angles, expected, rtol=0, atol=_ANGLE_TOL):
            raise FormatError(f"Detector {detector}: angles disagree with the legacy acquisition header grid")
        cut = first if detector <= 3 and not (detector == 1 and keep_detector1) else 0
        if cut >= npts:
            raise FormatError(f"Legacy cutoff {cutoff} removes every point from detector {detector}")
        removed[str(detector)] = cut
        # Keep the source expression's REAL*4 evaluation order, including
        # its independently rounded endpoint rather than (n-1)*rounded_step.
        origin = np.float32(np.float32(start + np.float32(np.float32(cut) * step))
                            + np.float32(np.float32(detector - 1) * delta))
        ang1 = _legacy_nint(np.float32(np.float32(100) * origin), "RAW start")
        span = np.float32(np.float32(np.float32(npts - cut - 1) * step) * np.float32(100))
        ang2 = ang1 + _legacy_nint(span, "RAW span")
        if ang2 != ang1 + (npts - cut - 1) * dang:
            raise FormatError(f"Detector {detector}: historical rounded RAW endpoint disagrees with rounded step/count")
        prepared_angles = (ang1 + dang * np.arange(npts - cut, dtype=float)) / 100
        prepared_counts = counts[cut:].copy()
        if detector in drop:
            prepared_counts = -np.maximum(1, prepared_counts)
        for value, label in ((ang1, "RAW start"), (dang, "RAW step"), (ang2, "RAW end")):
            _fixed_integer(value, 8, label)
        _fixed_integer(len(prepared_counts), 5, "RAW point count")
        for value in prepared_counts:
            _fixed_integer(int(value), 6, "RAW detector count")
        scale = _legacy_decimal(bank.scale, 8, 4, "detector scale")
        zero_centidegrees = _legacy_decimal(float(bank.zero) * 100, 8, 4, "detector zero")
        if scale <= 0:
            raise FormatError("Legacy RAW rounding produced a nonpositive detector scale")
        angle_rounding[str(detector)] = float(np.max(np.abs(prepared_angles - angles[cut:])))
        banks.append(Bank(detector, prepared_angles, prepared_counts, scale, zero_centidegrees / 100))
    header = deepcopy(dataset.header)
    source_header_fields = {key: deepcopy(header.get(key)) for key in
                            ("title", "wavelength", "monitor", "monochromator", "date", "a4_start", "a4_step")}
    header["title"] = str(header["title"])[:50]
    header["monochromator"] = str(header["monochromator"])[:5]
    header["date"] = str(header.get("date", ""))[:11]
    _ascii_field(header["title"], 50, "title")
    _ascii_field(header["monochromator"], 5, "monochromator")
    header["wavelength"] = _legacy_decimal(header["wavelength"], 8, 4, "wavelength")
    if header["wavelength"] <= 0:
        raise FormatError("Legacy RAW rounding produced a nonpositive wavelength")
    # Original PROPREP reads the monitor base as REAL and the prefactor as
    # INTEGER, then performs REAL multiplication before writing G14.7.
    base = header.get("monitor_base", header["monitor"])
    prefactor = header.get("monitor_prefactor", 1)
    mon32 = _legacy_real32(np.float32(_legacy_real32(base, "monitor base")
                                    * _legacy_real32(prefactor, "monitor prefactor")), "monitor preset")
    monitor = float(f"{float(mon32):.7G}")
    if monitor <= 0 or not math.isfinite(monitor):
        raise FormatError("Legacy RAW monitor must be positive")
    header["monitor"] = monitor
    for value in header["collimation"]:
        _fixed_integer(value, 3, "collimation")
    for value in header["experimental_parameters"]:
        _fixed_integer(value, 6, "experimental parameter")
    header["bank_steps"] = {str(detector): dang / 100 for detector in range(1, 33)}
    header["preparation"] = {
        "mode": "legacy-proprep-python", "cutoff_m4_degrees": cutoff,
        "cutoff_real32_degrees": float(cut32), "drop_detectors": sorted(drop),
        "keep_detector1": bool(keep_detector1), "removed_leading_points": removed,
        "cutoff_rule": "max(0,NINT(REAL32((REAL32(cutoff)-REAL32(start))/REAL32(step))))",
        "negative_counts": "preserved unless detector dropped; dropped counts use -max(1,count)",
        "source_header_fields": source_header_fields,
        "raw_angle_max_absolute_rounding_deg": angle_rounding,
        "raw_step_centidegrees": dang,
        "rounding": "REAL*4 arithmetic; NINT ties away from zero; RAW Fw.d and G14.7 text precision",
    }
    return RawDataset(header, banks, dataset.source)


def read_raw(path) -> RawDataset:
    """Read the official fixed-width PROPREP intermediate format.

    All available banks (IDs 1–32) are read and validated. No metadata sidecar
    is consulted. Acquisition completeness and temperatures cannot be inferred
    from this format; these fields are explicitly unknown (None).

    Numeric fields follow GFORMAT's actual input descriptors: wavelength and
    bank angles F8.0, monitor G14.1, and detector scale/zero F8.4. A missing
    decimal point therefore changes the value, including with an exponent.
    Original fixed-width lexemes and descriptors are retained as provenance;
    the decoded values are authoritative for both reduction modes.
    """
    p, lines = _read_text(path)
    if len(lines) < 3 or len(lines[0]) < 70 or len(lines[1]) < 30:
        raise FormatError("RAW header is truncated")
    h = lines[0]
    wavelength = _fortran_real(h[50:58], 0, "RAW wavelength")
    collimation = (_integer(h[58:61], "collimation 1"), _integer(h[61:64], "collimation 2"))
    if h[64:65] != " ":
        raise FormatError("RAW header separator at column 65 is malformed")
    mono = h[65:70].strip()
    monitor = _fortran_real(h[70:84], 1, "RAW monitor")
    if h[84:].strip():
        raise FormatError("Extra text after RAW monitor field")
    if wavelength <= 0 or monitor <= 0 or not mono:
        raise FormatError("RAW wavelength/monitor must be positive and monochromator must be present")
    experimental = tuple(_integer(lines[1][i:i+6], "experimental parameter") for i in range(0, 30, 6))
    if lines[1][30:].strip():
        raise FormatError("Extra text after RAW experimental parameters")
    banks, dates, steps = [], {}, {}
    raw_numeric_fields = {"wavelength": {"descriptor": "F8.0", "text": h[50:58]},
                          "monitor": {"descriptor": "G14.1", "text": h[70:84]}, "banks": {}}
    pos = 2
    seen = set()
    while pos < len(lines):
        if not lines[pos].strip():
            pos += 1
            continue
        row = lines[pos]
        if len(row) < 50:
            raise FormatError(f"Truncated RAW bank header at line {pos + 1}")
        start, step, end = (_fortran_real(row[i:i+8], 0, "RAW angle") for i in (0, 8, 16))
        scale = _fortran_real(row[24:32], 4, "RAW detector scale")
        zero = _fortran_real(row[32:40], 4, "RAW detector zero") / 100.0
        detector = _integer(row[40:45], "RAW detector ID")
        n = _integer(row[45:50], "RAW point count")
        if not 1 <= detector <= 32 or detector in seen or n < 1:
            raise FormatError(f"Invalid or duplicate RAW detector ID / point count at line {pos + 1}")
        if step <= 0 or scale <= 0 or not math.isclose(start + (n - 1) * step, end, rel_tol=0, abs_tol=1e-6):
            raise FormatError(f"RAW detector {detector}: inconsistent start/step/end/count or nonpositive scale")
        if len(row) > 50 and row[50:55].strip():
            raise FormatError("Malformed RAW bank date separator")
        dates[str(detector)] = row[55:].strip() if len(row) > 55 else ""
        steps[str(detector)] = step / 100.0
        raw_numeric_fields["banks"][str(detector)] = {
            name: {"descriptor": descriptor, "text": row[offset:offset+8]}
            for name, offset, descriptor in (("start", 0, "F8.0"), ("step", 8, "F8.0"),
                                            ("end", 16, "F8.0"), ("scale", 24, "F8.4"),
                                            ("zero_centidegrees", 32, "F8.4"))}
        seen.add(detector)
        pos += 1
        counts = []
        while len(counts) < n:
            if pos >= len(lines):
                raise FormatError(f"Truncated RAW detector {detector}: {len(counts)}/{n} counts")
            data = lines[pos]
            expected = min(12, n - len(counts))
            if len(data) < expected * 6 or data[expected * 6:].strip():
                raise FormatError(f"Invalid fixed-width RAW count row at line {pos + 1}")
            for column in range(0, expected * 6, 6):
                value = _fortran_real(data[column:column+6], 0, "RAW count")
                if value != math.floor(value) or not -(2**63) <= value < 2**63:
                    raise FormatError("RAW counts must decode to int64-valued integers")
                counts.append(int(value))
            pos += 1
        angles = (start + step * np.arange(n, dtype=float)) / 100.0
        banks.append(Bank(detector, angles, np.asarray(counts, dtype=np.int64), scale, zero))
    if not banks:
        raise FormatError("No RAW detector banks")
    unique_dates = set(dates.values())
    unique_steps = set(steps.values())
    header = {"title": h[:50].rstrip(), "wavelength": wavelength, "collimation": collimation,
              "monochromator": mono, "monitor": monitor, "experimental_parameters": experimental,
              "date": next(iter(unique_dates)) if len(unique_dates) == 1 else "",
              "bank_dates": dates, "bank_steps": steps, "number_of_detectors": len(banks),
              "detector_separation": 5.0, "planned_points": None, "recorded_points": None,
              "count_type": None,
              "bank_points": {str(b.detector): len(b.counts) for b in banks},
              "complete": None, "temp_summary": None, "format": "raw",
              "raw_numeric_fields": raw_numeric_fields,
              "metadata_limitations": "RAW does not encode acquisition completeness, temperatures or full collimation"}
    if len(unique_steps) == 1:
        header["a4_step"] = next(iter(unique_steps))
    return RawDataset(header, banks, str(p))


def _fixed_integer(value, width, label):
    value = _integer(str(value), label)
    s = f"{value:{width}d}"
    if len(s) != width:
        raise FormatError(f"{label}={value} overflows RAW width {width}")
    return s


def _fixed_float(value, width, decimals, label):
    value = float(value)
    if not math.isfinite(value):
        raise FormatError(f"Nonfinite {label}")
    s = f"{value:{width}.{decimals}f}"
    if len(s) != width:
        raise FormatError(f"{label}={value} overflows RAW width {width}")
    if not math.isclose(float(s), value, rel_tol=0, abs_tol=1e-10 * max(1, abs(value))):
        raise FormatError(f"{label}={value} cannot be represented at RAW precision {decimals}; refusing to round")
    return s


def _centidegree(value, label):
    scaled = float(value) * 100.0
    if not math.isfinite(scaled) or not math.isclose(scaled, round(scaled), rel_tol=0, abs_tol=1e-6):
        raise FormatError(f"{label}={value} is not an integer centidegree; refusing to round")
    return int(round(scaled))


def _ascii_field(value, width, label):
    value = str(value)
    try:
        value.encode("ascii")
    except UnicodeError as exc:
        raise FormatError(f"{label} must be ASCII for legacy RAW") from exc
    if "\n" in value or "\r" in value or len(value) > width:
        raise FormatError(f"{label} exceeds RAW width {width} or contains a newline")
    return value.ljust(width)


def write_raw(dataset: RawDataset, path, *, overwrite: bool = False) -> Path:
    """Write validated official fixed-width RAW; refuse truncation/rounding.

    No sidecar is created. Retain ``dataset.header`` separately when acquisition
    temperatures, planned counts, full collimation, or preparation provenance
    must accompany this inherently limited legacy format.
    """
    h = dataset.header
    try:
        title = _ascii_field(h["title"], 50, "title")
        wavelength = _fixed_float(h["wavelength"], 8, 4, "wavelength")
        cols = tuple(h["collimation"])
        ep = tuple(h["experimental_parameters"])
        mono = _ascii_field(h["monochromator"], 5, "monochromator")
        monitor = float(h["monitor"])
    except KeyError as exc:
        raise FormatError(f"Missing RAW header field: {exc.args[0]}") from exc
    if len(cols) != 2 or len(ep) != 5 or float(h["wavelength"]) <= 0 or monitor <= 0 or not math.isfinite(monitor):
        raise FormatError("Invalid RAW header dimensions, wavelength or monitor")
    # PROPREP writes G14.7 but GFORMAT reads G14.1. Python's G format can
    # emit '137' or '1E+07', which Fortran would read as 13.7 or 1E+06.
    # Always supply an explicit decimal in the mantissa, including exponents.
    # Preserve the exact monitor value or reject significant-digit loss.
    mon = f"{monitor:.7G}"
    mantissa, separator, exponent = mon.partition("E")
    if "." not in mantissa:
        mantissa += "."
    mon = mantissa + (separator + exponent if separator else "")
    if len(mon) > 14 or _fortran_real(mon, 1, "RAW monitor") != monitor:
        raise FormatError("Monitor cannot be represented exactly in G14.7 RAW field")
    mon = mon.rjust(14)
    lines = [title + wavelength + "".join(_fixed_integer(c, 3, "collimation") for c in cols) + " " + mono + mon,
             "".join(_fixed_integer(v, 6, "experimental parameter") for v in ep)]
    if not dataset.banks:
        raise FormatError("Cannot write RAW without detector banks")
    seen = set()
    for bank in dataset.banks:
        angles, counts = _bank_arrays(bank)
        if bank.detector in seen:
            raise FormatError(f"Duplicate detector {bank.detector}")
        seen.add(bank.detector)
        if len(angles) > 1:
            step = float(angles[1] - angles[0])
            if not np.allclose(np.diff(angles), step, rtol=0, atol=_ANGLE_TOL):
                raise FormatError(f"Detector {bank.detector}: irregular angles cannot be represented by RAW")
        else:
            steps = h.get("bank_steps", {})
            step = steps.get(str(bank.detector), steps.get(bank.detector, h.get("a4_step")))
            if step is None:
                raise FormatError(f"Detector {bank.detector}: single point needs explicit a4_step/bank_steps")
        start_i = _centidegree(angles[0], "bank start")
        step_i = _centidegree(step, "bank step")
        if step_i <= 0:
            raise FormatError("RAW bank step must be positive")
        end_i = _centidegree(angles[-1], "bank end")
        expected = (start_i + step_i * np.arange(len(angles))) / 100.0
        if end_i != start_i + step_i * (len(angles)-1) or not np.allclose(angles, expected, rtol=0, atol=_ANGLE_TOL):
            raise FormatError(f"Detector {bank.detector}: angles do not match a centidegree grid")
        date = h.get("bank_dates", {}).get(str(bank.detector), h.get("date", ""))
        date = str(date)
        if "\n" in date or "\r" in date or not date.isascii():
            raise FormatError("Invalid RAW bank date text")
        lines.append("".join(_fixed_integer(v, 8, "bank angle") for v in (start_i, step_i, end_i))
                     + _fixed_float(bank.scale, 8, 4, "detector scale")
                     + _fixed_float(bank.zero * 100.0, 8, 4, "detector zero in centidegrees")
                     + _fixed_integer(bank.detector, 5, "detector ID")
                     + _fixed_integer(len(counts), 5, "point count") + "     " + date)
        for i in range(0, len(counts), 12):
            lines.append("".join(_fixed_integer(int(c), 6, "detector count") for c in counts[i:i+12]))
    output = "\n".join(lines) + "\n"
    p = Path(path)
    # Validation is complete before the destination is opened or truncated.
    with p.open("w" if overwrite else "x", encoding="ascii", newline="\n") as handle:
        handle.write(output)
    return p
