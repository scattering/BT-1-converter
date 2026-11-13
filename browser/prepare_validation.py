#!/usr/bin/env python3
"""Prepare read-only fixture copies and a native reference for browser checks.

Run from the project root:
    .venv/bin/python browser/prepare_validation.py
Generated acquisition copies and results stay under ignored artifacts/.
An existing baseline is never overwritten; select a fresh --out to repeat.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(0, str(REPOSITORY/'tests'))

from bt1modern.io import write_raw
from browser.validation import digest, run_validation
from test_cli_python_modes import controlled_raw
from test_io import synthetic_bt1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=REPOSITORY/'artifacts'/'pyodide-validation')
    parser.add_argument('--real-fixture-dir', type=Path, default=Path('/Users/williamratcliff/Documents/Codex/2026-11-03/task-4/bt1_retrieval'))
    parser.add_argument('--synthetic-only', action='store_true')
    args = parser.parse_args()
    root = args.out.resolve()
    if (root/'native-baseline.json').exists() or (root/'results').exists():
        parser.error(f'Use a fresh --out; baseline/results already exist in {root}')
    inputs = root/'inputs'
    inputs.mkdir(parents=True, exist_ok=False)
    raw = controlled_raw(inputs/'controlled.raw')
    raw.banks[0].counts[5:7] = 0
    raw.banks[0].counts[20] = -1
    write_raw(raw, inputs/'controlled.raw')
    second = controlled_raw(inputs/'second.raw')
    second.header['monitor'] = 237
    second.banks[0].counts[second.banks[0].counts >= 0] *= 3
    write_raw(second, inputs/'second.raw')
    empty = controlled_raw(inputs/'empty.raw')
    for bank in empty.banks:
        bank.counts[:] = -1
    write_raw(empty, inputs/'empty.raw')
    (inputs/'complete.bt1').write_text(synthetic_bt1(planned=8, masks=True))
    (inputs/'partial.bt1').write_text(synthetic_bt1(planned=8, recorded=4, footer=False))
    (inputs/'malformed.bt1').write_text(synthetic_bt1(planned=8).replace('M4= 1.350', 'M4= 1.375'))
    provenance = []
    if not args.synthetic_only:
        for name in ('Mn5Si001.bt1', 'Mn5Si002.bt1'):
            source = args.real_fixture_dir/name
            before = digest(source)
            shutil.copyfile(source, inputs/name)
            assert digest(source) == before == digest(inputs/name)
            provenance.append({'name': name, 'source': str(source), 'sha256': before, 'source_preserved': True})
    report = run_validation(root)
    (root/'native-baseline.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    manifest = {'schema_version': 1, 'baseline_url': 'native-baseline.json',
                'inputs': [{'name': p.name, 'url': 'inputs/'+p.name, 'sha256': digest(p), 'bytes': p.stat().st_size} for p in sorted(inputs.iterdir())],
                'provenance': provenance,
                'source_sha256': {str(p.relative_to(REPOSITORY)): digest(p) for p in sorted((REPOSITORY/'bt1modern').glob('*.py'))}}
    (root/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    summary = {'passed': report['passed'], 'runtime': report['runtime'], 'case_count': len(report['cases']),
               'safety_count': len(report['safety']), 'cases': {k: v['passed'] for k, v in report['cases'].items()},
               'safety': {k: v['passed'] for k, v in report['safety'].items()}, 'sources_unchanged': report['sources_unchanged']}
    (root/'native-summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
