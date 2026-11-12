"""Pure-Python reproduction of the audited GFORMAT numerical path.

Only NumPy and the standard library are used; no compiler, executable, native
wrapper, or subprocess is imported. The reference is official gformat.f,
SHA256 4b449fe4b9e07959863f7c26e665bc1ee38c97c43f25cfc4d61e7c5bc006f7b3.

This is an explicitly historical policy, independently of the error formula.
It preserves single-precision REAL rounding at each arithmetic operation,
Fortran INT/NINT, the 0.00011-degree downward snap, IPT-1 interval iteration,
both-endpoint masking even at r=0, the post-normalization zero-count sigma=1,
and the two-stage inverse-variance combination. Optional background matching
and count rescaling follow the original sequential loops. Independent sigma
uses only the audited two-line variance correction; it does not change grids.

The returned I and sigma are internal float32 values promoted losslessly to
float64, BEFORE historical F8.0 serialization. Angles are the uniform grid
declared by the historical centidegree header. The arithmetic TSTART/GSTEP are
also retained in metadata. Invalid bins have I=sigma=0 and valid=False.

Deliberate safety bounds reject historical buffer overflows, negative master
indices, mismatched summed grids, nonfinite arithmetic, and INT-truncated
headers that would mislabel the grid. Undefined output padding, uninitialized
negative-count sigmas, and diagnostic text are not emulated. Absorption and
covariance are outside this API. Neither historical linear-sigma propagation
nor reproduction of an old algorithm establishes statistical correctness.
"""
from __future__ import annotations

import math
import numpy as np

from .reduce import ReducedPattern, _check_compatibility


# Explicit operands AND result casts avoid NumPy 1.x/2.x scalar promotion
# differences. In particular np.float32 ** 2 is not portable between versions.
F32 = np.float32


def _add(a, b):
    return F32(F32(a) + F32(b))


def _sub(a, b):
    return F32(F32(a) - F32(b))


def _mul(a, b):
    return F32(F32(a) * F32(b))


def _div(a, b):
    return F32(F32(a) / F32(b))


def _sqrt(a):
    return F32(np.sqrt(F32(a)))


def _int(a):
    """Fortran INT: truncate an already rounded REAL toward zero."""
    return math.trunc(float(F32(a)))


def _nint(a):
    """Fortran NINT: nearest integer, half away from zero.

    The argument first becomes REAL. The subsequent half-unit comparison is
    evaluated in Python float, so an already integral REAL above 2**23 is not
    changed by rounding an intermediate float32(x+0.5).
    """
    x = float(F32(a))
    return math.floor(x + .5) if x >= 0 else math.ceil(x - .5)


def _snapped(x, step, interpolate):
    if not interpolate:
        return _mul(_nint(_div(x, step)), step)
    lower = _int(_div(x, step))
    remainder = _sub(x, _mul(lower, step))
    extra = 0 if abs(remainder) < F32(.00011) else 1
    return _mul(lower + extra, step)


def _fixed_calibration(value, label):
    """The historical RAW calibration fields are F8.4, not arbitrary reals."""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'Legacy {label} must be finite')
    rendered = f'{value:.4f}'
    if len(rendered) > 8 or not math.isclose(float(rendered), value, rel_tol=0, abs_tol=1e-10):
        raise ValueError(f'Legacy {label} is not representable in its RAW F8.4 field')
    return F32(value)


