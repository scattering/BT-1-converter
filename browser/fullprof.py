"""Ancillary FullProf XYDATA export from the converter's existing CSV.

This module does no reduction, interpolation, normalization, or variance
calculation. The uniform CSV/GSAS and their validity metadata remain the primary
outputs. FullProf Ins=10 with INTER 1 1 0 0 accepts explicit variable-step angles,
so invalid CSV rows can be omitted without inventing observations in gaps.

Format reference: FullProf Manual, sections Ins=10 (pp. 74 and 136),
https://fullprofsuite.github.io/FullProf-Web/downloads/Docs/FullProf_Manual.pdf
"""

import csv
import hashlib
import io
import math
from pathlib import Path
import re


_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", re.ASCII)
_COLUMNS = ("two_theta_deg", "intensity", "sigma")


def _number(text, column, row_number):
    if text is None or not _NUMBER.fullmatch(text.strip()):
        raise ValueError(f"CSV row {row_number}: {column} must be a finite decimal number")
    token = text.strip()
    value = float(token)
    if not math.isfinite(value):
        raise ValueError(f"CSV row {row_number}: {column} must be finite")
    return token, value


def write_fullprof_from_csv(csv_path, dat_path):
    """Write a new six-header, three-column Ins=10 file and return its metadata.

    Valid rows retain their numeric CSV tokens, including nonpositive intensity
    with positive sigma. Invalid rows are omitted according to the explicit
    ``valid`` column; the surviving angles are never renumbered or interpolated.
    All rows are checked before creating the destination. Exclusive creation
    refuses existing files and symlinks, including aliases of the input CSV.

    FullProf 8.50 was observed to replace nonpositive intensities and their
    variances with 0 and 1 during import, even when supplied sigma is positive.
    Such rows are preserved here and reported explicitly, not silently changed
    to accommodate the external reader.
    """
    source = Path(csv_path)
    destination = Path(dat_path)
    source_bytes = source.read_bytes()
    reader = csv.DictReader(io.StringIO(source_bytes.decode("utf-8"), newline=""))
    fields = reader.fieldnames
    if (fields is None or len(set(fields)) != len(fields)
            or not set((*_COLUMNS, "valid")).issubset(fields)):
        raise ValueError("CSV requires unique two_theta_deg,intensity,sigma,valid columns")

    rows = []
    omitted = 0
    nonpositive = 0
    previous_angle = None
    for row_number, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"CSV row {row_number}: inconsistent number of columns")
        angle_token, angle = _number(row["two_theta_deg"], "two_theta_deg", row_number)
        if previous_angle is not None and angle <= previous_angle:
            raise ValueError(f"CSV row {row_number}: angles must be strictly increasing")
        previous_angle = angle
        valid = row["valid"].strip()
        if valid not in ("0", "1"):
            raise ValueError(f"CSV row {row_number}: valid must be 0 or 1")
        if valid == "0":
            omitted += 1
            continue
        intensity_token, intensity = _number(row["intensity"], "intensity", row_number)
        sigma_token, sigma = _number(row["sigma"], "sigma", row_number)
        if sigma <= 0:
            raise ValueError(f"CSV row {row_number}: valid observations require positive sigma")
        nonpositive += intensity <= 0
        rows.append(f"{angle_token} {intensity_token} {sigma_token}")
    if not rows:
        raise ValueError("CSV has no valid observations for FullProf export")

    warnings = []
    if omitted:
        warnings.append(
            f"Omitted {omitted} invalid CSV bins; retained original angles. "
            "Use FullProf Ins=10 and INTER 1 1 0 0; do not interpolate across gaps."
        )
    if nonpositive:
        warnings.append(
            f"Preserved {nonpositive} valid nonpositive-intensity observations with their "
            "supplied sigma. FullProf 8.50 was observed to reset nonpositive intensity "
            "to 0 and variance to 1 on import; check the imported weights."
        )
    headers = [
        "XYDATA BT1 converter; FullProf Ins=10",
        "! two_theta_deg intensity sigma; numeric values retained from CSV",
        "INTER 1 1 0 0",
        f"! Invalid CSV bins omitted: {omitted}; original angles retained",
        f"! Valid nonpositive intensities: {nonpositive}; check FullProf imported weights",
        "! Marginal sigma only; interpolation covariance is not represented",
    ]
    contents = ("\n".join(headers + rows) + "\n").encode("ascii")
    with destination.open("xb") as stream:
        stream.write(contents)
    return {
        "filename": destination.name,
        "format": "FullProf XYDATA",
        "columns": list(_COLUMNS),
        "instrument_code": 10,
        "interpolation": 0,
        "points": len(rows),
        "omitted_invalid_points": omitted,
        "nonpositive_intensity_points": nonpositive,
        "source_csv_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "warnings": warnings,
    }
