"""Pure-Python BT-1 conversion with explicit correct and legacy modes.

Both paths use NumPy/stdlib only; no compiler, executable discovery, subprocess,
Fortran runtime wrapper or network is involved. Fortran is an independent oracle
in the separate test harness. The legacy path explicitly rounds arithmetic at
historical REAL boundaries and selects historical intervals; the correct path
uses enclosing intervals and independent variance by default.

Mode, uncertainty and serialization are recorded rather than inferred from a
filename. A legacy CSV retains the internal float32 values promoted to float64,
while its GSAS output has historical integer F8.0 quantization. Correct-mode CSV
retains float64 and GSAS preserves fractional values. Both default to one uniform
BANK with explicit zero-weight holes and blank final padding. Staging and source
hash checks protect inputs and prevent publishing a partially computed group.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

import numpy as np

from . import __version__
from .io import parse_bt1, prepare_bt1, prepare_legacy_bt1, read_raw, write_raw
from .reduce import reduce_datasets
from .gsas import write_gsas, write_legacy_gsas
from .legacy import reduce_legacy


def _json_default(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _name(text):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', text):
        raise ValueError('Output name must start with a letter/digit and contain only letters, digits, _, ., -')
    return text


def _read(path, allow_partial=False):
    suffix = path.suffix.lower()
    if suffix == '.bt1':
        return parse_bt1(path, allow_partial=allow_partial)
    if suffix == '.raw':
        return read_raw(path)
    raise ValueError(f'Unsupported input extension {suffix!r}; expected .bt1 or .raw')


def _source_record(path, dataset):
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': _digest(path),
            'header': dataset.header,
            'banks': [{'detector': b.detector, 'points': len(b.counts),
                       'first_angle_deg': float(b.angles[0]), 'last_angle_deg': float(b.angles[-1]),
                       'scale': b.scale, 'zero_deg': b.zero,
                       'masked_points': int(np.count_nonzero(b.counts < 0))} for b in dataset.banks]}


def _check_temperatures(datasets, allow_mixed=False):
    temperatures = [d.header.get('temp_summary') for d in datasets]
    means = [float(t['mean']) for t in temperatures if t is not None]
    if means and max(means) - min(means) > 1.0 and not allow_mixed:
        raise ValueError('Combined scans differ by over 1 K in measured mean temperature; '
                         'combine separately or explicitly use --allow-mixed-temperature')
    warnings = []
    if len(means) != len(datasets):
        warnings.append('Some input temperatures are unknown; acquisition equivalence cannot be verified.')
    if means and max(means) - min(means) > 1.0:
        warnings.append('Different measured temperatures were combined by explicit request.')
    return warnings


def _resolve_mode_options(args):
    """Resolve statistical defaults without silently overriding an explicit flag.

    Legacy mode fixes geometry and low-count rules to those of the historical
    reduction. Only its uncertainty formula and historically available reduction
    switches are selectable. Correct mode retains the more general modern API.
    None-valued argparse defaults let us distinguish omission from conflicting
    explicit requests, especially inclusive endpoints and alternative zero rules.
    """
    if args.mode == 'legacy':
        conflicts = []
        if args.step is not None: conflicts.append('--step')
        if args.monitor is not None: conflicts.append('--monitor')
        if args.endpoint not in (None, 'fortran'): conflicts.append('--endpoint inclusive')
        if args.zero_count not in (None, 'fortran'): conflicts.append('--zero-count ' + args.zero_count)
        if args.covariance: conflicts.append('--covariance')
        if args.gap_policy != 'uniform': conflicts.append('--gap-policy split')
        if conflicts:
            raise ValueError('Legacy mode cannot honor ' + ', '.join(conflicts) +
                             '; use --mode correct or omit these overrides')
        args.endpoint = 'fortran'
        args.zero_count = 'fortran'
        args.uncertainty = args.uncertainty or 'legacy-linear'
    else:
        if args.legacy_background or args.legacy_rescale:
            raise ValueError('--legacy-background and --legacy-rescale require --mode legacy')
        args.endpoint = args.endpoint or 'inclusive'
        args.zero_count = args.zero_count or 'one'
        args.uncertainty = args.uncertainty or 'independent'
    if args.covariance and args.uncertainty != 'independent':
        raise ValueError('--covariance requires --uncertainty independent')


def _input_cutoffs(specifications, paths):
    """Resolve explicit per-input preparation choices without changing defaults."""
    selected = set(paths)
    cutoffs = {}
    for specification in specifications or ():
        if '=' not in specification:
            raise ValueError('--input-cutoff requires PATH=DEGREES')
        path_text, value_text = specification.rsplit('=', 1)
        if not path_text or not value_text.strip():
            raise ValueError('--input-cutoff requires a nonempty PATH and DEGREES')
        try:
            cutoff = float(value_text)
        except ValueError as error:
            raise ValueError('--input-cutoff degrees must be a finite nonnegative angle') from error
        if not math.isfinite(cutoff) or cutoff < 0:
            raise ValueError('--input-cutoff degrees must be a finite nonnegative angle')
        try:
            path = Path(path_text).expanduser().resolve(strict=True)
        except FileNotFoundError as error:
            raise ValueError(f'--input-cutoff target does not exist: {path_text}') from error
        if path not in selected:
            raise ValueError(f'--input-cutoff target is not a selected input: {path_text}')
        if path in cutoffs:
            raise ValueError(f'Duplicate --input-cutoff target: {path_text}')
        if path.suffix.lower() != '.bt1':
            raise ValueError('--input-cutoff applies only to .bt1 input; .raw is already prepared')
        cutoffs[path] = cutoff
    return cutoffs


def convert(args):
    _resolve_mode_options(args)
    paths = [p.expanduser().resolve(strict=True) for p in args.inputs]
    if len(set(paths)) != len(paths):
        raise ValueError('The same input path was supplied more than once')
    if any(paths[i].samefile(paths[j]) for i in range(len(paths)) for j in range(i)):
        raise ValueError('Input paths refer to the same file; duplicate data cannot be treated as independent')
    names = [_name(p.stem) for p in paths]
    if len(set(names)) != len(names):
        raise ValueError('Input basenames collide; use distinct names in a separate input directory')
    input_cutoffs = _input_cutoffs(getattr(args, 'input_cutoff', None), paths)
    before = [_digest(p) for p in paths]
    originals = [_read(p, args.allow_partial) for p in paths]
    if [_digest(p) for p in paths] != before:
        raise RuntimeError('An input changed while parsing; retry from a stable snapshot')
    datasets = []
    for path, dataset in zip(paths, originals):
        if dataset.header.get('format') == 'bt1':
            cutoff = input_cutoffs.get(path, args.cutoff)
            if str(dataset.header.get('count_type', '')).upper() != 'NEUT':
                raise ValueError('Only NEUT monitor-preset acquisition is supported; other counting modes need an explicit normalization model')
            if args.mode == 'legacy':
                if dataset.header.get('complete') is not True:
                    raise ValueError('Legacy preparation requires a complete BT-1 scan; use --mode correct for an explicitly allowed partial snapshot')
                dataset = prepare_legacy_bt1(dataset, cutoff, args.drop_detector, args.keep_detector1)
            else:
                dataset = prepare_bt1(dataset, cutoff, args.drop_detector, args.keep_detector1)
        elif args.drop_detector or args.keep_detector1:
            raise ValueError('Detector-cut options apply to .bt1 input; .raw is already prepared')
        datasets.append(dataset)
    warnings = []
    if args.combine:
        warnings.extend(_check_temperatures(datasets, args.allow_mixed_temperature))
        groups = [(_name(args.combine), datasets)]
    else:
        groups = [(name, [data]) for name, data in zip(names, datasets)]
    if args.covariance and args.uncertainty != 'independent':
        raise ValueError('--covariance requires --uncertainty independent')
    instrument = args.instrument_file.expanduser().resolve(strict=True) if args.instrument_file else None
    instrument_hash = _digest(instrument) if instrument else None
    output = args.out_dir.expanduser().resolve()
    expected = []
    if not args.no_raw:
        expected += [name+'.raw' for name, path in zip(names, paths) if path.suffix.lower() == '.bt1']
    for name, _ in groups:
        expected += [name+'.gsas', name+'.csv', name+'.json']
        if args.covariance:
            expected.append(name+'.covariance.npz')
    if instrument:
        expected.append(instrument.name)
    if len(set(expected)) != len(expected):
        raise ValueError('Requested output filenames collide')
    source_paths = paths + ([instrument] if instrument else [])
    for name in expected:
        destination = output/name
        if any(destination.resolve() == p or (destination.exists() and destination.samefile(p)) for p in source_paths):
            raise ValueError(f'Refusing to overwrite an input file: {destination}')
        if destination.exists() and not args.overwrite:
            raise FileExistsError(f'Output exists: {destination}; use a new output directory or --overwrite')
        if destination.is_dir():
            raise IsADirectoryError(destination)
    output.mkdir(parents=True, exist_ok=True)
    summaries = []
    with tempfile.TemporaryDirectory(prefix='.bt1modern-', dir=output) as temporary:
        stage = Path(temporary)
        if instrument:
            shutil.copyfile(instrument, stage/instrument.name)
            if _digest(stage/instrument.name) != instrument_hash or _digest(instrument) != instrument_hash:
                raise RuntimeError('Instrument file changed while copying; retry from a stable snapshot')
        if not args.no_raw:
            for name, data, path in zip(names, datasets, paths):
                if path.suffix.lower() == '.bt1':
                    write_raw(data, stage/(name+'.raw'))
        for name, members in groups:
            if args.mode == 'legacy':
                try:
                    pattern = reduce_legacy(members, uncertainty=args.uncertainty,
                        method=args.method, background=args.legacy_background, rescale=args.legacy_rescale)
                except ValueError as error:
                    if input_cutoffs and 'identical per-detector grids and calibrations' in str(error):
                        raise ValueError(f'{error}; per-input cutoffs can produce different prepared grids. '
                                         'Use compatible preparation choices or convert separately; '
                                         'correct mode supports differing prepared grids') from error
                    raise
            else:
                pattern = reduce_datasets(members, output_step=args.step, monitor=args.monitor,
                    uncertainty=args.uncertainty, endpoint=args.endpoint, zero_count=args.zero_count,
                    covariance=args.covariance, method=args.method)
            pattern.metadata['mode'] = args.mode
            pattern.metadata['engine'] = 'python'
            pattern.metadata['runtime'] = 'Python and NumPy only; no native program execution'
            pattern.metadata['csv_precision'] = ('Internal float32 values promoted exactly to float64; GSAS applies historical F8.0 rounding.'
                if args.mode == 'legacy' else 'Internal float64 values; GSAS fixed fields may quantize the CSV values.')
            title = members[0].header.get('title', name)
            if len(members) > 1:
                title = f'{name}: combined {len(members)} scans'
            if args.mode == 'legacy':
                # Legacy F8.0 must never silently erase positive uncertainty in
                # an otherwise positive-I observation: the writer rejects that
                # unimportable case and advises the fractional correct mode.
                gsas = write_legacy_gsas(pattern, stage/(name+'.gsas'), title=title,
                    instrument_reference=instrument.name if instrument else None)
            else:
                gsas = write_gsas(pattern, stage/(name+'.gsas'), title=title,
                    instrument_reference=instrument.name if instrument else None, gap_policy=args.gap_policy)
            gsas['path'] = str(output/(name+'.gsas'))
            np.savetxt(stage/(name+'.csv'), np.column_stack([pattern.angles, pattern.intensity,
                pattern.sigma, pattern.valid.astype(int), pattern.contributions]), delimiter=',',
                fmt=['%.17g', '%.17g', '%.17g', '%d', '%d'],
                header='two_theta_deg,intensity,sigma,valid,contributions', comments='')
            covariance = None
            if pattern.covariance_factor is not None:
                factor = pattern.covariance_factor
                metadata = {k: v for k, v in factor.items() if k not in ('indptr', 'indices', 'data')}
                metadata['row_angles_file'] = name+'.csv'
                metadata['sample_indices'] = 'Input bank indices after preparation; see removed_leading_points in source header'
                np.savez_compressed(stage/(name+'.covariance.npz'), indptr=factor['indptr'],
                    indices=factor['indices'], data=factor['data'], shape=np.asarray(factor['shape'], dtype=np.int64),
                    metadata_json=np.asarray(json.dumps(metadata, default=_json_default)))
                covariance = {'file': name+'.covariance.npz', 'format': factor['format'],
                              'definition': factor['definition'], 'nonzero_entries': len(factor['data'])}
            members_paths = [Path(d.source).resolve() for d in members]
            record = {'software': {'name': 'bt1modern', 'version': __version__, 'python': sys.version,
                                   'numpy': np.__version__},
                'created_utc': datetime.now(timezone.utc).isoformat(),
                'inputs': [_source_record(p, d) for p, d in zip(members_paths, members)],
                'reduction': pattern.metadata, 'gsas': gsas, 'covariance': covariance,
                'warnings': warnings + gsas['warnings'],
                'limits': ['Absorption correction is not implemented; background offsets/rescaling are explicit legacy switches.',
                          'Calibration and monitor factors are treated as exact; Poisson variances use observed counts.',
                          'A calibrated instrument profile is required for quantitative refinement.',
                          'GSAS stores marginal ESDs only; only correct mode offers a conditional covariance factor.',
                          'Uniform-grid missing bins are explicit zero-weight placeholders, not measured zero intensity.'],
                'instrument_file': {'path': str(instrument), 'sha256': _digest(stage/instrument.name)} if instrument else None}
            if any(d.header.get('complete') is False for d in members):
                record['warnings'].append('Explicitly converted an incomplete snapshot; this is not a complete scan.')
            if any(d.header.get('complete') is None for d in members):
                record['warnings'].append('RAW input does not establish acquisition completeness.')
            artifacts = [name+'.gsas', name+'.csv'] + ([name+'.covariance.npz'] if covariance else [])
            record['outputs'] = {f: {'sha256': _digest(stage/f), 'bytes': (stage/f).stat().st_size} for f in artifacts}
            (stage/(name+'.json')).write_text(json.dumps(record, indent=2, default=_json_default, allow_nan=False)+'\n')
            summaries.append({'name': name, 'mode': args.mode, 'uncertainty': args.uncertainty, 'valid_points': int(pattern.valid.sum()),
                'gsas_banks': len(gsas['banks']), 'output': str(output/(name+'.gsas')),
                'warnings': record['warnings']})
        if [_digest(p) for p in paths] != before:
            raise RuntimeError('An input changed during conversion; no staged outputs were published')
        if instrument and _digest(instrument) != instrument_hash:
            raise RuntimeError('Instrument file changed during conversion; no staged outputs were published')
        for filename in expected:
            target = output/filename
            if args.overwrite:
                os.replace(stage/filename, target)
            else:
                # Exclusive publication also protects against a file appearing
                # after the initial preflight check; stage is on the same disk.
                os.link(stage/filename, target)
    return summaries


def main(argv=None):
    parser = argparse.ArgumentParser(description='Convert BT-1 data to uniform-grid GSAS using entirely Python correct or legacy modes.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect', help='Read metadata and completeness without writing files')
    inspect.add_argument('inputs', nargs='+', type=Path)
    conv = commands.add_parser('convert', help='Write separate reductions unless --combine is supplied')
    conv.add_argument('inputs', nargs='+', type=Path)
    conv.add_argument('--out-dir', required=True, type=Path)
    conv.add_argument('--mode', choices=['correct', 'legacy'], default='correct',
                      help='Both are Python: correct uses strict interpolation (default), legacy reproduces historical arithmetic and grid rules')
    conv.add_argument('--combine', metavar='NAME', help='Explicitly combine equivalent scans by inverse-variance weighting')
    conv.add_argument('--allow-mixed-temperature', action='store_true', help='Permit explicit combination of scans differing by over 1 K')
    conv.add_argument('--allow-partial', action='store_true', help='Permit and label an acquired prefix in correct mode only')
    conv.add_argument('--cutoff', type=float, default=1.0, help='M4 cutoff for detectors 1–3 in .bt1 files (default: 1 degree)')
    conv.add_argument('--input-cutoff', action='append', default=[], metavar='PATH=DEGREES',
                      help='Override the M4 cutoff for one selected .bt1 path; repeat for multiple inputs. '
                           'Other inputs use --cutoff. Legacy combination still requires compatible prepared grids')
    conv.add_argument('--keep-detector1', action='store_true')
    conv.add_argument('--drop-detector', action='append', type=int, default=[], metavar='N')
    conv.add_argument('--step', type=float, help='Output step in degrees; default smallest input spacing')
    conv.add_argument('--monitor', type=float, help='Output monitor preset; default first scan in each reduction')
    conv.add_argument('--method', choices=['interpolate', 'nearest'], default='interpolate')
    conv.add_argument('--uncertainty', choices=['independent', 'legacy-linear'], default=None,
                      help='Default: independent in correct mode, legacy-linear in legacy mode; selectable separately from geometry')
    conv.add_argument('--endpoint', choices=['inclusive', 'fortran'], default=None,
                      help='Default: inclusive for correct mode, historical interval endpoint rule for legacy')
    conv.add_argument('--zero-count', choices=['one', 'fortran', 'exclude'], default=None,
                      help='Default: raw one-count floor for correct mode, historical final sigma=1 for legacy')
    conv.add_argument('--gap-policy', choices=['uniform', 'split'], default='uniform',
                      help='Default: single uniform BANK with zero-weight holes; split is an opt-in correct-mode export')
    conv.add_argument('--legacy-background', action='store_true', help='Enable historical detector-background offsets in legacy mode')
    conv.add_argument('--legacy-rescale', action='store_true', help='Enable historical count rescaling in legacy mode')
    conv.add_argument('--covariance', action='store_true', help='Correct independent mode only: save sparse F with covariance = F @ F.T')
    conv.add_argument('--no-raw', action='store_true', help='Skip legacy RAW export (e.g. when its fixed widths cannot represent the input)')
    conv.add_argument('--instrument-file', type=Path, help='Copy and reference an existing user-supplied calibrated instrument file')
    conv.add_argument('--overwrite', action='store_true', help='Replace only named derived outputs; source paths are always protected')
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            result = [_source_record(p.resolve(strict=True), _read(p, allow_partial=True)) for p in args.inputs]
        else:
            result = convert(args)
    except (OSError, ValueError, RuntimeError) as error:
        print(f'bt1modern: {error}', file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, default=_json_default, allow_nan=False))
    return 0