def _validate(datasets):
    if not 1 <= len(datasets) <= 50:
        raise ValueError('Legacy mode requires 1–50 scans per output group')
    _check_compatibility(datasets)
    common_step = None
    normalized = []
    monitors = []
    for scan, dataset in enumerate(datasets):
        if [b.detector for b in dataset.banks] != list(range(1, 33)):
            raise ValueError('Legacy mode requires exactly 32 ordered banks with detector IDs 1..32')
        monitor = float(dataset.header.get('monitor', 0))
        preset = F32(monitor)
        if not np.isfinite(preset) or preset <= 0:
            raise ValueError('Legacy monitor preset must be positive and representable as REAL')
        monitors.append(preset)
        banks = []
        corrected_starts = []
        for j, bank in enumerate(dataset.banks):
            x, counts = np.asarray(bank.angles, dtype=float), np.asarray(bank.counts, dtype=float)
            if x.ndim != 1 or counts.shape != x.shape or not 2 <= len(x) <= 2000:
                raise ValueError('Legacy banks require matching 1-D angles/counts and 2–2000 samples')
            if not np.all(np.isfinite(x)) or np.any(np.diff(x) <= 0):
                raise ValueError('Legacy angles must be finite and strictly increasing')
            centidegrees = np.rint(x * 100)
            if not np.allclose(x * 100, centidegrees, rtol=0, atol=1e-6):
                raise ValueError('Legacy RAW angles must lie on integer centidegrees')
            step_cdeg = int(centidegrees[1] - centidegrees[0])
            if step_cdeg <= 0 or np.any(np.diff(centidegrees) != step_cdeg):
                raise ValueError('Legacy mode requires a uniform grid in each bank')
            if common_step is None:
                common_step = step_cdeg
            elif common_step != step_cdeg:
                raise ValueError('Legacy mode requires the same uniform step in all banks and scans')
            if np.any(centidegrees < -9999999) or np.any(centidegrees > 99999999):
                raise ValueError('Legacy RAW angle fields exceed their eight-column integer range')
            if (not np.all(np.isfinite(counts)) or np.any(counts != np.floor(counts)) or
                    np.any(counts < -99999) or np.any(counts > 999999)):
                raise ValueError('Legacy counts must be finite integers representable in RAW six-column fields')
            scale = _fixed_calibration(bank.scale, 'detector scale')
            zero_cdeg = _fixed_calibration(float(bank.zero) * 100, 'zero correction in centidegrees')
            if scale <= 0:
                raise ValueError('Legacy detector scale must be positive')
            # Reproduce RAW read then /100 assignments (gformat.f 308–313).
            start = _div(F32(centidegrees[0]), F32(100))
            step = _div(F32(step_cdeg), F32(100))
            zero = _div(zero_cdeg, F32(100))
            corrected_start = _sub(start, zero)
            if corrected_start < 0:
                raise ValueError('Legacy wrapper supports nonnegative corrected angles only')
            corrected_starts.append(corrected_start)
            if scan:
                ref = normalized[0][j]
                if (len(counts) != len(ref['counts']) or start != ref['start'] or
                        step != ref['step'] or zero != ref['zero'] or scale != ref['scale']):
                    raise ValueError('Legacy combined scans require identical per-detector grids and calibrations')
            banks.append({'start': start, 'step': step, 'zero': zero, 'scale': scale,
                          'counts': counts.astype(np.float32)})
        if np.any(np.diff(np.asarray(corrected_starts, dtype=float)) <= float(banks[0]['step']) / 2):
            raise ValueError('Legacy corrected detector origins must increase by more than half a step')
        normalized.append(banks)
    return normalized, monitors, common_step / 100


def reduce_legacy(datasets, uncertainty='legacy-linear', method='interpolate',
                  background=False, rescale=False) -> ReducedPattern:
    """Run historical GFORMAT policies entirely in Python.

    ``uncertainty`` independently chooses ``legacy-linear`` or ``independent``.
    No background matching/count rescaling is performed unless explicitly
    requested: defaults correspond to the historical ``-or`` flag profile.
    Multiple datasets use the first monitor and the original summed-file loop.
    ``contributions`` counts accepted file/bank estimates (one interpolation or
    nearest observation each), not the number of contributing raw count rows.
    """
    if uncertainty not in ('legacy-linear', 'independent'):
        raise ValueError('Legacy uncertainty must be legacy-linear or independent')
    if method not in ('interpolate', 'nearest'):
        raise ValueError('Legacy method must be interpolate or nearest')
    if not isinstance(background, bool) or not isinstance(rescale, bool):
        raise ValueError('Legacy background and rescale switches must be boolean')
    datasets = list(datasets)
    # Invalid overlap divisions in the old program yield NaN and a false
    # significance comparison; handled explicitly below. Other nonfinite
    # arithmetic is checked and rejected rather than emitted as an observation.
    with np.errstate(over='ignore', under='ignore', divide='ignore', invalid='ignore'):
        inputs, monitors, nominal_step = _validate(datasets)
        return _reduce(inputs, monitors, nominal_step, datasets, uncertainty, method, background, rescale)


