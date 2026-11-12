"""CLI preservation and normalization tests using small standalone RAW fixtures."""
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
from bt1modern.io import Bank, RawDataset, read_raw, write_raw


def raw_dataset(path, counts=(100,100), monitor=100):
    header={'title':'Controlled test', 'wavelength':2.0782, 'collimation':(15,20),
            'experimental_parameters':(0,0,0,1,1),'monochromator':'GE311',
            'monitor':monitor,'date':'1-Nov-2026','format':'raw'}
    return RawDataset(header,[Bank(1,np.array([1.,1.05]),np.array(counts),1.,0.)],str(path))


class CLISafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='bt1-cli-test-')
        self.root=Path(self.temp.name)
        self.a=self.root/'a.raw'
        self.b=self.root/'b.raw'
        self.out=self.root/'out'
        write_raw(raw_dataset(self.a),self.a)
        write_raw(raw_dataset(self.b,(400,400),200),self.b)

    def tearDown(self):self.temp.cleanup()

    def invoke(self,*args):
        stdout,stderr=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            code=cli.main(list(map(str,args)))
        return code,stdout.getvalue(),stderr.getvalue()

    def test_normal_conversion_preserves_source_and_roundtrips_metadata(self):
        before=self.a.read_bytes()
        code,stdout,stderr=self.invoke('convert',self.a,'--out-dir',self.out,'--covariance')
        self.assertEqual(code,0,stderr)
        self.assertEqual(before,self.a.read_bytes())
        record=json.loads((self.out/'a.json').read_text())
        self.assertEqual(record['inputs'][0]['sha256'],hashlib.sha256(before).hexdigest())
        self.assertEqual(record['reduction']['monitor'],100)
        with np.load(self.out/'a.covariance.npz',allow_pickle=False) as factor:
            metadata=json.loads(str(factor['metadata_json']))
            self.assertIn('after preparation',metadata['column_index_definition'])
            self.assertIn('weights held fixed',metadata['statistical_interpretation'])

    def test_duplicate_path_and_hardlinked_alias_are_rejected(self):
        alias=self.root/'alias.raw';os.link(self.a,alias)
        for second in (self.a,alias):
            code,_,stderr=self.invoke('convert',self.a,second,'--out-dir',self.out,'--combine','sum')
            self.assertEqual(code,2,stderr)
        self.assertFalse(self.out.exists())

    def test_overwrite_cannot_replace_source_alias(self):
        self.out.mkdir()
        target=self.out/'a.gsas'
        before=self.a.read_bytes()
        os.link(self.a,target)
        code,_,stderr=self.invoke('convert',self.a,'--out-dir',self.out,'--overwrite')
        self.assertEqual(code,2,stderr)
        self.assertIn('input file',stderr)
        self.assertEqual(before,self.a.read_bytes())
        target.unlink();target.symlink_to(self.a)
        code,_,stderr=self.invoke('convert',self.a,'--out-dir',self.out,'--overwrite')
        self.assertEqual(code,2,stderr)
        self.assertEqual(before,self.a.read_bytes())

    def test_combination_is_explicit_and_normalizes_to_first_monitor(self):
        code,_,stderr=self.invoke('convert',self.a,self.b,'--out-dir',self.out)
        self.assertEqual(code,0,stderr)
        a=np.genfromtxt(self.out/'a.csv',delimiter=',',names=True)
        b=np.genfromtxt(self.out/'b.csv',delimiter=',',names=True)
        np.testing.assert_allclose(a['intensity'],100)
        np.testing.assert_allclose(b['intensity'],400)
        together=self.root/'together'
        code,_,stderr=self.invoke('convert',self.a,self.b,'--out-dir',together,'--combine','sum')
        self.assertEqual(code,0,stderr)
        result=np.genfromtxt(together/'sum.csv',delimiter=',',names=True)
        np.testing.assert_allclose(result['intensity'],150)
        np.testing.assert_allclose(result['sigma'],np.sqrt(50))
        record=json.loads((together/'sum.json').read_text())
        self.assertEqual(len(record['inputs']),2)
        self.assertEqual(record['reduction']['input_monitors'],[100,200])

    def test_nonmonitor_counting_mode_is_rejected(self):
        source=self.root/'timed.bt1';source.write_text('stub, replaced by controlled reader')
        controlled=raw_dataset(source);controlled.header.update(format='bt1',count_type='TIME')
        with mock.patch('bt1modern.cli._read',return_value=controlled):
            code,_,stderr=self.invoke('convert',source,'--out-dir',self.out)
        self.assertEqual(code,2,stderr)
        self.assertIn('NEUT',stderr)
        self.assertFalse(self.out.exists())

    def test_source_change_during_read_is_detected_before_publication(self):
        original_read=cli._read
        def changed_after_read(path,allow_partial=False):
            result=original_read(path,allow_partial)
            write_raw(raw_dataset(path,(900,900)),path,overwrite=True)
            return result
        with mock.patch('bt1modern.cli._read',side_effect=changed_after_read):
            code,_,stderr=self.invoke('convert',self.a,'--out-dir',self.out)
        self.assertEqual(code,2,stderr)
        self.assertFalse((self.out/'a.gsas').exists())


if __name__=='__main__':unittest.main()
