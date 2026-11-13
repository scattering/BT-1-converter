"""Run identical BT-1 conversion checks in native Python and browser Pyodide.

No numerical tolerance is applied in cross-runtime comparisons. Reports retain
native/browser versions and each explicitly classified environmental difference.
The native converter is used directly; a caller can supply the browser-only
publication context for MEMFS without changing reduction algorithms.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib
import io
import json
import math
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest

import numpy as np

from bt1modern import cli
from bt1modern.gsas import read_gsas


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def invoke(arguments, publication_context=None):
    stdout, stderr = io.StringIO(), io.StringIO()
    publication = publication_context() if publication_context else contextlib.nullcontext()
    with publication, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = cli.main(list(map(str, arguments)))
        except Exception as error:
            return {"code": None, "stdout": stdout.getvalue(), "stderr": stderr.getvalue(),
                    "exception": f"{type(error).__name__}: {error}"}
    return {"code": code, "stdout": stdout.getvalue(), "stderr": stderr.getvalue()}


def array_record(array):
    a = np.asarray(array)
    # JSON has no NaN; an explicit string round-trips the missing-bin marker.
    values = a.tolist()
    def clean(value):
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, float) and not math.isfinite(value):
            return "NaN" if math.isnan(value) else str(value)
        return value
    return {"shape": list(a.shape), "dtype": str(a.dtype), "values": clean(values)}


def snapshot(output):
    result = {}
    for path in sorted(output.iterdir()):
        if not path.is_file():
            continue
        item = {"sha256": digest(path), "bytes": path.stat().st_size}
        if path.suffix == '.json':
            item['metadata'] = json.loads(path.read_text())
        elif path.suffix == '.csv':
            rows = np.genfromtxt(path, delimiter=',', names=True)
            item['columns'] = {name: array_record(rows[name]) for name in rows.dtype.names}
        elif path.name.endswith('.covariance.npz'):
            with np.load(path, allow_pickle=False) as data:
                item['arrays'] = {key: array_record(data[key]) for key in data.files if key != 'metadata_json'}
                item['metadata'] = json.loads(str(data['metadata_json']))
        else:
            item['base64'] = base64.b64encode(path.read_bytes()).decode('ascii')
        result[path.name] = item
    return result


def case_specs(root):
    """Options are identical across environments; only the root path changes."""
    source = root/'inputs'
    specifications = [
        ('correct-covariance', ['controlled.raw'], ['--covariance']),
        ('legacy-default', ['controlled.raw'], ['--mode', 'legacy']),
        ('legacy-independent', ['controlled.raw'], ['--mode', 'legacy', '--uncertainty', 'independent']),
        ('correct-legacy-sigma', ['controlled.raw'], ['--uncertainty', 'legacy-linear']),
        ('correct-nearest', ['controlled.raw'], ['--method', 'nearest']),
        ('legacy-nearest', ['controlled.raw'], ['--mode', 'legacy', '--method', 'nearest']),
        ('correct-combine', ['controlled.raw', 'second.raw'], ['--combine', 'combined', '--covariance']),
        ('legacy-combine', ['controlled.raw', 'second.raw'], ['--mode', 'legacy', '--combine', 'combined']),
        ('correct-bt1', ['complete.bt1'], ['--cutoff', '1.326', '--keep-detector1', '--covariance']),
        ('legacy-bt1', ['complete.bt1'], ['--mode', 'legacy', '--cutoff', '1.326', '--keep-detector1']),
        ('correct-partial', ['partial.bt1'], ['--allow-partial', '--keep-detector1', '--covariance']),
    ]
    if (source/'Mn5Si001.bt1').exists():
        specifications.extend([
            ('real-correct', ['Mn5Si001.bt1'], ['--covariance']),
            ('real-legacy', ['Mn5Si001.bt1'], ['--mode', 'legacy']),
            ('real-correct-combine', ['Mn5Si001.bt1', 'Mn5Si002.bt1'], ['--combine', 'real_combined', '--covariance']),
            ('real-legacy-combine', ['Mn5Si001.bt1', 'Mn5Si002.bt1'], ['--combine', 'real_combined', '--mode', 'legacy']),
        ])
    return specifications


def _array_comparison(actual, expected):
    a = np.asarray(actual['values'], dtype=actual['dtype'])
    b = np.asarray(expected['values'], dtype=expected['dtype'])
    same_shape = a.shape == b.shape
    exact = same_shape and np.array_equal(a, b, equal_nan=True)
    item = {'exact': bool(exact), 'shape': list(a.shape), 'native_shape': list(b.shape),
            'dtype': actual['dtype'], 'native_dtype': expected['dtype']}
    if same_shape:
        equal = (a == b) | (np.isnan(a) & np.isnan(b))
        item['different_elements'] = int(np.count_nonzero(~equal))
        finite = np.isfinite(a) & np.isfinite(b)
        item['max_abs_difference'] = float(np.max(np.abs(a[finite] - b[finite]))) if np.any(finite) else 0.0
        if not exact:
            indices = np.flatnonzero(~equal)[:5]
            item['examples'] = [{'flat_index': int(i), 'browser': str(a.flat[i]), 'native': str(b.flat[i])} for i in indices]
    return item


def _metadata_comparison(actual, expected, root, native_root, *, npz_names=()):
    differences = []
    environment = []
    def walk(a, b, path):
        # Do not hide values: record each differing volatile field explicitly.
        if path in ('$.created_utc', '$.software.python', '$.software.numpy') and a != b:
            environment.append({'field': path, 'browser': a, 'native': b,
                                'reason': 'run timestamp' if path.endswith('created_utc') else 'runtime version'})
            return
        if isinstance(a, str) and isinstance(b, str):
            aa, bb = a.replace(str(root), '<validation-root>'), b.replace(str(native_root), '<validation-root>')
            if aa == bb and a != b:
                environment.append({'field': path, 'browser': a, 'native': b, 'reason': 'validation workspace path'})
                return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a or key not in b:
                    differences.append({'field': path+'.'+key, 'browser': a.get(key), 'native': b.get(key)})
                elif path == '$.outputs' and key in npz_names and a[key] != b[key]:
                    environment.append({'field': path+'.'+key, 'browser': a[key], 'native': b[key],
                        'reason': 'compressed NPZ container identity; array and metadata equivalence checked separately'})
                else:
                    walk(a[key], b[key], path+'.'+key)
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                differences.append({'field': path, 'browser_length': len(a), 'native_length': len(b)})
            else:
                for i, (av, bv) in enumerate(zip(a, b)):
                    walk(av, bv, f'{path}[{i}]')
        elif a != b:
            differences.append({'field': path, 'browser': a, 'native': b})
    walk(actual, expected, '$')
    return {'exact_except_recorded_environment': not differences, 'differences': differences,
            'environment_differences': environment}


def compare_case(actual, expected, root, native_root):
    details = {}
    passed = set(actual) == set(expected)
    npz_names = [name for name in actual if name.endswith('.covariance.npz')]
    for name in sorted(set(actual) | set(expected)):
        if name not in actual or name not in expected:
            details[name] = {'passed': False, 'missing': 'browser' if name not in actual else 'native'}
            passed = False
            continue
        a, b = actual[name], expected[name]
        detail = {'bytes_exact': a['sha256'] == b['sha256'], 'browser_sha256': a['sha256'], 'native_sha256': b['sha256']}
        if name.endswith('.csv'):
            detail['columns'] = {key: _array_comparison(a['columns'][key], b['columns'][key]) for key in a['columns']}
            detail['passed'] = detail['bytes_exact'] and all(value['exact'] for value in detail['columns'].values())
        elif name.endswith('.covariance.npz'):
            detail['arrays'] = {key: _array_comparison(a['arrays'][key], b['arrays'][key]) for key in a['arrays']}
            detail['metadata'] = _metadata_comparison(a['metadata'], b['metadata'], root, native_root)
            detail['passed'] = all(value['exact'] for value in detail['arrays'].values()) and detail['metadata']['exact_except_recorded_environment']
        elif name.endswith('.json'):
            detail['metadata'] = _metadata_comparison(a['metadata'], b['metadata'], root, native_root, npz_names=npz_names)
            detail['passed'] = detail['metadata']['exact_except_recorded_environment']
        else:
            detail['passed'] = detail['bytes_exact']
        passed = passed and detail['passed']
        details[name] = detail
    return {'passed': bool(passed), 'files': details}


def _gsas_invariants(output):
    results = {}
    for file in output.glob('*.gsas'):
        banks = read_gsas(file)
        lines = file.read_text().splitlines()
        bank = banks[0]
        remaining = len(bank.intensity) % 5
        padding = not remaining or lines[-1][remaining*16:] == ' '*(80-remaining*16)
        uniform = len(bank.angles) < 2 or np.allclose(np.diff(bank.angles), np.diff(bank.angles)[0], rtol=0, atol=1e-12)
        results[file.name] = {'one_bank': len(banks) == 1, 'uniform_grid': bool(uniform),
            'blank_padding': bool(padding), 'zero_weight_bins': int(np.count_nonzero(bank.sigma == 0)),
            'records_80_columns': all(len(line) == 80 for line in lines)}
    return results


def run_validation(root, expected=None, publication_context=None):
    root = Path(root).resolve()
    output_root = root/'results'
    output_root.mkdir(parents=True, exist_ok=False)
    sources = {p.name: digest(p) for p in (root/'inputs').iterdir() if p.is_file()}
    report = {'schema_version': 1, 'runtime': {'python': sys.version, 'numpy': np.__version__, 'platform': platform.platform()},
              'root': str(root), 'inputs': sources, 'cases': {}, 'safety': {}, 'passed': True}
    if expected is not None:
        report['input_hashes_match_native'] = sources == expected['inputs']
        report['passed'] = report['input_hashes_match_native']
    snapshots = {}
    for label, names, options in case_specs(root):
        output = output_root/label
        args = ['convert', *(root/'inputs'/name for name in names), '--out-dir', output, *options]
        execution = invoke(args, publication_context)
        item = {'arguments': list(map(str, args)), 'execution': execution, 'passed': execution['code'] == 0}
        if item['passed']:
            snapshots[label] = snapshot(output)
            item['gsas_invariants'] = _gsas_invariants(output)
            item['passed'] = all(all(value[k] for k in ('one_bank', 'uniform_grid', 'blank_padding', 'records_80_columns')) for value in item['gsas_invariants'].values())
            # Verify every recorded artifact digest against the actual bytes.
            item['output_hashes_valid'] = all(
                digest(output/name) == meta['sha256'] and (output/name).stat().st_size == meta['bytes']
                for value in snapshots[label].values() if 'metadata' in value and 'outputs' in value['metadata']
                for name, meta in value['metadata']['outputs'].items())
            item['passed'] = item['passed'] and item['output_hashes_valid']
            if expected is not None:
                item['comparison'] = compare_case(snapshots[label], expected['snapshots'][label], root, expected['root'])
                item['passed'] = item['passed'] and item['comparison']['passed']
        report['cases'][label] = item
        report['passed'] = report['passed'] and item['passed']
    bad_cases = [
        ('malformed', ['malformed.bt1'], [], 'Missing or irregular angle'),
        ('partial-refused', ['partial.bt1'], [], 'Incomplete'),
        ('legacy-partial-refused', ['partial.bt1'], ['--mode', 'legacy', '--allow-partial'], 'complete'),
        ('empty-observations', ['empty.raw'], [], 'valid'),
        ('duplicate-input', ['controlled.raw', 'controlled.raw'], ['--combine', 'duplicate'], 'same input'),
    ]
    for label, names, options, marker in bad_cases:
        output = output_root/label
        execution = invoke(['convert', *(root/'inputs'/name for name in names), '--out-dir', output, *options], publication_context)
        passed = execution['code'] == 2 and marker.lower() in execution['stderr'].lower() and not list(output.glob('*'))
        report['safety'][label] = {'passed': passed, 'execution': execution, 'no_published_files': not list(output.glob('*'))}
    # Check refusal and explicit overwrite for each mode after successful conversion.
    for label in ('correct-covariance', 'legacy-default'):
        if label not in snapshots:
            continue
        output = output_root/label
        args = report['cases'][label]['arguments']
        before = {p.name: digest(p) for p in output.iterdir() if p.is_file()}
        refused = invoke(args, publication_context)
        unchanged = before == {p.name: digest(p) for p in output.iterdir() if p.is_file()}
        report['safety'][label+'-existing-refused'] = {'passed': refused['code'] == 2 and unchanged and 'Output exists' in refused['stderr'], 'unchanged': unchanged, 'execution': refused}
        replaced = invoke([*args, '--overwrite'], publication_context)
        after = snapshot(output) if replaced['code'] == 0 else {}
        comparison = compare_case(after, snapshots[label], root, root)
        report['safety'][label+'-overwrite'] = {'passed': replaced['code'] == 0 and comparison['passed'], 'execution': replaced, 'comparison': comparison}
    # A symlink to an input must remain protected even with --overwrite.
    protected = output_root/'source-protection'
    protected.mkdir()
    alias = protected/'controlled.gsas'
    alias.symlink_to(root/'inputs'/'controlled.raw')
    protected_run = invoke(['convert', root/'inputs'/'controlled.raw', '--out-dir', protected, '--overwrite'], publication_context)
    report['safety']['symlink-source-protection'] = {'passed': protected_run['code'] == 2 and 'input file' in protected_run['stderr'], 'execution': protected_run}
    after_sources = {p.name: digest(p) for p in (root/'inputs').iterdir() if p.is_file()}
    report['sources_unchanged'] = sources == after_sources
    report['passed'] = bool(report['passed'] and report['sources_unchanged'] and all(case['passed'] for case in report['safety'].values()))
    if expected is None:
        report['snapshots'] = snapshots
    return report


def run_portable_tests(test_directory, publication_context=None):
    """Reuse repository tests; report omitted native-only capabilities explicitly."""
    directory = str(Path(test_directory).resolve())
    if directory not in sys.path:
        sys.path.insert(0, directory)
    modules = ['test_cli_python_modes', 'test_cli_safety', 'test_gsas', 'test_io',
               'test_legacy_kernel', 'test_native_serialization', 'test_python_legacy_io', 'tests_reduce']
    unavailable = []
    try:
        with tempfile.TemporaryDirectory() as directory_name:
            source = Path(directory_name)/'source'
            source.write_text('probe')
            os.link(source, Path(directory_name)/'linked')
    except (AttributeError, OSError, NotImplementedError) as error:
        unavailable = ['test_duplicate_path_and_hardlinked_alias_are_rejected', 'test_overwrite_cannot_replace_source_alias']
        hardlink_reason = f'Native hardlinks unavailable: {type(error).__name__}: {error}; duplicate path and symlink protection checked separately'
    suite = unittest.TestSuite()
    omitted = [{'test': 'test_python_legacy_differential.PythonLegacyDifferentialTests', 'reason': 'Independent compiled Fortran oracle remains native-only'}]
    def add_tests(collection):
        for test in collection:
            if isinstance(test, unittest.TestSuite):
                add_tests(test)
            elif test._testMethodName in unavailable:
                omitted.append({'test': test.id(), 'reason': hardlink_reason})
            else:
                suite.addTest(test)
    for name in modules:
        add_tests(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name)))
    stream = io.StringIO()
    context = publication_context() if publication_context else contextlib.nullcontext()
    with context:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    return {'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
            'failures': [{'test': test.id(), 'traceback': tb} for test, tb in result.failures],
            'errors': [{'test': test.id(), 'traceback': tb} for test, tb in result.errors],
            'skipped': [{'test': test.id(), 'reason': reason} for test, reason in result.skipped],
            'omitted': omitted, 'log': stream.getvalue()}