def _reduce(inputs, monitors, nominal_step, datasets, uncertainty, method, background, rescale):
    interpolate = method == 'interpolate'
    step = inputs[0][0]['step']
    ttstart = np.zeros(32, dtype=np.float32)
    npt = np.zeros(32, dtype=np.int64)
    wsint = np.zeros((32, 2000), dtype=np.float32)
    sumw = np.zeros((32, 2000), dtype=np.float32)
    accepted = np.zeros((32, 2000), dtype=np.int64)
    negative_ratios = 0
    file_conflicts = 0
    detector_conflicts = 0

    # File -> detector -> input interval order is important: associativity is
    # not valid in REAL. Each multiply/add below rounds before the next one.
    for scan, banks in enumerate(inputs):
        pnorm = _div(monitors[0], monitors[scan]) if len(inputs) > 1 else F32(1)
        if not np.isfinite(pnorm) or pnorm <= 0:
            raise ValueError('Legacy monitor normalization overflows or underflows REAL')
        for j, bank in enumerate(banks):
            counts = bank['counts']
            intensity = np.zeros(len(counts), dtype=np.float32)
            sigma = np.zeros(len(counts), dtype=np.float32)
            for i, raw in enumerate(counts):
                if raw > 0:
                    sigma[i] = _mul(_mul(bank['scale'], _sqrt(raw)), pnorm)
                intensity[i] = _mul(_mul(raw, bank['scale']), pnorm)
                # The source tests the scaled intensity, not the raw count.
                if intensity[i] == 0:
                    sigma[i] = F32(1)
            if not np.all(np.isfinite(intensity)) or not np.all(np.isfinite(sigma)):
                raise ValueError('Legacy count normalization overflowed REAL')
            if scan == 0:
                ttstart[j] = _snapped(_sub(bank['start'], bank['zero']), step, interpolate)
            iterations = len(counts) - int(interpolate)
            for i in range(iterations):
                theta = _add(_sub(bank['start'], bank['zero']), _mul(i, bank['step']))
                target = _snapped(theta, step, interpolate)
                if interpolate:
                    ratio = _div(_sub(target, theta), bank['step'])
                    negative_ratios += int(ratio < 0)
                    # Preserve source grouping: left + (right-left)*r, rather
                    # than the mathematically equivalent weighted sum.
                    value = _add(intensity[i], _mul(_sub(intensity[i+1], intensity[i]), ratio))
                    if uncertainty == 'legacy-linear':
                        error = _add(sigma[i], _mul(_sub(sigma[i+1], sigma[i]), ratio))
                    else:
                        left_noise = _mul(_sub(1, ratio), sigma[i])
                        right_noise = _mul(ratio, sigma[i+1])
                        error = _sqrt(_add(_mul(left_noise, left_noise), _mul(right_noise, right_noise)))
                    # Original ignores BOTH endpoints even when one coefficient
                    # is zero. Negative-count sigma is unspecified in Fortran;
                    # it cannot matter once the complete interval is masked.
                    if intensity[i] < 0 or intensity[i+1] < 0:
                        error = F32(0)
                else:
                    value, error = intensity[i], sigma[i]
                    if value < 0:
                        error = F32(0)
                column = _nint(_div(_sub(target, ttstart[j]), step))
                if not 0 <= column < 2000:
                    raise ValueError('Legacy rebinned detector index exceeds its 2000-point work array')
                npt[j] = max(npt[j], column + 1)
                if error > 0:
                    weight = _div(1, _mul(error, error))
                    if not np.isfinite(weight) or not np.isfinite(value):
                        raise ValueError('Legacy interpolation weight/value is nonfinite in REAL arithmetic')
                    if sumw[j, column] > 0:
                        difference = _sub(value, _div(wsint[j, column], sumw[j, column]))
                        diff_error = _sqrt(_add(_div(1, sumw[j, column]), _div(1, weight)))
                        file_conflicts += int(abs(difference) > _mul(4, diff_error))
                    wsint[j, column] = _add(wsint[j, column], _mul(value, weight))
                    sumw[j, column] = _add(sumw[j, column], weight)
                    accepted[j, column] += 1
    if not np.all(np.isfinite(wsint)) or not np.all(np.isfinite(sumw)):
        raise ValueError('Legacy per-detector weighted sums overflowed REAL')

    # Source 438–475: sequential overlap sums, followed by the >2sigma decision.
    differences = np.zeros(31, dtype=np.float32)
    undefined_overlaps = []
    for j in range(31):
        offset = _nint(_div(_sub(ttstart[j+1], ttstart[j]), step))
        if offset < 0:
            raise ValueError('Legacy overlap would address a negative detector index')
        y1 = y2 = w1 = w2 = F32(0)
        for i in range(offset, int(npt[j])):
            other = i - offset
            if other >= 2000:
                raise ValueError('Legacy overlap exceeds the next detector work array')
            y1 = _add(y1, wsint[j, i])
            y2 = _add(y2, wsint[j+1, other])
            w1 = _add(w1, sumw[j, i])
            w2 = _add(w2, sumw[j+1, other])
        if w1 <= 0 or w2 <= 0:
            # Original 0/0 -> NaN -> significance comparison False -> diff=0.
            # Avoid producing the intermediate NaN while preserving the result.
            undefined_overlaps.append([j + 1, j + 2])
            continue
        mean1, mean2 = _div(y1, w1), _div(y2, w2)
        error1, error2 = _div(1, _sqrt(w1)), _div(1, _sqrt(w2))
        threshold = _mul(2, _sqrt(_add(_mul(error1, error1), _mul(error2, error2))))
        difference = _sub(mean1, mean2)
        if abs(difference) > threshold:
            differences[j] = difference
    corrections = np.zeros(32, dtype=np.float32)
    if background:
        # Detector 20 is the original baseline (index 19 in Python).
        for j in range(18, -1, -1):
            corrections[j] = _sub(corrections[j+1], differences[j])
        for j in range(20, 32):
            corrections[j] = _add(corrections[j-1], differences[j-1])
    bank_intensity = np.zeros_like(wsint)
    bank_sigma = np.zeros_like(sumw)
    for j in range(32):
        for i in range(int(npt[j])):
            if sumw[j, i] > 0:
                bank_intensity[j, i] = _add(_div(wsint[j, i], sumw[j, i]), corrections[j])
                bank_sigma[j, i] = _div(1, _sqrt(sumw[j, i]))
    if not np.all(np.isfinite(bank_intensity)) or not np.all(np.isfinite(bank_sigma)):
        raise ValueError('Legacy detector normalization/background is nonfinite')

    # Source 525–529 defines the master range from detector1 and detector32,
    # even when either has masks. Intermediate detectors may not exceed it.
    tstart = ttstart[0]
    tend = _add(ttstart[-1], _mul(_sub(npt[-1], 1), step))
    size = 1 + _nint(_div(_sub(tend, tstart), step))
    if not 1 <= size <= 99995:
        raise ValueError('Legacy uniform output exceeds the safe historical MAX_OUT/padding range')
    header_start = _int(_mul(100, tstart))
    header_step = _int(_mul(100, step))
    if not math.isclose(header_step / 100, nominal_step, rel_tol=0, abs_tol=1e-9):
        raise ValueError(f'Legacy header step {header_step/100:g} differs from input step {nominal_step:g}; historical INT truncation would mislabel angles')
    origin_tolerance = max(1e-6, 4 * abs(float(np.spacing(tstart))))
    if abs(header_start / 100 - float(tstart)) > origin_tolerance:
        raise ValueError('Legacy header start is truncated away from the arithmetic grid and would mislabel angles')

    values = np.zeros(size, dtype=np.float32)
    weights = np.zeros(size, dtype=np.float32)
    contributions = np.zeros(size, dtype=np.int64)
    for j in range(32):
        offset = _nint(_div(_sub(ttstart[j], ttstart[0]), step))
        if offset < 0 or offset + npt[j] > size:
            raise ValueError('Legacy detector range extends beyond the first/last-detector master grid')
        for i in range(int(npt[j])):
            row = i + offset
            if bank_sigma[j, i] > 0:
                # This second 1/sigma^2 is essential: it is NOT exactly sumw
                # after its intervening REAL sqrt and reciprocal roundings.
                weight = _div(1, _mul(bank_sigma[j, i], bank_sigma[j, i]))
                if weights[row] > 0:
                    difference = _sub(bank_intensity[j, i], _div(values[row], weights[row]))
                    diff_error = _sqrt(_add(_div(1, weights[row]), _div(1, weight)))
                    detector_conflicts += int(abs(difference) > _mul(4, diff_error))
                values[row] = _add(values[row], _mul(bank_intensity[j, i], weight))
                weights[row] = _add(weights[row], weight)
                contributions[row] += accepted[j, i]
    if not np.all(np.isfinite(values)) or not np.all(np.isfinite(weights)):
        raise ValueError('Legacy master weighted sums overflowed REAL')
    valid = weights > 0
    sigmas = np.zeros(size, dtype=np.float32)
    for row in np.flatnonzero(valid):
        values[row] = _div(values[row], weights[row])
        sigmas[row] = _div(1, _sqrt(weights[row]))

    # Source 617–638: no vectorized np.sum, which would change association.
    normalization = F32(1)
    if rescale:
        total, count = F32(0), 0
        for row in range(size):
            if sigmas[row] > 0:
                total = _add(total, _div(values[row], _mul(sigmas[row], sigmas[row])))
                count += 1
        normalization = _div(total, count) if count else F32(1)
        if not np.isfinite(normalization) or normalization <= 0:
            raise ValueError('Legacy count-rescaling factor is nonfinite or nonpositive')
        for row in range(size):
            if sigmas[row] > 0:
                values[row] = _mul(normalization, values[row])
                sigmas[row] = _mul(normalization, sigmas[row])
    if (not np.all(np.isfinite(values)) or not np.all(np.isfinite(sigmas)) or
            np.any(sigmas[valid] <= 0)):
        raise ValueError('Legacy final intensity/ESD is nonfinite or its valid ESD is not positive')
    if not np.any(valid):
        raise ValueError('Legacy output has no valid weighted bins')
    angles = header_start / 100 + np.arange(size, dtype=np.float64) * (header_step / 100)
    flags = ('s' if len(inputs) > 1 else '') + ('m' if not interpolate else '')
    flags += ('' if background else 'o') + ('' if rescale else 'r')
    metadata = {
        'engine': 'python', 'mode': 'legacy', 'implementation': 'Python/NumPy explicit float32 GFORMAT arithmetic',
        'reference_source_sha256': '4b449fe4b9e07959863f7c26e665bc1ee38c97c43f25cfc4d61e7c5bc006f7b3',
        'sources': [str(d.source) for d in datasets], 'uncertainty': uncertainty,
        'method': method, 'background': background, 'rescale': rescale,
        'historical_flags': ['-' + flags] if flags else [],
        'monitor': float(monitors[0]), 'input_monitors': [float(m) for m in monitors],
        'legacy_tstart_deg': float(tstart), 'legacy_step_deg': float(step),
        'legacy_tend_deg': float(tend), 'legacy_nominal_input_step_deg': nominal_step,
        'historical_header_start_centidegrees': header_start,
        'historical_header_step_centidegrees': header_step,
        'output_step': header_step / 100, 'grid_origin': header_start / 100,
        'endpoint': 'Historical IPT-1 interval loop with both-endpoint masking' if interpolate else 'All samples, Fortran NINT nearest',
        'angle_tolerance': .00011, 'zero_count': 'Fortran sigma=1 whenever scaled intensity is zero',
        'negative_interpolation_ratios': negative_ratios,
        'background_offsets': [float(c) for c in corrections],
        'background_undefined_overlap_pairs': undefined_overlaps,
        'rescale_factor': float(normalization), 'invalid_points': int((~valid).sum()),
        'statistical_conflicts_over_4sigma': {'within_detector': file_conflicts, 'between_detectors': detector_conflicts},
        'calibration_uncertainties_propagated': False, 'interpolation_correlations_retained': False,
        'contributions_definition': 'Number of accepted file/bank interpolation estimates or nearest samples, not distinct raw count rows',
        'internal_precision': 'IEEE float32 at each operation; returned I/ESD losslessly promoted to float64 before F8.0 serialization',
        'notes': [
            'Entire reduction executes in Python/NumPy without Fortran execution, compiler discovery or subprocesses.',
            'Monitor and detector calibration factors are treated as exact.',
            'Legacy-grid policies are independent of the selectable uncertainty formula.',
            'Historical linear-sigma propagation is retained only as an explicit compatibility choice.',
            'No covariance is available; interpolation can correlate neighboring output bins.',
            'Unsafe historical buffer/geometry/header failures are rejected rather than reproduced.',
            'Returned valid mask is based on actual accumulated weights, before integer GSAS quantization.',
        ],
    }
    return ReducedPattern(angles, values.astype(np.float64), sigmas.astype(np.float64),
                          valid, contributions, metadata, None)
