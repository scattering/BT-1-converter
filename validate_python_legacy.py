"""Independent Fortran-oracle tests for the two Python reduction modes.

This is a TEST HARNESS, not a runtime dependency of bt1modern. Only the oracle
uses a compiler. Python preparation/reduction/writing are called directly; no
native backend module or bundled Fortran source is imported. Oracle helpers
were copied from the preceding independent validator to remain self-contained.

Exact declared GSAS numeric equality is required. Half-unit residuals are NOT
accepted as equivalent: serialization ties and float32 ordering issues are
reported explicitly. Numerical compatibility is not scientific correctness.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import numpy as np
from bt1modern.io import Bank, RawDataset, parse_bt1, read_raw, write_raw

PINNED_SOURCES = {
    'proprep': ('official-downloads/source/proprep.f', '25edddf9e3b43c6b2a71f0b6fd25039d8e9d62ab484977304102653bf4ffc72f'),
    'legacy-linear': ('official-downloads/source/gformat.f', '4b449fe4b9e07959863f7c26e665bc1ee38c97c43f25cfc4d61e7c5bc006f7b3'),
    'independent': ('gformat-corrected-experiment.f', '98693d63574493f8f1b6a5a4abec1ea3e766915e543d4c897b578646a26c048b'),
}

def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else Path(value).read_bytes()).hexdigest()

def json_default(value):
    if isinstance(value, np.ndarray): return value.tolist()
    if isinstance(value, np.generic): return value.item()
    if isinstance(value, Path): return str(value)
    raise TypeError(type(value).__name__)

def direct_run(executable, arguments, directory):
    result = subprocess.run([str(executable), *arguments], cwd=directory,
                            capture_output=True, text=True, timeout=60)
    log = result.stdout + result.stderr
    if result.returncode:
        raise RuntimeError(f'Oracle exit {result.returncode}: {executable}\n{log[-3000:]}')
    return log

@dataclass
class Oracle:
    executables: dict
    metadata: dict

def build_oracle(audit_root, output, compiler='gfortran'):
    """Compile the independently archived source versions, with pinned hashes."""
    audit_root, output = Path(audit_root), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    binary = shutil.which(compiler)
    if binary is None: raise FileNotFoundError(f'Compiler unavailable: {compiler}')
    version = subprocess.run([binary, '--version'], capture_output=True, text=True, check=True).stdout
    executables, records = {}, {}
    flags = ['-std=legacy', '-ffixed-line-length-none', '-O0', '-g', '-fcheck=bounds']
    for key, (relative, expected_hash) in PINNED_SOURCES.items():
        original = audit_root / relative
        actual_hash = digest(original)
        if actual_hash != expected_hash:
            raise ValueError(f'Oracle source hash mismatch for {original}: {actual_hash}')
        source = output / (key + '.f')
        source.write_bytes(original.read_bytes())
        executable = output / key
        command = [binary, *flags, '-o', str(executable), str(source)]
        cp = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (output / (key + '.compile.log')).write_text(cp.stdout + cp.stderr)
        if cp.returncode: raise RuntimeError(f'Oracle compile failed: {key}\n{cp.stderr}')
        executables[key] = executable
        records[key] = {'original_source': str(original.resolve()), 'source_sha256': actual_hash,
                        'command': command, 'executable': str(executable), 'executable_sha256': digest(executable)}
    return Oracle(executables, {'compiler': binary, 'compiler_version': version, 'sources': records,
                               'independence': 'Separately archived pinned source files; direct subprocess calls and independent GSAS parser.'})

def declared_gsas(raw):
    """Independent narrow parser of native one-BANK CONST ESD output.

    Compare ONLY declared NCH samples. Fortran writes complete five-pair records
    with extra trailing padding pairs; titles, staging filenames, CRLF and
    instrument-reference lines are tracked but are not numerical evidence.
    """
    lines = raw.decode('ascii').splitlines()
    positions = [i for i, line in enumerate(lines) if line.startswith('BANK')]
    if len(positions) != 1: raise AssertionError('Expected one native GSAS BANK')
    index = positions[0]; tokens = lines[index].split()
    if tokens[4] != 'CONST' or tokens[-1] != 'ESD': raise AssertionError('Expected native CONST ESD')
    bank, count, records = map(int, tokens[1:4]); start, step = float(tokens[5])/100, float(tokens[6])/100
    if bank != 1 or count <= 0 or records != (count+4)//5 or step <= 0: raise AssertionError('Invalid native BANK header')
    values = []
    for row in lines[index+1:index+1+records]:
        row = row.ljust(80)
        for col in range(0, 80, 16):
            if not row[col:col+16].strip(): continue
            values.append((float(row[col:col+8]), float(row[col+8:col+16])))
    if len(values) < count: raise AssertionError('Truncated native GSAS data')
    pairs = np.asarray(values[:count], dtype=float)
    if not np.all(np.isfinite(pairs)) or np.any(pairs[:,1] < 0): raise AssertionError('Nonfinite or negative native ESD')
    return {'angles': start + step*np.arange(count), 'pairs': pairs, 'count': count,
            'records': records, 'start': start, 'step': step, 'padding_pairs': len(values)-count,
            'title': lines[0], 'preamble': lines[:index]}

def oracle_reduce(raw_inputs, directory, oracle, *, uncertainty='independent',
                  method='interpolate', background=False, rescale=False):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=False)
    stems = []
    for n, raw in enumerate(raw_inputs):
        # Legacy source slices arg(ln-3:ln) before checking the extension;
        # basenames shorter than four characters violate its string bounds.
        stem = f'oracle{n+1:02d}'; stems.append(stem)
        (directory/(stem+'.raw')).write_bytes(raw)
    flags = ''
    if len(raw_inputs)>1: flags += 's'
    if method == 'nearest': flags += 'm'
    if not background: flags += 'o'
    if not rescale: flags += 'r'
    args = (['-'+flags] if flags else []) + stems
    log = direct_run(oracle.executables[uncertainty], args, directory)
    (directory/'oracle.log').write_text(log)
    raw = (directory/(stems[0]+'.gsas')).read_bytes()
    return raw, {'command_flags': args, 'log': log, 'gsas_sha256': digest(raw)}

def oracle_prepare(source, directory, oracle, *, cutoff=1., drop_detectors=(), keep_detector1=False):
    directory=Path(directory);directory.mkdir(parents=True, exist_ok=False)
    target=directory/'input.bt1';target.write_bytes(Path(source).read_bytes())
    args=[f'-c{cutoff:.10g}']
    if keep_detector1:args.append('-1')
    if drop_detectors:args.append('-d'+','.join(map(str,drop_detectors)))
    args.append('input')
    log=direct_run(oracle.executables['proprep'],args,directory)
    (directory/'oracle.log').write_text(log)
    return (directory/'input.raw').read_bytes()

def synthetic_raw(path, *, variant='quarter', monitor=137, count_multiplier=1):
    """32 ordered banks, common 0.05-degree spacing and one-degree overlaps.

    Curvature (not a linear ramp) exposes wrong interpolation brackets. The
    threshold fixture straddles the actual historical 0.00011-degree snap rule.
    The mask fixture contains a positive aligned point followed by a negative
    point: historical Fortran rejects that interval even at interpolation r=0.
    """
    n=121; q=np.arange(n,dtype=int)
    offsets=np.array([0.,.00009,.000109,.00011,.000111,.00012,-.0001,.0002])
    banks=[]
    for j in range(1,33):
        angles=1.3+(j-1)*5.+q*.05
        counts=(100+7*j+20*(q%7)**2)*count_multiplier
        scale=1.+.01*j
        if variant=='quarter':zero=.0125
        elif variant=='threshold':zero=-offsets[(j-1)%len(offsets)]
        elif variant=='half':zero=.025
        elif variant=='background':
            zero=.0125;scale=1.;counts=(1000+100*j+10*(q%7)**2)*count_multiplier
        elif variant=='mask-zero':
            zero=0.;scale=2.85 if j==1 else 1.
            if j==1:
                counts=counts.copy();counts[10]=-1;counts[20:22]=0;counts[30:33]=-1
        else:raise ValueError(variant)
        banks.append(Bank(j,angles,counts,scale,float(zero)))
    header={'title':'Independent synthetic '+variant,'wavelength':2.0782,'collimation':(15,20),
            'experimental_parameters':(0,0,0,1,1),'monochromator':'GE311','monitor':monitor,
            'date':'1-Nov-2026','format':'raw','a4_step':.05}
    write_raw(RawDataset(header,banks,str(path)),path)
    return Path(path).read_bytes()


def precision_raw(path, *, monitor=16777217, multiplier=1, implied_decimal=False):
    """Exercise nonexact single-precision scales/counts and monitor parsing.

    The monitor preset deliberately lies just above 2**24; the independent
    oracle rounds it on REAL input. A 123-point bank also exercises final
    partial-record padding. Header monitor is written with an explicit decimal
    point, since ordinary G14.7 cannot preserve all these significant digits.
    With implied_decimal=True the field intentionally lacks a decimal point:
    original Fortran G14.1 then divides its integer token by ten before REAL
    rounding. That separate input-parsing case must not be mistaken for a
    reduction-kernel rounding mismatch.
    """
    n=123;q=np.arange(n,dtype=np.int64);banks=[]
    # Keep resulting ESDs representable above zero after F8.0 rounding;
    # rejection of positive-I/zero-ESD serialization is a separate safety test.
    scales=[.1001,.3333,2.85,1.004,9.9999,.1013]
    for j in range(1,33):
        counts=((q*7919+j*1237)%97003+101)*multiplier
        banks.append(Bank(j,1.3+(j-1)*5+q*.05,counts,scales[(j-1)%len(scales)],.0125))
    header={'title':'Precision and monitor regression','wavelength':2.0782,'collimation':(15,20),
            'experimental_parameters':(0,0,0,1,1),'monochromator':'GE311','monitor':100,
            'date':'1-Nov-2026','format':'raw','a4_step':.05}
    write_raw(RawDataset(header,banks,str(path)),path)
    lines=Path(path).read_text().splitlines()
    monitor_field=f'{monitor:14d}' if implied_decimal else f'{monitor:13d}.'
    lines[0]=lines[0][:70]+monitor_field
    raw=('\n'.join(lines)+'\n').encode('ascii');Path(path).write_bytes(raw)
    return raw


def implied_calibration_raw(path):
    """Encode equivalent scale/zero values without explicit decimal points.

    The independent source reads these as two F8.4 fields. Keeping the format
    fixture separate from the arithmetic fixtures makes failures attributable.
    """
    raw=synthetic_raw(path,variant='quarter')
    lines=raw.decode('ascii').splitlines();pos=2
    while pos<len(lines):
        row=lines[pos];count=int(row[45:50])
        scale=f'{round(float(row[24:32])*10000):8d}'
        zero=f'{round(float(row[32:40])*10000):8d}'
        lines[pos]=row[:24]+scale+zero+row[40:]
        pos+=1+(count+11)//12
    result=('\n'.join(lines)+'\n').encode('ascii');Path(path).write_bytes(result)
    return result


def compare_python_reduction(raw_inputs, directory, oracle, **options):
    """Run actual Python code and independently invoked native reference."""
    from bt1modern.legacy import reduce_legacy
    from bt1modern.gsas import write_legacy_gsas
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    datasets=[]
    for i,raw in enumerate(raw_inputs):
        path=directory/f'input{i+1:02d}.raw';path.write_bytes(raw);datasets.append(read_raw(path))
    pattern=reduce_legacy(datasets,**options)
    writer=write_legacy_gsas(pattern,directory/'python.gsas')
    python_bytes=(directory/'python.gsas').read_bytes()
    reference_bytes,reference_metadata=oracle_reduce(raw_inputs,directory/'oracle',oracle,**options)
    got,expected=declared_gsas(python_bytes),declared_gsas(reference_bytes)
    same_angles=np.array_equal(got['angles'],expected['angles'])
    same_pairs=np.array_equal(got['pairs'],expected['pairs'])
    same_shape=got['pairs'].shape==expected['pairs'].shape
    mismatches=[]
    if same_shape:
        for i,j in np.argwhere(got['pairs']!=expected['pairs'])[:30]:
            internal=pattern.intensity[i] if j==0 else pattern.sigma[i]
            mismatches.append({'index':int(i),'angle':float(expected['angles'][i]),
                               'field':'intensity' if j==0 else 'sigma',
                               'python_serialized':float(got['pairs'][i,j]),
                               'oracle_serialized':float(expected['pairs'][i,j]),
                               'python_unrounded':float(internal),
                               'distance_to_nearest_half_integer':float(abs((float(internal)-np.floor(internal))-.5))})
    pattern_angles_match=np.array_equal(pattern.angles,got['angles'])
    covariance_absent=pattern.covariance_factor is None
    record={'options':options,'passed':bool(same_angles and same_pairs and pattern_angles_match and covariance_absent),
            'comparison_policy':'Exact equality, including integer F8.0 serialization; no numerical tolerance.',
            'declared_points_python':got['count'],'declared_points_oracle':expected['count'],
            'exact_declared_angles':same_angles,'exact_declared_intensity_sigma_pairs':same_pairs,
            'pattern_angles_match_header':pattern_angles_match,'covariance_not_invented':covariance_absent,
            'mismatched_values':int(np.count_nonzero(got['pairs']!=expected['pairs'])) if same_shape else None,
            'first_mismatches':mismatches,'python_padding_pairs':got['padding_pairs'],
            'oracle_padding_pairs':expected['padding_pairs'],
            'whole_output_bytes_equal':python_bytes==reference_bytes,
            'whole_bytes_note':'Staging titles/instrument references/blank padding may differ; declared numeric data must match exactly.',
            'input_sha256':[digest(x) for x in raw_inputs],'python_metadata':pattern.metadata,
            'writer_metadata':writer,'python_gsas_sha256':digest(python_bytes),'oracle':reference_metadata}
    (directory/'comparison.json').write_text(json.dumps(record,indent=2,default=json_default)+'\n')
    return record,pattern


def compare_python_preparation(source,directory,oracle,**options):
    from bt1modern.io import prepare_legacy_bt1
    source=Path(source);before=digest(source)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    prepared=prepare_legacy_bt1(parse_bt1(source),**options)
    write_raw(prepared,directory/'python.raw')
    raw=(directory/'python.raw').read_bytes()
    reference=oracle_prepare(source,directory/'oracle',oracle,**options)
    (directory/'oracle.raw').write_bytes(reference)
    a,b=read_raw(directory/'python.raw'),read_raw(directory/'oracle.raw')
    header_fields=('wavelength','collimation','monochromator','monitor','experimental_parameters')
    header_equal=all(a.header[k]==b.header[k] for k in header_fields)
    banks_equal=len(a.banks)==len(b.banks)
    if banks_equal:
        for x,y in zip(a.banks,b.banks):
            banks_equal &= (x.detector,x.scale,x.zero)==(y.detector,y.scale,y.zero)
            banks_equal &= np.array_equal(x.angles,y.angles) and np.array_equal(x.counts,y.counts)
    unchanged=digest(source)==before
    record={'source':str(source),'source_sha256':before,'options':options,
            'passed':bool(header_equal and banks_equal and unchanged),'header_numeric_equal':bool(header_equal),
            'banks_numeric_equal':bool(banks_equal),'source_unchanged':unchanged,
            'first_three_starts_degrees':[float(x.angles[0]) for x in a.banks[:3]],
            'oracle_first_three_starts_degrees':[float(x.angles[0]) for x in b.banks[:3]],
            'whole_raw_bytes_equal':raw==reference}
    (directory/'comparison.json').write_text(json.dumps(record,indent=2,default=json_default)+'\n')
    return record,raw


def validate(args):
    output=Path(args.out).resolve();output.mkdir(parents=True,exist_ok=False)
    audit=Path(args.audit_root).resolve();oracle=build_oracle(audit,output/'oracle-build',compiler=args.compiler)
    report={'oracle':oracle.metadata,'python':sys.version,'numpy':np.__version__,'preparation':[],'cases':[],
            'execution':'Python reducer/preparation/writer at runtime; compiled Fortran only independent test oracle.',
            'comparison':'Exact declared GSAS angles/intensity/ESD; no half-unit tolerance.',
            'limits':'Historical numerical compatibility does not establish scientific correctness or instrument calibration.'}
    project=Path(__file__).resolve().parent
    report['python_sources']={str(path.relative_to(project)):digest(path) for path in
        [project/'validate_python_legacy.py',*(project/'bt1modern').glob('*.py')]}
    sources=[audit/'retrieval'/f'Mn5Si00{i}.bt1' for i in (1,2)]
    if args.legacy_fixture_dir:sources.append(Path(args.legacy_fixture_dir)/'test.bt1')
    standard_raw=[]
    def add_case(name,raws,**options):
        try:
            row,_=compare_python_reduction(raws,output/name,oracle,**options)
        except Exception as exc:
            row={'passed':False,'options':options,'error':f'{type(exc).__name__}: {exc}'}
        row['case']=name;report['cases'].append(row)
        if not row['passed']:print(name, row.get('first_mismatches') or row.get('error'),flush=True)
    for i,source in enumerate(sources,1):
        # Oracle RAW is used for reducer tests, isolating preprocessing from the
        # reduction comparison. Python preparation is tested independently.
        raw=oracle_prepare(source,output/f'fixture{i}',oracle,cutoff=1.);standard_raw.append(raw)
        for label,options in [('standard',{'cutoff':1.}),('nongrid',{'cutoff':2.334,'drop_detectors':(2,10),'keep_detector1':True})]:
            try:row,_=compare_python_preparation(source,output/f'prep{i}-{label}',oracle,**options)
            except Exception as exc:row={'passed':False,'source':str(source),'options':options,'error':f'{type(exc).__name__}: {exc}'}
            report['preparation'].append(row)
        for uncertainty in ('legacy-linear','independent'):
            for method in ('interpolate','nearest'):
                add_case(f'real{i}-{uncertainty}-{method}',[raw],uncertainty=uncertainty,method=method,background=False,rescale=False)
    for uncertainty in ('legacy-linear','independent'):
        for method in ('interpolate','nearest'):
            add_case(f'combined-{uncertainty}-{method}',standard_raw[:2],uncertainty=uncertainty,method=method,background=False,rescale=False)
    for variant in ('quarter','threshold','mask-zero','half'):
        raw=synthetic_raw(output/(variant+'.raw'),variant=variant)
        for uncertainty in ('legacy-linear','independent'):
            for method in ('interpolate','nearest'):
                add_case(f'synthetic-{variant}-{uncertainty}-{method}',[raw],uncertainty=uncertainty,method=method,background=False,rescale=False)
    raw=synthetic_raw(output/'background.raw',variant='background')
    for uncertainty in ('legacy-linear','independent'):
        for background in (False,True):
            for rescale in (False,True):
                add_case(f'flags-{uncertainty}-{background}-{rescale}',[raw],uncertainty=uncertainty,method='interpolate',background=background,rescale=rescale)
    first=precision_raw(output/'precision1.raw',monitor=16777217)
    second=precision_raw(output/'precision2.raw',monitor=33554435,multiplier=2)
    for inputs,label in [([first],'single'),([first,second],'combined')]:
        for uncertainty in ('legacy-linear','independent'):
            for method in ('interpolate','nearest'):
                add_case(f'precision-{label}-{uncertainty}-{method}',inputs,uncertainty=uncertainty,method=method,background=False,rescale=False)
    # A distinct legitimate Fortran fixed-field input case: no decimal point
    # means the G14.1 edit descriptor supplies an implied decimal digit. This
    # previously exposed a Python parser error rather than kernel arithmetic.
    implied_first=precision_raw(output/'implied-monitor1.raw',monitor=16777217,implied_decimal=True)
    implied_second=precision_raw(output/'implied-monitor2.raw',monitor=33554435,multiplier=2,implied_decimal=True)
    for uncertainty in ('legacy-linear','independent'):
        for method in ('interpolate','nearest'):
            add_case(f'implied-monitor-{uncertainty}-{method}',[implied_first,implied_second],uncertainty=uncertainty,method=method,background=False,rescale=False)
    implied_calibration=implied_calibration_raw(output/'implied-calibration.raw')
    for uncertainty in ('legacy-linear','independent'):
        for method in ('interpolate','nearest'):
            add_case(f'implied-calibration-{uncertainty}-{method}',[implied_calibration],uncertainty=uncertainty,method=method,background=False,rescale=False)
    report['passed']=all(r['passed'] for r in report['cases']+report['preparation'])
    (output/'validation.json').write_text(json.dumps(report,indent=2,default=json_default)+'\n')
    print(json.dumps({'output':str(output),'reduction_cases':len(report['cases']),
                      'preparation_cases':len(report['preparation']),
                      'failed_reductions':sum(not r['passed'] for r in report['cases']),
                      'failed_preparations':sum(not r['passed'] for r in report['preparation']),
                      'passed':report['passed']},indent=2))
    return 0 if report['passed'] else 1


def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--audit-root',required=True,type=Path)
    p.add_argument('--legacy-fixture-dir',type=Path)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--compiler',default='gfortran')
    return validate(p.parse_args())


if __name__=='__main__':raise SystemExit(main())
