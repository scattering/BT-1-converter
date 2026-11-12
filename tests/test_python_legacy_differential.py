"""Exact differential tests: Python compatibility mode vs independent Fortran.

The compiler is used ONLY by this test oracle. Set BT1_AUDIT_ROOT to the pinned
independent archive and BT1_LEGACY_FIXTURE_DIR for the historical acquisition.
Runtime bt1modern imports neither this module nor any native backend.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest
import numpy as np
from validate_python_legacy import (build_oracle,compare_python_reduction,
    compare_python_preparation,oracle_prepare,synthetic_raw,precision_raw,
    implied_calibration_raw,declared_gsas)


class PythonLegacyDifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler=os.environ.get('FC','gfortran')
        if shutil.which(compiler) is None:raise unittest.SkipTest('Fortran compiler unavailable for independent test oracle')
        cls.audit=Path(os.environ.get('BT1_AUDIT_ROOT',str(Path(__file__).resolve().parents[2]/'bt1_audit')))
        if not (cls.audit/'official-downloads/source/gformat.f').exists():
            raise unittest.SkipTest('Set BT1_AUDIT_ROOT to independent pinned audit sources')
        cls.temp=tempfile.TemporaryDirectory(prefix='bt1-python-legacy-differential-')
        cls.addClassCleanup(cls.temp.cleanup);cls.root=Path(cls.temp.name)
        cls.oracle=build_oracle(cls.audit,cls.root/'oracle',compiler=compiler);cls.counter=0

    def case_dir(self,label):
        type(self).counter+=1
        return self.root/f'{self.counter:03d}-{label}'

    def synthetic(self,variant,**kwargs):
        return synthetic_raw(self.case_dir(variant).with_suffix('.raw'),variant=variant,**kwargs)

    def compare(self,raw,label,**kwargs):
        folder=self.case_dir(label)
        record,pattern=compare_python_reduction(raw,folder,self.oracle,**kwargs)
        self.assertTrue(record['passed'],f"Exact mismatch: {record['first_mismatches']} ({folder})")
        return pattern,declared_gsas((folder/'python.gsas').read_bytes())

    def test_quarter_curvature_both_uncertainties_and_nearest(self):
        raw=self.synthetic('quarter');outputs={}
        for uncertainty in ('legacy-linear','independent'):
            for method in ('interpolate','nearest'):
                with self.subTest(uncertainty=uncertainty,method=method):
                    _,output=self.compare([raw],'quarter',uncertainty=uncertainty,method=method,background=False,rescale=False)
                    outputs[uncertainty,method]=output['pairs']
        if len(outputs)==4:
            self.assertFalse(np.array_equal(outputs['legacy-linear','interpolate'],outputs['independent','interpolate']))
            np.testing.assert_array_equal(outputs['legacy-linear','nearest'],outputs['independent','nearest'])

    def test_threshold_boundary_and_interior_rules(self):
        raw=self.synthetic('threshold')
        for uncertainty in ('legacy-linear','independent'):
            with self.subTest(uncertainty=uncertainty):
                self.compare([raw],'threshold',uncertainty=uncertainty,method='interpolate',background=False,rescale=False)

    def test_masks_and_zero_count_preserve_historical_interval_semantics(self):
        raw=self.synthetic('mask-zero')
        for uncertainty in ('legacy-linear','independent'):
            with self.subTest(uncertainty=uncertainty):
                _,data=self.compare([raw],'masks',uncertainty=uncertainty,method='interpolate',background=False,rescale=False)
                j=int(np.argmin(abs(data['angles']-1.75)))
                # Positive aligned sample before a negative sample is removed
                # by the original both-endpoint mask, even at exact r=0.
                np.testing.assert_array_equal(data['pairs'][j],[0,0])
                j=int(np.argmin(abs(data['angles']-2.3)))
                np.testing.assert_array_equal(data['pairs'][j],[0,1])

    def test_background_and_rescale_flags_are_independent(self):
        raw=self.synthetic('background');outputs={}
        for background in (False,True):
            for rescale in (False,True):
                with self.subTest(background=background,rescale=rescale):
                    _,data=self.compare([raw],'flags',uncertainty='independent',method='interpolate',background=background,rescale=rescale)
                    outputs[background,rescale]=data['pairs']
        if len(outputs)==4:
            self.assertFalse(np.array_equal(outputs[False,False],outputs[True,False]))
            self.assertFalse(np.array_equal(outputs[False,False],outputs[False,True]))

    def test_combination_unequal_monitors_and_scales(self):
        a=self.synthetic('quarter',monitor=137,count_multiplier=1)
        b=self.synthetic('quarter',monitor=419,count_multiplier=3)
        for uncertainty in ('legacy-linear','independent'):
            with self.subTest(uncertainty=uncertainty):
                self.compare([a,b],'combine',uncertainty=uncertainty,method='interpolate',background=False,rescale=False)

    def test_half_bin_nearest_and_endpoint_rules(self):
        raw=self.synthetic('half')
        for method in ('nearest','interpolate'):
            self.compare([raw],'half',uncertainty='independent',method=method,background=False,rescale=False)

    def test_float32_monitor_precision_scaling_and_final_partial_records(self):
        first=precision_raw(self.case_dir('precision1').with_suffix('.raw'),monitor=16777217)
        second=precision_raw(self.case_dir('precision2').with_suffix('.raw'),monitor=33554435,multiplier=2)
        for uncertainty in ('legacy-linear','independent'):
            for method in ('interpolate','nearest'):
                for raws in ([first],[first,second]):
                    with self.subTest(uncertainty=uncertainty,method=method,files=len(raws)):
                        _,data=self.compare(raws,'precision',uncertainty=uncertainty,method=method,background=False,rescale=False)
                        self.assertNotEqual(data['count']%5,0)

    def test_g14_1_monitor_implied_decimal_input(self):
        # G14.1 reads no-dot integer tokens with one implied fractional digit.
        # Its REAL rounding can change monitor ratios even when both values
        # share the same factor ten; treating tokens as ordinary Python floats
        # caused many one-count output mismatches in this regression.
        first=precision_raw(self.case_dir('implied1').with_suffix('.raw'),monitor=16777217,implied_decimal=True)
        second=precision_raw(self.case_dir('implied2').with_suffix('.raw'),monitor=33554435,multiplier=2,implied_decimal=True)
        for uncertainty in ('legacy-linear','independent'):
            for method in ('interpolate','nearest'):
                with self.subTest(uncertainty=uncertainty,method=method):
                    self.compare([first,second],'implied-monitor',uncertainty=uncertainty,method=method,background=False,rescale=False)

    def test_f8_4_calibration_implied_decimal_input(self):
        raw=implied_calibration_raw(self.case_dir('implied-calibration').with_suffix('.raw'))
        for uncertainty in ('legacy-linear','independent'):
            for method in ('interpolate','nearest'):
                with self.subTest(uncertainty=uncertainty,method=method):
                    self.compare([raw],'implied-calibration',uncertainty=uncertainty,method=method,background=False,rescale=False)

    def test_f80_signed_half_ties_against_independent_compiler(self):
        # A direct format oracle isolates decimal ties from interpolation and
        # weighting. Compare the actual 80-column F8.0 fields, including -0.
        from types import SimpleNamespace
        from bt1modern.gsas import write_legacy_gsas
        folder=self.case_dir('f80');folder.mkdir()
        source=folder/'f80.f90'
        source.write_text('''program rounding
implicit none
real :: values(10) = [-2.5,-1.5,-0.5,0.5,1.5,2.5,3.5,4.5,5.5,6.5]
integer :: i,j
do i=1,10,5
 write(*,'(5(F8.0,F8.0))') (values(j),2.5,j=i,i+4)
end do
end program rounding
''')
        binary=folder/'f80'
        subprocess.run([self.oracle.metadata['compiler'],'-O0','-o',str(binary),str(source)],check=True,capture_output=True)
        expected=subprocess.run([str(binary)],check=True,capture_output=True,text=True).stdout.splitlines()
        values=np.arange(-2.5,7.5,dtype=float)
        pattern=SimpleNamespace(angles=1.+.05*np.arange(10),intensity=values,sigma=np.full(10,2.5),
            valid=np.ones(10,dtype=bool),contributions=np.ones(10,dtype=int),
            metadata={'legacy_tstart_deg':1.,'legacy_step_deg':float(np.float32(.05)),
                      'legacy_nominal_input_step_deg':.05})
        write_legacy_gsas(pattern,folder/'python.gsas')
        lines=(folder/'python.gsas').read_text().splitlines()
        bank=next(i for i,line in enumerate(lines) if line.startswith('BANK'))
        self.assertEqual(lines[bank+1:bank+3],expected)

    def test_real_completed_scans_and_non_grid_preparation(self):
        raws=[]
        for scan in (1,2):
            source=self.audit/'retrieval'/f'Mn5Si00{scan}.bt1'
            self.assertTrue(source.exists(),f'Missing archived fixture {source}')
            raw=oracle_prepare(source,self.case_dir('oracle-fixture'),self.oracle,cutoff=1.);raws.append(raw)
            for opts in ({'cutoff':1.},{'cutoff':2.334,'drop_detectors':(2,10),'keep_detector1':True}):
                record,_=compare_python_preparation(source,self.case_dir('python-prep'),self.oracle,**opts)
                self.assertTrue(record['passed'],str(record))
            for uncertainty in ('legacy-linear','independent'):
                self.compare([raw],'real',uncertainty=uncertainty,method='interpolate',background=False,rescale=False)
        self.compare(raws,'real-combined',uncertainty='independent',method='interpolate',background=False,rescale=False)

    def test_historical_fixture_when_explicitly_supplied(self):
        fixture=os.environ.get('BT1_LEGACY_FIXTURE_DIR')
        if not fixture:self.skipTest('Set BT1_LEGACY_FIXTURE_DIR for historical acquisition')
        source=Path(fixture)/'test.bt1'
        raw=oracle_prepare(source,self.case_dir('oracle-history'),self.oracle,cutoff=1.)
        record,_=compare_python_preparation(source,self.case_dir('python-history'),self.oracle,cutoff=1.)
        self.assertTrue(record['passed'],str(record))
        for uncertainty in ('legacy-linear','independent'):
            self.compare([raw],'historical',uncertainty=uncertainty,method='interpolate',background=False,rescale=False)


if __name__=='__main__':unittest.main()
