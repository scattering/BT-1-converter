"""BT-1 reduction with explicit statistical and endpoint policies.

Input objects are duck-typed Bank(detector, angles, counts, scale, zero) and
RawDataset(header, banks, source). Angles and zero corrections are degrees.
Monitor and detector calibration factors are treated as known constants.

``covariance_factor`` is an optional CSR matrix F, represented by a dictionary
with NumPy ``indptr``, ``indices``, ``data`` arrays and a ``shape`` tuple. It maps
independent unit-variance raw-count noise to output intensity noise: Cov = F F.T.
This is conditional linear error propagation with observed variances and
inverse-variance weights held fixed, not an exact covariance of their random,
data-dependent estimation.
``column_blocks`` maps its columns back to the input bank and its row *after*
any preparation/cutoff. These are not necessarily original acquisition rows.
This retains interpolation-induced correlations without allocating dense Cov.
"""
from dataclasses import dataclass
from typing import Any
import math
import numpy as np


@dataclass
class ReducedPattern:
    angles: np.ndarray
    intensity: np.ndarray
    sigma: np.ndarray
    valid: np.ndarray
    contributions: np.ndarray
    metadata: dict[str, Any]
    covariance_factor: dict[str, Any] | None = None


def _header_value(header, names):
    for key in names:
        if key in header:
            return header[key]
    return None


def _monitor(dataset):
    value = _header_value(dataset.header, ('monitor', 'mon', 'preset', 'monitor_counts'))
    if value is None:
        raise ValueError(f'Missing monitor preset in {dataset.source!r}')
    value = float(value)
    if not np.isfinite(value) or value <= 0:
        raise ValueError(f'Monitor preset must be finite and positive in {dataset.source!r}')
    return value


def _check_compatibility(datasets):
    fields = {
        'wavelength': ('wavelength', 'lambda'),
        'monochromator': ('monochromator', 'mono'),
        'collimation': ('collimation',),
        'coll1': ('coll1', 'collimation1'),
        'coll2': ('coll2', 'collimation2'),
    }
    for label, aliases in fields.items():
        entries = [(d.source, _header_value(d.header, aliases)) for d in datasets]
        entries = [(s, v) for s, v in entries if v is not None]
        if not entries:
            continue
        reference_source, reference = entries[0]
        for source, value in entries[1:]:
            if label == 'wavelength':
                same = np.isclose(float(reference), float(value), rtol=1e-5, atol=1e-7)
            elif label == 'collimation':
                same = tuple(float(v) for v in reference) == tuple(float(v) for v in value)
            elif label.startswith('coll'):
                same = float(reference) == float(value)
            else:
                same = str(reference).strip().upper() == str(value).strip().upper()
            if not same:
                raise ValueError(f'Incompatible {label}: {reference_source!r}={reference!r}, {source!r}={value!r}')


def _nearest_integer(value):
    """Fortran NINT convention, including negative half-integers."""
    return np.where(value >= 0, np.floor(value + 0.5), np.ceil(value - 0.5)).astype(np.int64)


