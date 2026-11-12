"""End-to-end CLI regression tests for the two entirely Python reductions.

These tests deliberately remove compilers from PATH and make process creation or
an import of the former native backend fail. Fortran is an independent oracle in
other test modules; neither product mode may rely on it at runtime.

The fixture is a complete, ordered 32-detector RAW layout. Only detector 1 has
usable measurements, leaving long masked regions that must remain on the same
uniform grid. Its half-step zero correction gives a hand-calculable first bin:
I=(100+400)/2=250; original sigma=(10+20)/2=15; independent variance
=(100+400)/4=125. The final bank has 199 samples, forcing a partially filled
GSAS record and testing that padding cannot turn into extra observations.
"""
import builtins
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

from bt1modern import cli
from bt1modern.gsas import read_gsas
from bt1modern.io import Bank, RawDataset, write_raw


def controlled_raw(path):
    header = {
        'title': 'Python analytical first-bin mode test',
        'wavelength': 2.0777,
        'collimation': (60, 20),
        'experimental_parameters': (0, 0, 0, 1, 1),
        'monochromator': 'GE311',
        'monitor': 100,
        'date': '4-Nov-2026',
        'format': 'raw',
    }
    banks = []
    for detector in range(1, 33):
        n = 199 if detector == 32 else 201
        angles = 1.30 + 5*(detector-1) + .05*np.arange(n)
        counts = np.full(n, -1, dtype=int)
        if detector == 1:
            counts[:] = 100
            counts[1] = 400
        banks.append(Bank(detector, angles, counts, 1., .025 if detector == 1 else 0.))
    return RawDataset(header, banks, str(path))


class PythonModeCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='bt1-python-modes-')
        self.root = Path(self.temporary.name)
        self.source = self.root/'controlled.raw'
        write_raw(controlled_raw(self.source), self.source)
        self.original_bytes = self.source.read_bytes()
        self.source_sha = hashlib.sha256(self.original_bytes).hexdigest()

    def tearDown(self):
        self.temporary.cleanup()

    def invoke(self, *args):
        """Run the real CLI while making every native runtime route unavailable."""
        stdout, stderr = io.StringIO(), io.StringIO()
        original_import = builtins.__import__

        def checked_import(name, *positional, **keywords):
            if name == 'native' or name == 'bt1modern.native' or name.endswith('.native'):
                raise AssertionError('Product CLI attempted to import the former native backend')
            return original_import(name, *positional, **keywords)

        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            stack.enter_context(mock.patch.dict(os.environ, {'PATH': '/nonexistent/compiler-free-path'}))
            stack.enter_context(mock.patch('subprocess.run', side_effect=AssertionError('Product CLI spawned a process')))
            stack.enter_context(mock.patch('subprocess.Popen', side_effect=AssertionError('Product CLI spawned a process')))
            stack.enter_context(mock.patch('os.system', side_effect=AssertionError('Product CLI used a shell')))
            stack.enter_context(mock.patch('builtins.__import__', side_effect=checked_import))
            code = cli.main(list(map(str, args)))
        return code, stdout.getvalue(), stderr.getvalue()

    def conversion(self, label, *options):
        output = self.root/label
        code, stdout, error = self.invoke('convert', self.source, '--out-dir', output, *options)
        self.assertEqual(code, 0, error)
        record = json.loads((output/'controlled.json').read_text())
        csv = np.genfromtxt(output/'controlled.csv', delimiter=',', names=True)
        bank, = read_gsas(output/'controlled.gsas')
        return output, record, csv, bank, json.loads(stdout)

    def assert_preserved_and_python_only(self, output, record):
        self.assertEqual(self.source.read_bytes(), self.original_bytes)
        self.assertEqual(record['inputs'][0]['sha256'], self.source_sha)
        self.assertEqual(record['inputs'][0]['path'], str(self.source.resolve()))
        # RAW input is already prepared, and the product creates only the three
        # requested scientific artifacts. Native executables/logs/references
        # must not be smuggled into a Python-mode conversion.
        self.assertEqual({p.name for p in output.iterdir()},
                         {'controlled.gsas', 'controlled.csv', 'controlled.json'})
        self.assertFalse(list(output.rglob('*.fortran.*')))
        self.assertFalse(list(output.rglob('*.inst')))

    def test_default_correct_mode_without_compiler_or_processes(self):
        output, record, csv, bank, _ = self.conversion('default-correct')
        self.assertEqual(record['reduction']['mode'], 'correct')
        self.assertEqual(record['reduction']['uncertainty'], 'independent')
        self.assertAlmostEqual(csv['two_theta_deg'][0], 1.30, places=8)
        self.assertAlmostEqual(csv['intensity'][0], 250., places=6)
        self.assertAlmostEqual(csv['sigma'][0], np.sqrt(125), places=6)
        self.assertAlmostEqual(bank.sigma[0], 11.180, places=3)
        self.assert_preserved_and_python_only(output, record)

    def test_legacy_default_uses_original_uncertainty_entirely_in_python(self):
        output, record, csv, bank, _ = self.conversion('legacy-original', '--mode', 'legacy')
        self.assertEqual(record['reduction']['mode'], 'legacy')
        self.assertEqual(record['reduction']['uncertainty'], 'legacy-linear')
        # Native-equivalent REAL arithmetic can move the internal result by a
        # few ulps; the original F8.0 exchange result is exactly (250,15).
        self.assertAlmostEqual(csv['intensity'][0], 250., delta=.001)
        self.assertAlmostEqual(csv['sigma'][0], 15., delta=.0001)
        self.assertEqual(bank.intensity[0], 250.)
        self.assertEqual(bank.sigma[0], 15.)
        self.assert_preserved_and_python_only(output, record)

    def test_legacy_corrected_uncertainty_is_an_independent_choice(self):
        output, record, csv, bank, _ = self.conversion('legacy-independent',
            '--mode', 'legacy', '--uncertainty', 'independent')
        self.assertEqual(record['reduction']['mode'], 'legacy')
        self.assertEqual(record['reduction']['uncertainty'], 'independent')
        self.assertAlmostEqual(csv['intensity'][0], 250., delta=.001)
        self.assertAlmostEqual(csv['sigma'][0], np.sqrt(125), delta=.0001)
        self.assertEqual(bank.intensity[0], 250.)
        self.assertEqual(bank.sigma[0], 11.)
        # CSV contains the internal REAL-equivalent sigma, while the legacy
        # exchange file intentionally uses the historical integer field.
        self.assertGreater(abs(csv['sigma'][0] - bank.sigma[0]), .1)
        self.assertIn('float32', json.dumps(record['reduction']).lower())
        self.assertIn('F8.0', json.dumps(record['gsas']))
        self.assert_preserved_and_python_only(output, record)

    def test_both_modes_keep_one_uniform_grid_holes_and_blank_padding(self):
        for mode in ['correct', 'legacy']:
            with self.subTest(mode=mode):
                output, record, csv, bank, _ = self.conversion('grid-'+mode, '--mode', mode)
                self.assertEqual(len(record['gsas']['banks']), 1)
                self.assertEqual(len(bank.angles), len(csv))
                self.assertGreater(len(bank.angles), 3000)
                np.testing.assert_allclose(np.diff(bank.angles), .05, rtol=0, atol=1e-9)
                invalid = csv['valid'] == 0
                self.assertGreater(int(invalid.sum()), 2000)
                np.testing.assert_array_equal(bank.intensity[invalid], 0.)
                np.testing.assert_array_equal(bank.sigma[invalid], 0.)
                lines = (output/'controlled.gsas').read_text().splitlines()
                self.assertTrue(all(len(line) == 80 for line in lines))
                remaining = len(bank.angles) % 5
                self.assertNotEqual(remaining, 0, 'Fixture must exercise partial final record')
                self.assertEqual(lines[-1][16*remaining:], ' '*(80-16*remaining))
                self.assert_preserved_and_python_only(output, record)

    def test_legacy_rejects_unsupported_overrides_before_output(self):
        conflicts = [('--step', '.05'), ('--monitor', '100'), ('--covariance',),
                     ('--endpoint', 'inclusive'), ('--zero-count', 'one'),
                     ('--zero-count', 'exclude'), ('--gap-policy', 'split')]
        for index, option in enumerate(conflicts):
            with self.subTest(option=option):
                output = self.root/f'unsupported-{index}'
                code, _, error = self.invoke('convert', self.source, '--out-dir', output,
                    '--mode', 'legacy', *option)
                self.assertEqual(code, 2, error)
                self.assertFalse(output.exists())
                self.assertEqual(self.source.read_bytes(), self.original_bytes)

    def test_legacy_only_switches_are_rejected_by_correct_mode(self):
        for index, option in enumerate(['--legacy-background', '--legacy-rescale']):
            with self.subTest(option=option):
                output = self.root/f'correct-conflict-{index}'
                code, _, error = self.invoke('convert', self.source, '--out-dir', output, option)
                self.assertEqual(code, 2, error)
                self.assertFalse(output.exists())
                self.assertEqual(self.source.read_bytes(), self.original_bytes)

    def test_correct_mode_can_request_legacy_sigma_without_legacy_geometry(self):
        _, record, csv, bank, _ = self.conversion('correct-original-sigma',
            '--mode', 'correct', '--uncertainty', 'legacy-linear')
        self.assertEqual(record['reduction']['mode'], 'correct')
        self.assertEqual(record['reduction']['uncertainty'], 'legacy-linear')
        self.assertAlmostEqual(csv['intensity'][0], 250., places=6)
        self.assertAlmostEqual(csv['sigma'][0], 15., places=6)
        self.assertEqual(bank.sigma[0], 15.)


if __name__ == '__main__':
    unittest.main()
