"""Per-input M4 choices exercise the real CLI and unchanged preparation kernels."""

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

from bt1modern import cli
from bt1modern.io import parse_bt1, prepare_bt1, read_raw, write_raw

if __package__:
    from .test_io import synthetic_bt1
else:
    from test_io import synthetic_bt1


class InputCutoffCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bt1-input-cutoffs-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.a, self.b = self.root/'a.bt1', self.root/'b.bt1'
        for path in (self.a, self.b):
            path.write_text(synthetic_bt1(planned=8), encoding='ascii')
        self.originals = {p: p.read_bytes() for p in (self.a, self.b)}

    def invoke(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(list(map(str, arguments)))
        return code, stdout.getvalue(), stderr.getvalue()

    def run_conversion(self, name, inputs, *options):
        output = self.root/name
        code, _, error = self.invoke('convert', *inputs, '--out-dir', output, *options)
        self.assertEqual(code, 0, error)
        self.assert_sources_preserved()
        return output

    def assert_sources_preserved(self):
        for path, original in self.originals.items():
            self.assertEqual(path.read_bytes(), original)

    def record(self, output, name):
        return json.loads((output/(name+'.json')).read_text())

    def assert_products_equal(self, first, second, names):
        for name in names:
            for extension in ('.raw', '.csv', '.gsas'):
                left, right = first/(name+extension), second/(name+extension)
                if left.exists() or right.exists():
                    self.assertEqual(left.read_bytes(), right.read_bytes(), name+extension)

    def test_default_and_global_cutoff_remain_exact_in_both_modes(self):
        for mode in ('correct', 'legacy'):
            with self.subTest(mode=mode):
                default = self.run_conversion(mode+'-default', [self.a], '--mode', mode)
                global_one = self.run_conversion(mode+'-global', [self.a], '--mode', mode, '--cutoff', '1')
                input_one = self.run_conversion(mode+'-input', [self.a], '--mode', mode,
                    '--input-cutoff', f'{self.a}=1')
                self.assert_products_equal(default, global_one, ['a'])
                self.assert_products_equal(default, input_one, ['a'])
                for output in (default, global_one, input_one):
                    preparation = self.record(output, 'a')['inputs'][0]['header']['preparation']
                    self.assertEqual(preparation['cutoff_m4_degrees'], 1)

    def test_two_correct_overrides_record_separate_cuts_and_change_low_angle_weight(self):
        baseline = self.run_conversion('baseline', [self.a, self.b], '--cutoff', '1.35', '--combine', 'sum')
        output = self.run_conversion('different', [self.a, self.b], '--cutoff', '1.6', '--combine', 'sum',
            '--input-cutoff', f'{self.a}=1.35', '--input-cutoff', f'{self.b}=1.45')
        record = self.record(output, 'sum')
        for index, (cutoff, removed) in enumerate(((1.35, 1), (1.45, 3))):
            source = record['inputs'][index]
            self.assertEqual(source['header']['preparation']['cutoff_m4_degrees'], cutoff)
            self.assertEqual(source['header']['preparation']['removed_leading_points']['2'], removed)
            self.assertEqual(source['banks'][1]['points'], 8-removed)
            self.assertAlmostEqual(source['banks'][1]['first_angle_deg'], 5+cutoff)
            path = (self.a, self.b)[index]
            self.assertEqual(source['sha256'], hashlib.sha256(self.originals[path]).hexdigest())
        before = np.genfromtxt(baseline/'sum.csv', delimiter=',', names=True)
        after = np.genfromtxt(output/'sum.csv', delimiter=',', names=True)
        np.testing.assert_array_equal(before['two_theta_deg'], after['two_theta_deg'])
        # Identical synthetic observations normally provide two equal-weight
        # contributions here. The later second cutoff leaves one observation.
        index = int(np.argmin(abs(after['two_theta_deg']-6.35)))
        self.assertAlmostEqual(after['two_theta_deg'][index], 6.35, places=8)
        self.assertEqual(before['contributions'][index], 2)
        self.assertEqual(after['contributions'][index], 1)
        self.assertAlmostEqual(after['intensity'][index], before['intensity'][index], places=10)
        self.assertAlmostEqual(after['sigma'][index]/before['sigma'][index], np.sqrt(2), places=10)

    def test_separate_outputs_use_override_and_global_fallback(self):
        output = self.run_conversion('separate', [self.a, self.b], '--cutoff', '1.35',
            '--input-cutoff', f'{self.a}=1.4')
        a_reference = self.run_conversion('a-reference', [self.a], '--cutoff', '1.4')
        b_reference = self.run_conversion('b-reference', [self.b], '--cutoff', '1.35')
        self.assert_products_equal(output, a_reference, ['a'])
        self.assert_products_equal(output, b_reference, ['b'])
        for name, cutoff, points in (('a', 1.4, 6), ('b', 1.35, 7)):
            record = self.record(output, name)
            self.assertEqual(record['inputs'][0]['header']['preparation']['cutoff_m4_degrees'], cutoff)
            self.assertEqual(len(read_raw(output/(name+'.raw')).banks[1].counts), points)

    def test_paths_with_equals_and_relative_or_symlink_spelling_resolve(self):
        directory = self.root/'directory with=equals'
        directory.mkdir()
        source = directory/'nested.bt1'
        source.write_bytes(self.originals[self.a])
        alias = self.root/'alias.bt1'
        alias.symlink_to(source)
        relative = os.path.relpath(alias, Path.cwd())
        output = self.run_conversion('resolved-path', [source], '--input-cutoff', relative+'=1.35')
        self.assertEqual(self.record(output, 'nested')['inputs'][0]['header']['preparation']['cutoff_m4_degrees'], 1.35)
        # Exercise rsplit on the target path itself, as well as alias resolution.
        explicit = self.run_conversion('equals-path', [source], '--input-cutoff', str(source)+'=1.4')
        self.assertEqual(self.record(explicit, 'nested')['inputs'][0]['header']['preparation']['cutoff_m4_degrees'], 1.4)

    def test_malformed_nonfinite_negative_missing_and_unselected_overrides_fail_before_output(self):
        unrelated = self.root/'other.bt1'
        unrelated.write_bytes(self.originals[self.a])
        bad = ['missing-separator', '=1', str(self.a)+'=', str(self.a)+'=text',
               str(self.a)+'=nan', str(self.a)+'=inf', str(self.a)+'=-inf',
               str(self.a)+'=-0.1', str(self.root/'missing.bt1')+'=1', str(unrelated)+'=1']
        for index, specification in enumerate(bad):
            with self.subTest(specification=specification):
                output = self.root/f'bad-{index}'
                code, _, error = self.invoke('convert', self.a, '--out-dir', output,
                    '--input-cutoff', specification)
                self.assertEqual(code, 2, error)
                self.assertIn('--input-cutoff', error)
                self.assertFalse(output.exists())
                self.assert_sources_preserved()

    def test_duplicate_targets_after_resolution_are_rejected(self):
        alias = self.root/'alias.bt1'
        alias.symlink_to(self.a)
        for index, second in enumerate((self.a, alias)):
            with self.subTest(second=second):
                output = self.root/f'duplicate-{index}'
                code, _, error = self.invoke('convert', self.a, '--out-dir', output,
                    '--input-cutoff', f'{self.a}=1.35', '--input-cutoff', f'{second}=1.4')
                self.assertEqual(code, 2, error)
                self.assertIn('Duplicate --input-cutoff', error)
                self.assertFalse(output.exists())
        self.assert_sources_preserved()

    def test_raw_override_is_rejected_while_global_raw_behavior_is_unchanged(self):
        raw = self.root/'prepared.raw'
        write_raw(prepare_bt1(parse_bt1(self.a), cutoff=1), raw)
        original = raw.read_bytes()
        output = self.root/'raw-override'
        code, _, error = self.invoke('convert', raw, '--out-dir', output, '--input-cutoff', f'{raw}=1.35')
        self.assertEqual(code, 2, error)
        self.assertIn('already prepared', error)
        self.assertFalse(output.exists())
        first = self.run_conversion('raw-default', [raw])
        second = self.run_conversion('raw-global', [raw], '--cutoff', '1.35')
        self.assert_products_equal(first, second, ['prepared'])
        self.assertEqual(raw.read_bytes(), original)

    def test_cutoff_removing_all_points_retains_existing_preparation_refusal(self):
        output = self.root/'empty-cut'
        code, _, error = self.invoke('convert', self.a, '--out-dir', output,
            '--input-cutoff', f'{self.a}=2')
        self.assertEqual(code, 2, error)
        self.assertIn('removes every point', error)
        self.assertFalse(output.exists())
        self.assert_sources_preserved()

    def test_legacy_equal_prepared_grids_succeed_and_different_grids_fail_unpublished(self):
        global_cut = self.run_conversion('legacy-global', [self.a, self.b], '--mode', 'legacy',
            '--combine', 'sum', '--cutoff', '1.35')
        equal = self.run_conversion('legacy-equal', [self.a, self.b], '--mode', 'legacy',
            '--combine', 'sum', '--input-cutoff', f'{self.a}=1.35', '--input-cutoff', f'{self.b}=1.35')
        self.assert_products_equal(global_cut, equal, ['a', 'b', 'sum'])
        output = self.root/'legacy-different'
        code, _, error = self.invoke('convert', self.a, self.b, '--out-dir', output,
            '--mode', 'legacy', '--combine', 'sum', '--input-cutoff', f'{self.a}=1.35',
            '--input-cutoff', f'{self.b}=1.45')
        self.assertEqual(code, 2, error)
        self.assertIn('identical per-detector grids', error)
        self.assertIn('per-input cutoffs', error)
        self.assertEqual(list(output.iterdir()), [])
        self.assert_sources_preserved()

    def test_different_numeric_cutoffs_are_allowed_when_legacy_prepared_grids_match(self):
        baseline = self.run_conversion('legacy-base', [self.a, self.b], '--mode', 'legacy', '--combine', 'sum')
        output = self.run_conversion('legacy-no-removals', [self.a, self.b], '--mode', 'legacy', '--combine', 'sum',
            '--input-cutoff', f'{self.a}=0', '--input-cutoff', f'{self.b}=1')
        self.assert_products_equal(baseline, output, ['a', 'b', 'sum'])
        self.assertEqual([source['header']['preparation']['cutoff_m4_degrees']
                          for source in self.record(output, 'sum')['inputs']], [0, 1])

    def test_direct_namespace_without_new_attribute_keeps_default_behavior(self):
        args = argparse.Namespace(inputs=[self.a], out_dir=self.root/'direct', mode='correct', combine=None,
            allow_mixed_temperature=False, allow_partial=False, cutoff=1., keep_detector1=False,
            drop_detector=[], step=None, monitor=None, method='interpolate', uncertainty=None,
            endpoint=None, zero_count=None, gap_policy='uniform', legacy_background=False,
            legacy_rescale=False, covariance=False, no_raw=False, instrument_file=None, overwrite=False)
        cli.convert(args)
        reference = self.run_conversion('namespace-reference', [self.a])
        self.assert_products_equal(args.out_dir, reference, ['a'])
        self.assert_sources_preserved()


if __name__ == '__main__':
    unittest.main()
