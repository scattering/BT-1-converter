"""Analytical tests for the Python implementation of legacy GFORMAT rules.

No compiler, native module, or oracle executable is imported or run.  Native
differential validation lives separately.  The examples below have simple
independent calculations and keep the original input arrays unchanged.
"""
from copy import deepcopy
import importlib
import unittest
from unittest import mock

import numpy as np

from bt1modern.io import Bank, RawDataset
from bt1modern import legacy


def fixture(*, monitor=100., counts=100, points=121, source='controlled.raw'):
    header = {'title': 'Python legacy analytical fixture', 'wavelength': 2.0777,
              'collimation': (60, 20), 'experimental_parameters': (0, 0, 0, 1, 1),
              'monochromator': 'GE311', 'monitor': monitor, 'format': 'raw'}
    banks = [Bank(j + 1, 1.3 + 5*j + .05*np.arange(points),
                  np.full(points, counts, dtype=float), 1., 0.) for j in range(32)]
    return RawDataset(header, banks, source)


def position(pattern, angle):
    index = int(np.argmin(abs(pattern.angles - angle)))
    if abs(pattern.angles[index] - angle) > 1e-9:
        raise AssertionError(f'{angle} is not on the returned grid')
    return index


class LegacyKernelTests(unittest.TestCase):
    def setUp(self):
        # Every reduction in this class runs with external execution and
        # compiler discovery disabled, including the explicit import check.
        for target in ['subprocess.run', 'subprocess.Popen', 'os.system', 'shutil.which']:
            patcher = mock.patch(target, side_effect=AssertionError('Python kernel attempted external execution or compiler discovery'))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict('os.environ', {'PATH': ''})
        patcher.start()
        self.addCleanup(patcher.stop)

    def reduce(self, datasets=None, **options):
        return legacy.reduce_legacy([fixture()] if datasets is None else datasets, **options)

    def test_import_and_reduction_need_no_external_tools(self):
        module = importlib.reload(legacy)
        result = module.reduce_legacy([fixture()])
        self.assertTrue(np.any(result.valid))
        self.assertEqual(result.intensity.dtype, np.dtype('float64'))
        self.assertEqual(result.sigma.dtype, np.dtype('float64'))
        # Returned observations preserve the internal REAL values, not F8.0
        # integer serialization and not an additional float64 recomputation.
        np.testing.assert_array_equal(result.intensity, result.intensity.astype(np.float32).astype(np.float64))
        np.testing.assert_array_equal(result.sigma, result.sigma.astype(np.float32).astype(np.float64))
        self.assertIsNone(result.covariance_factor)

    def test_midpoint_original_sigma_and_corrected_variance(self):
        data = fixture()
        data.banks[0].zero = -.025
        data.banks[0].counts[:2] = [100, 400]
        original = self.reduce([data])
        corrected = self.reduce([data], uncertainty='independent')
        for result in (original, corrected):
            self.assertAlmostEqual(result.angles[0], 1.35)
            self.assertAlmostEqual(result.intensity[0], 250., delta=.002)
            self.assertEqual(result.contributions[0], 1)
        # At r=1/2, original sigma=(10+20)/2=15. Independent variance is
        # (1/2)^2*100+(1/2)^2*400=125, with no integer-output quantization.
        self.assertAlmostEqual(original.sigma[0], 15., delta=.0002)
        self.assertAlmostEqual(corrected.sigma[0], np.sqrt(125.), delta=.0002)
        self.assertNotEqual(corrected.sigma[0], round(corrected.sigma[0]))

    def test_near_grid_snap_keeps_native_small_extrapolation(self):
        data = fixture()
        data.banks[0].zero = -.0001
        data.banks[0].counts[:2] = [100, 400]
        original = self.reduce([data])
        corrected = self.reduce([data], uncertainty='independent')
        # 1.3001 lies inside the 0.00011-degree snap window. The target is
        # 1.30, so r=-.002 and I=100-.002*(400-100)=99.4.
        for result in (original, corrected):
            self.assertAlmostEqual(result.angles[0], 1.3)
            self.assertAlmostEqual(result.intensity[0], 99.4, delta=.002)
        self.assertAlmostEqual(original.sigma[0], 9.98, delta=.0003)
        self.assertAlmostEqual(corrected.sigma[0], np.sqrt(1.002**2*100 + .002**2*400), delta=.0003)

    def test_outside_snap_window_uses_next_grid_point(self):
        data = fixture()
        data.banks[0].zero = -.00012
        data.banks[0].counts[:2] = [100, 400]
        result = self.reduce([data])
        self.assertAlmostEqual(result.angles[0], 1.35)
        self.assertAlmostEqual(result.intensity[0], 100 + (1 - .00012/.05)*300, delta=.002)

    def test_masked_next_endpoint_blocks_even_zero_ratio(self):
        data = fixture()
        data.banks[0].counts[10] = -1
        result = self.reduce([data])
        # The observed sample at 1.75 is positive and exactly on-grid, but
        # legacy interpolation rejects its interval because 1.80 is masked.
        for angle in [1.75, 1.80]:
            index = position(result, angle)
            self.assertFalse(result.valid[index])
            self.assertEqual(result.contributions[index], 0)
            self.assertEqual(result.intensity[index], 0.)
            self.assertEqual(result.sigma[index], 0.)
        for angle in [1.70, 1.85]:
            index = position(result, angle)
            self.assertTrue(result.valid[index])
            self.assertEqual(result.contributions[index], 1)

    def test_nearest_masks_only_current_sample(self):
        data = fixture()
        data.banks[0].counts[10] = -1
        result = self.reduce([data], method='nearest')
        self.assertTrue(result.valid[position(result, 1.75)])
        self.assertFalse(result.valid[position(result, 1.80)])
        self.assertTrue(result.valid[position(result, 1.85)])

    def test_zero_count_sigma_one_is_applied_after_scale_and_monitor(self):
        first = fixture(monitor=100., counts=-1)
        second = fixture(monitor=200., counts=0, source='second.raw')
        first.banks[0].scale = second.banks[0].scale = 3.
        # The first scan establishes the monitor but is masked. The second
        # has multiplier 3*(100/200)=1.5; legacy zero-count sigma is still 1.
        result = self.reduce([first, second])
        self.assertEqual(result.intensity[0], 0.)
        self.assertEqual(result.sigma[0], 1.)
        self.assertTrue(result.valid[0])
        self.assertEqual(result.contributions[0], 1)

    def test_unequal_monitors_and_two_stage_overlap_weighting(self):
        first = fixture(monitor=100., counts=100)
        second = fixture(monitor=200., counts=400, source='second.raw')
        first.banks[1].counts[:] = 400
        second.banks[1].counts[:] = 1600
        result = self.reduce([first, second])
        # Detector 1: normalized observations 100+-10 and 200+-10 give
        # I=150,V=50. Detector 2: 400+-20 and 800+-20 give I=600,V=200.
        low = position(result, 1.35)
        self.assertAlmostEqual(result.intensity[low], 150., delta=.001)
        self.assertAlmostEqual(result.sigma[low]**2, 50., delta=.001)
        self.assertEqual(result.contributions[low], 2)
        # In their overlap, (150/50+600/200)/(1/50+1/200)=240, V=40.
        overlap = position(result, 6.35)
        self.assertAlmostEqual(result.intensity[overlap], 240., delta=.002)
        self.assertAlmostEqual(result.sigma[overlap]**2, 40., delta=.002)
        self.assertEqual(result.contributions[overlap], 4)

    def test_contributions_count_only_accepted_file_bank_estimates(self):
        first = fixture()
        second = fixture(source='masked-second.raw')
        second.banks[1].counts[:] = -1
        result = self.reduce([first, second])
        self.assertEqual(result.contributions[position(result, 1.35)], 2)
        self.assertEqual(result.contributions[position(result, 6.35)], 3)
        self.assertEqual(result.contributions[position(result, 8.00)], 1)
        self.assertTrue(np.issubdtype(result.contributions.dtype, np.integer))
        self.assertIsNone(result.covariance_factor)

    def test_nearest_is_identical_for_both_uncertainty_modes(self):
        data = fixture()
        data.banks[0].zero = -.025
        data.banks[0].counts = 100 + np.arange(121)**2
        original = self.reduce([data], method='nearest', uncertainty='legacy-linear')
        corrected = self.reduce([data], method='nearest', uncertainty='independent')
        for field in ('angles', 'intensity', 'sigma', 'valid', 'contributions'):
            np.testing.assert_array_equal(getattr(original, field), getattr(corrected, field))

    def test_interpolation_excludes_final_interval_endpoint_nearest_keeps_it(self):
        data = fixture()
        data.banks[-1].counts[-1] = 400
        interpolated = self.reduce([data])
        nearest = self.reduce([data], method='nearest')
        self.assertAlmostEqual(interpolated.angles[-1], 162.25)
        self.assertAlmostEqual(nearest.angles[-1], 162.3)
        self.assertEqual(len(nearest.angles), len(interpolated.angles) + 1)
        self.assertAlmostEqual(nearest.intensity[-1], 400., delta=.001)
        self.assertAlmostEqual(nearest.sigma[-1], 20., delta=.001)
        np.testing.assert_allclose(np.diff(interpolated.angles), .05, rtol=0, atol=1e-12)

    def test_kernel_does_not_mutate_input_metadata_arrays_or_calibration(self):
        data = fixture()
        data.banks[0].zero = -.025
        before = deepcopy(data)
        self.reduce([data], uncertainty='independent')
        self.assertEqual(data.header, before.header)
        self.assertEqual(data.source, before.source)
        for actual, expected in zip(data.banks, before.banks):
            np.testing.assert_array_equal(actual.angles, expected.angles)
            np.testing.assert_array_equal(actual.counts, expected.counts)
            self.assertEqual((actual.detector, actual.scale, actual.zero),
                             (expected.detector, expected.scale, expected.zero))

    def test_rejects_invalid_options_and_scan_count(self):
        for options in [{'uncertainty': 'reciprocal'}, {'method': 'cubic'},
                        {'background': 'false'}, {'rescale': 1}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.reduce(**options)
        for datasets in [[], [fixture()]*51]:
            with self.subTest(count=len(datasets)), self.assertRaises(ValueError):
                self.reduce(datasets)

    def test_rejects_missing_or_unordered_banks(self):
        for count, reverse in [(31, False), (32, True)]:
            data = fixture()
            data.banks = data.banks[:count]
            if reverse:
                data.banks.reverse()
            with self.subTest(count=count, reverse=reverse), self.assertRaises(ValueError):
                self.reduce([data])

    def test_rejects_invalid_sample_count(self):
        for points in [1, 2001]:
            with self.subTest(points=points), self.assertRaises(ValueError):
                self.reduce([fixture(points=points)])

    def test_rejects_irregular_steps_and_nonincreasing_origins(self):
        for change in ['irregular', 'different-step', 'origin']:
            data = fixture()
            if change == 'irregular':
                data.banks[2].angles[19] += .01
            elif change == 'different-step':
                data.banks[2].angles = 11.3 + .1*np.arange(121)
            else:
                data.banks[1].zero = 5.
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.reduce([data])

    def test_rejects_combined_geometry_calibration_and_acquisition_mismatch(self):
        for change in ['angle', 'zero', 'scale', 'wavelength', 'collimation']:
            first, second = fixture(), fixture(source='second.raw')
            if change == 'angle':
                second.banks[4].angles += .05
            elif change == 'zero':
                second.banks[4].zero = .001
            elif change == 'scale':
                second.banks[4].scale = 1.01
            elif change == 'wavelength':
                second.header['wavelength'] = 1.5403
            else:
                second.header['collimation'] = (15, 20)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.reduce([first, second])

    def test_rejects_nonfinite_and_nonphysical_numeric_inputs(self):
        for field, value in [('counts', np.nan), ('counts', np.inf), ('counts', .5),
                             ('angles', np.nan), ('angles', np.inf),
                             ('scale', 0.), ('scale', -1.), ('scale', np.inf),
                             ('zero', np.nan), ('monitor', 0.), ('monitor', np.inf),
                             ('monitor', 1e40)]:
            data = fixture()
            if field == 'monitor':
                data.header['monitor'] = value
            elif field in ('counts', 'angles'):
                getattr(data.banks[0], field)[0] = value
            else:
                setattr(data.banks[0], field, value)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.reduce([data])

    def test_rejects_legacy_header_step_truncation(self):
        data = fixture()
        for index, bank in enumerate(data.banks):
            bank.angles = 1.3 + 5*index + .53*np.arange(121)
        # REAL .53*100 falls below 53; INT serialization would label the data
        # with .52 degrees. The Python kernel must reject that mislabeled grid.
        with self.assertRaisesRegex(ValueError, 'header step'):
            self.reduce([data])


if __name__ == '__main__':
    unittest.main()