def reduce_datasets(datasets, *, output_step=None, monitor=None,
                    uncertainty='independent', endpoint='inclusive',
                    zero_count='one', covariance=False, method='interpolate',
                    angle_tolerance=1e-8):
    """Reduce raw observations to a common absolute-zero 2theta grid.

    ``monitor`` is the output normalization preset; None uses the first dataset.
    ``output_step`` defaults to the smallest positive within-bank angle spacing.
    Input spacing may be irregular. An explicitly present negative/nonfinite
    count masks that sample and both adjacent interpolation brackets; omitted
    angle rows cannot be inferred as missing and are ordinary irregular spacing.

    ``zero_count='one'`` uses raw variance max(count, 1), scaled by the square of
    the calibration/monitor factor. ``'fortran'`` instead uses final sigma=1 at
    zero counts, matching historical gformat. ``'exclude'`` masks zero counts.
    These are explicit conventions, not exact small-count confidence intervals.

    ``uncertainty='independent'`` propagates squared interpolation coefficients;
    ``'legacy-linear'`` interpolates standard deviations for comparison only.
    The latter does not specify a consistent joint covariance and cannot request
    ``covariance=True``. All inputs/banks are assumed independent observations.

    ``endpoint='fortran'`` excludes an exactly aligned last sample of each bank
    in interpolation mode, matching gformat's interval loop. It has no effect
    on ``method='nearest'``. Angle comparisons use absolute ``angle_tolerance``
    degrees (default 1e-8), unlike gformat's single-precision 0.00011 tolerance.
    No background matching, absorption correction, or output rescaling occurs.
    """
    datasets = list(datasets)
    if not datasets:
        raise ValueError('At least one dataset is required')
    if uncertainty not in ('independent', 'legacy-linear'):
        raise ValueError("uncertainty must be 'independent' or 'legacy-linear'")
    if endpoint not in ('inclusive', 'fortran'):
        raise ValueError("endpoint must be 'inclusive' or 'fortran'")
    if zero_count not in ('one', 'fortran', 'exclude'):
        raise ValueError("zero_count must be 'one', 'fortran', or 'exclude'")
    if method not in ('interpolate', 'nearest'):
        raise ValueError("method must be 'interpolate' or 'nearest'")
    if covariance and uncertainty != 'independent':
        raise ValueError('Covariance is available only for independent uncertainty propagation')
    if not np.isfinite(angle_tolerance) or angle_tolerance < 0:
        raise ValueError('angle_tolerance must be finite and nonnegative')
    _check_compatibility(datasets)
    monitors = [_monitor(d) for d in datasets]
    target_monitor = monitors[0] if monitor is None else float(monitor)
    if not np.isfinite(target_monitor) or target_monitor <= 0:
        raise ValueError('Output monitor must be finite and positive')

    prepared = []
    spacings = []
    column_blocks = []
    nraw = 0
    for dataset_index, (dataset, preset) in enumerate(zip(datasets, monitors)):
        seen_detectors = set()
        for bank in dataset.banks:
            detector = int(bank.detector)
            if detector in seen_detectors:
                raise ValueError(f'Duplicate detector {detector} in {dataset.source!r}')
            seen_detectors.add(detector)
            angles = np.asarray(bank.angles, dtype=np.float64)
            counts = np.asarray(bank.counts, dtype=np.float64)
            if angles.ndim != 1 or counts.ndim != 1 or angles.shape != counts.shape or not len(angles):
                raise ValueError('Bank angles and counts must be nonempty matching one-dimensional arrays')
            if not np.all(np.isfinite(angles)) or np.any(np.diff(angles) <= 0):
                raise ValueError('Bank angles must be finite and strictly increasing')
            scale, zero = float(bank.scale), float(bank.zero)
            if not np.isfinite(scale) or scale <= 0 or not np.isfinite(zero):
                raise ValueError('Detector scale must be positive/finite and zero correction finite')
            corrected = angles - zero
            spacings.extend(np.diff(angles).tolist())
            factor = scale * target_monitor / preset
            usable = np.isfinite(counts) & (counts >= 0)
            if zero_count == 'exclude':
                usable &= counts > 0
            raw_variance = np.where(usable, np.maximum(counts, 1.0), 0.0)
            with np.errstate(over='ignore', invalid='ignore'):
                intensity = np.where(usable, counts * factor, 0.0)
                sigma = np.sqrt(raw_variance) * factor
            if zero_count == 'fortran':
                sigma = np.where(usable & (counts == 0), 1.0, sigma)
            if not np.all(np.isfinite(intensity[usable])) or not np.all(np.isfinite(sigma[usable])):
                raise ValueError('Normalized counts or uncertainties overflowed')
            prepared.append((corrected, intensity, sigma, usable, nraw))
            column_blocks.append({'start': nraw, 'count': len(counts), 'dataset': dataset_index,
                                  'source': str(dataset.source), 'detector': detector})
            nraw += len(counts)
    if not prepared:
        raise ValueError('Datasets contain no banks')
    if output_step is None:
        if not spacings:
            raise ValueError('An explicit output_step is required for single-point-only banks')
        output_step = min(spacings)
    step = float(output_step)
    if not np.isfinite(step) or step <= 0:
        raise ValueError('output_step must be finite and positive')
    lo = min(b[0][0] for b in prepared)
    hi = max(b[0][-1] for b in prepared)
    tol = max(float(angle_tolerance), np.finfo(float).eps * max(abs(lo), abs(hi), 1.) * 16)
    if tol >= step / 2:
        raise ValueError('angle_tolerance must be less than half the output step')
    if method == 'nearest':
        first = int(_nearest_integer(np.array(lo / step)))
        last = int(_nearest_integer(np.array(hi / step)))
    else:
        first = math.ceil((lo - tol) / step)
        last = math.floor((hi + tol) / step)
    size = max(0, last - first + 1)
    if size > 10_000_000:
        raise ValueError('Output grid exceeds 10 million points; specify a larger step')
    angles = (first + np.arange(size, dtype=np.int64)) * step
    weighted_intensity = np.zeros(size)
    weight_sum = np.zeros(size)
    contributions = np.zeros(size, dtype=np.int64)
    noise_rows = [dict() for _ in range(size)] if covariance else None

    def add_observation(row, indices, coeffs, intensity, sigma, base):
        coeffs = np.asarray(coeffs, dtype=float)
        indices = np.asarray(indices, dtype=np.int64)
        value = float(np.dot(coeffs, intensity[indices]))
        scaled_noise = coeffs * sigma[indices]
        if uncertainty == 'independent':
            variance = float(np.dot(scaled_noise, scaled_noise))
        else:
            variance = float(np.sum(scaled_noise) ** 2)
        if not np.isfinite(variance) or variance <= 0 or not np.isfinite(value):
            raise ValueError('Observation variance must be finite and positive')
        weight = 1.0 / variance
        if not np.isfinite(weight):
            raise ValueError('Observation weight overflowed')
        # A finite observation weight does not guarantee a finite accumulated
        # weight. Defer rejection to the explicit check before normalization.
        with np.errstate(over='ignore', invalid='ignore'):
            weighted_intensity[row] += weight * value
            weight_sum[row] += weight
        contributions[row] += 1
        if noise_rows is not None:
            for source_index, amplitude in zip(indices, scaled_noise):
                column = int(base + source_index)
                noise_rows[row][column] = noise_rows[row].get(column, 0.0) + weight * amplitude

    for x, y, s, usable, base in prepared:
        if method == 'nearest':
            bins = _nearest_integer(x / step) - first
            for j in np.flatnonzero(usable):
                add_observation(int(bins[j]), [j], [1.0], y, s, base)
            continue
        bank_first = max(0, int(np.searchsorted(angles, x[0] - tol, side='left')))
        bank_stop = min(size, int(np.searchsorted(angles, x[-1] + tol, side='right')))
        for row in range(bank_first, bank_stop):
            target = angles[row]
            right = int(np.searchsorted(x, target, side='left'))
            exact = None
            if right < len(x) and abs(x[right] - target) <= tol:
                exact = right
            elif right > 0 and abs(x[right - 1] - target) <= tol:
                exact = right - 1
            if exact is not None:
                if usable[exact] and not (endpoint == 'fortran' and exact == len(x) - 1):
                    add_observation(row, [exact], [1.0], y, s, base)
            elif 0 < right < len(x) and usable[right - 1] and usable[right]:
                left = right - 1
                r = (target - x[left]) / (x[right] - x[left])
                if 0 < r < 1:
                    add_observation(row, [left, right], [1 - r, r], y, s, base)
    if not np.all(np.isfinite(weighted_intensity)) or not np.all(np.isfinite(weight_sum)):
        raise ValueError('Accumulated weighted intensity or weight overflowed; rescale normalization')
    valid = weight_sum > 0
    intensity = np.full(size, np.nan)
    sigma = np.full(size, np.nan)
    intensity[valid] = weighted_intensity[valid] / weight_sum[valid]
    sigma[valid] = np.sqrt(1.0 / weight_sum[valid])
    factor = None
    if noise_rows is not None:
        indptr = [0]
        indices = []
        data = []
        for row, mapping in enumerate(noise_rows):
            if valid[row]:
                for column in sorted(mapping):
                    amplitude = mapping[column] / weight_sum[row]
                    if amplitude:
                        indices.append(column)
                        data.append(amplitude)
            indptr.append(len(indices))
        factor = {'format': 'csr_noise_factor', 'shape': (size, nraw),
                  'indptr': np.asarray(indptr, dtype=np.int64),
                  'indices': np.asarray(indices, dtype=np.int64),
                  'data': np.asarray(data, dtype=np.float64), 'column_blocks': column_blocks,
                  'column_index_definition': 'Within each block, column-start is the input bank row after preparation/cutoff, not necessarily the original acquisition row',
                  'statistical_interpretation': 'Conditional linear error propagation with observed variances/weights held fixed',
                  'definition': 'Covariance = F @ F.T; columns are independent unit-variance raw-count noise'}
    metadata = {'sources': [str(d.source) for d in datasets], 'output_step': step,
                'monitor': target_monitor, 'input_monitors': monitors,
                'uncertainty': uncertainty, 'endpoint': endpoint, 'zero_count': zero_count,
                'method': method, 'angle_tolerance': tol, 'grid_origin': 0.0,
                'calibration_uncertainties_propagated': False,
                'contributions_definition': 'Number of independent bank observations (raw samples in nearest mode)',
                'invalid_points': int(np.count_nonzero(~valid)),
                'interpolation_correlations_retained': bool(covariance),
                'notes': ['Monitor and detector factors treated as exact.',
                          'Conditional linear error propagation with observed variances/weights held fixed.',
                          'Interpolation creates correlations between output bins sharing raw samples.',
                          'No background matching, absorption correction, or global rescaling.']}
    if not covariance and method == 'interpolate':
        metadata['notes'].append('Reported sigmas are marginal uncertainties; off-diagonal covariance is omitted.')
    return ReducedPattern(angles, intensity, sigma, valid, contributions, metadata, factor)


def covariance_entry(factor, row, column):
    """Read one covariance entry from a CSR noise factor without densifying."""
    nrows = factor['shape'][0]
    if not 0 <= row < nrows or not 0 <= column < nrows:
        raise IndexError('Covariance row/column out of range')
    ptr, idx, data = factor['indptr'], factor['indices'], factor['data']
    i, stop_i = int(ptr[row]), int(ptr[row + 1])
    j, stop_j = int(ptr[column]), int(ptr[column + 1])
    value = 0.0
    while i < stop_i and j < stop_j:
        if idx[i] == idx[j]:
            value += data[i] * data[j]
            i += 1
            j += 1
        elif idx[i] < idx[j]:
            i += 1
        else:
            j += 1
    return float(value)
