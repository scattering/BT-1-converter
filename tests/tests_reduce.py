"""Independent analytic regression cases for reduction, not source mirroring."""
import unittest
from types import SimpleNamespace
import numpy as np
from bt1modern.reduce import reduce_datasets, covariance_entry


def dataset(x, y, *, scale=1., zero=0., monitor=100., source='test', detector=1):
    bank = SimpleNamespace(detector=detector, angles=np.array(x, dtype=float),
                           counts=np.array(y, dtype=float), scale=scale, zero=zero)
    return SimpleNamespace(header={'monitor': monitor}, banks=[bank], source=source)


class ReductionTests(unittest.TestCase):
    def test_correct_interpolation_and_all_exact_endpoints(self):
        p = reduce_datasets([dataset([0,1,2,3], [100,400,900,1600])], output_step=.5)
        np.testing.assert_allclose(p.intensity, [100,250,400,650,900,1250,1600])
        np.testing.assert_allclose(p.sigma**2, [100,125,400,325,900,625,1600])
        self.assertTrue(p.valid.all())

    def test_asymmetric_quarter_step_variance(self):
        # Independent hand calculation at r=1/4: I=175; V=(3/4)^2*100+(1/4)^2*400.
        p = reduce_datasets([dataset([0,1], [100,400])], output_step=.25)
        self.assertAlmostEqual(p.intensity[1], 175.)
        self.assertAlmostEqual(p.sigma[1]**2, 81.25)
        self.assertAlmostEqual(p.sigma[1], 9.013878188659973)

    def test_actual_spacing_irregular_input(self):
        p = reduce_datasets([dataset([0,1,3], [100,400,900])], output_step=1)
        np.testing.assert_allclose(p.intensity, [100,400,650,900])
        np.testing.assert_allclose(p.sigma**2, [100,400,325,900])

    def test_no_extrapolation_and_independent_bank_masks(self):
        a = dataset([.25,1.25], [100,400])
        b = dataset([0,1,2], [100,100,100], source='b', detector=2)
        p = reduce_datasets([a,b], output_step=.5)
        np.testing.assert_equal(p.contributions, [1,2,2,1,1])
        self.assertAlmostEqual(p.intensity[-1], 100.)

    def test_negative_and_nonfinite_counts_block_adjacent_interpolation(self):
        for bad in (-1., np.nan, np.inf):
            p = reduce_datasets([dataset([0,1,2], [100,bad,400])], output_step=.5)
            np.testing.assert_equal(p.valid, [True,False,False,False,True])
            self.assertTrue(np.isnan(p.intensity[1:4]).all())

    def test_detector_and_monitor_scaling(self):
        p = reduce_datasets([dataset([0,1], [100,400], scale=2.5, monitor=50)],
                            monitor=100, output_step=1)
        np.testing.assert_allclose(p.intensity, [500,2000])
        np.testing.assert_allclose(p.sigma, [50,100])

    def test_zero_count_conventions(self):
        d = dataset([0,1], [0,4], scale=3)
        default = reduce_datasets([d], output_step=1)
        compat = reduce_datasets([d], output_step=1, zero_count='fortran')
        excluded = reduce_datasets([d], output_step=1, zero_count='exclude')
        self.assertEqual(default.sigma[0], 3.)
        self.assertEqual(compat.sigma[0], 1.)
        self.assertFalse(excluded.valid[0])

    def test_one_file_combination_does_not_renormalize_last_cell(self):
        p = reduce_datasets([dataset([0,1], [100,400])], output_step=1)
        np.testing.assert_allclose(p.intensity, [100,400])
        np.testing.assert_allclose(p.sigma, [10,20])

    def test_two_normalized_measurements_inverse_variance(self):
        a = dataset([0,1], [100,100], monitor=100)
        b = dataset([0,1], [400,400], monitor=200, source='b')
        p = reduce_datasets([a,b], output_step=1)
        # Both normalized variances are 100, while intensities are 100 and 200.
        np.testing.assert_allclose(p.intensity, [150,150])
        np.testing.assert_allclose(p.sigma**2, [50,50])

    def test_covariance_tracks_shared_raw_counts(self):
        p = reduce_datasets([dataset([0,1,2], [100,400,900])], output_step=.5, covariance=True)
        f = p.covariance_factor
        for i in range(len(p.angles)):
            self.assertAlmostEqual(covariance_entry(f,i,i), p.sigma[i]**2)
        self.assertAlmostEqual(covariance_entry(f,1,3), .25*400)
        self.assertAlmostEqual(covariance_entry(f,0,4), 0.)
        self.assertAlmostEqual(covariance_entry(f,0,1), .5*100)

    def test_fortran_endpoint_policy_only_prunes_aligned_last(self):
        p = reduce_datasets([dataset([0,1,2], [100,400,900])], output_step=.5, endpoint='fortran')
        np.testing.assert_equal(p.valid, [True,True,True,True,False])
        p = reduce_datasets([dataset([.2,1.2,2.2], [100,400,900])], output_step=.5, endpoint='fortran')
        self.assertTrue(p.valid.all())

    def test_legacy_linear_sigma_is_explicit(self):
        p = reduce_datasets([dataset([0,1], [100,400])], output_step=.5, uncertainty='legacy-linear')
        self.assertAlmostEqual(p.sigma[1], 15.)
        with self.assertRaises(ValueError):
            reduce_datasets([dataset([0,1], [100,400])], covariance=True, uncertainty='legacy-linear')

    def test_nearest_half_away_rounding_and_zero_correction(self):
        p = reduce_datasets([dataset([.6,1.6], [100,400], zero=.1)], output_step=1, method='nearest')
        np.testing.assert_allclose(p.angles, [1,2])
        np.testing.assert_allclose(p.intensity, [100,400])
        np.testing.assert_allclose(p.sigma, [10,20])

    def test_mixed_step_default_is_smallest_but_each_bracket_is_actual(self):
        a = dataset([0,2], [100,900])
        b = dataset([0,1,2], [-1,-1,-1], source='masked')
        p = reduce_datasets([a,b])
        np.testing.assert_allclose(p.angles, [0,1,2])
        self.assertAlmostEqual(p.intensity[1], 500.)
        self.assertAlmostEqual(p.sigma[1]**2, 250.)

    def test_inputs_not_mutated_and_bad_angles_rejected(self):
        d = dataset([0,1], [100,400]); before=d.banks[0].counts.copy()
        reduce_datasets([d])
        np.testing.assert_array_equal(d.banks[0].counts,before)
        for angles in ([1,0], [0,0], [0,np.nan]):
            with self.assertRaises(ValueError):
                reduce_datasets([dataset(angles,[100,400])])

    def test_incompatible_instrument_headers_rejected(self):
        a=dataset([0,1],[100,100]); b=dataset([0,1],[100,100],source='b')
        a.header['wavelength']=2.; b.header['wavelength']=1.5
        with self.assertRaises(ValueError):reduce_datasets([a,b])
        del a.header['wavelength']; del b.header['wavelength']
        a.header['collimation']=(15,20); b.header['collimation']=(60,20)
        with self.assertRaises(ValueError):reduce_datasets([a,b])

    def test_accumulated_weights_overflow_is_rejected(self):
        # Each individual variance is 1e-308 and each weight is finite, but
        # adding two approximately 1e308 weights exceeds float64 capacity.
        a=dataset([0,1],[1,1],scale=1e-154)
        b=dataset([0,1],[1,1],scale=1e-154,source='b')
        single=reduce_datasets([a])
        self.assertTrue(np.isfinite(single.sigma).all())
        with self.assertRaisesRegex(ValueError,'Accumulated.*overflowed'):
            reduce_datasets([a,b])


if __name__ == '__main__':
    unittest.main()
