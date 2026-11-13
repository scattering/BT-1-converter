import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { proposeGroups } from './compatibility.mjs';

function scan(id, mean = 3, title = 'Sample A') {
  return {
    id, name: `${id}.bt1`, selected: false,
    record: {
      header: {
        format: 'bt1', complete: true, count_type: 'NEUT', planned_points: 3, recorded_points: 3,
        temp_summary: { count: 3, min: mean, max: mean, mean, unit: 'K' },
        title, monochromator: 'GE311', wavelength: 2.0777,
        collimation_all: [60, 20, 15, 15], mosaic: [0, 0, 0], magnetic_field: 0,
        experimental_parameters: [0, 0, 0, 1, 1], a3_range: [0, 0, 0],
        a4_start: 1.3, a4_end: 1.4, a4_step: 0.05, detector_separation: 5,
        number_of_detectors: 2, monitor: 100,
      },
      banks: [1, 2].map(detector => ({ detector, points: 3, first_angle_deg: 1.3 + (detector - 1) * 5,
        last_angle_deg: 1.4 + (detector - 1) * 5, scale: 1, zero_deg: 0 })),
    },
  };
}

test('inclusive 0.2 K boundary uses decimal values without widening the threshold', () => {
  for (const [a, b] of [[0, 0.2], [3, 3.2], [10.1, 10.3], [100.1, 100.3]]) {
    const groups = proposeGroups([scan('a', a), scan('b', b)]);
    assert.equal(groups.length, 1);
    assert.equal(groups[0].temperatureSpread, 0.2);
  }
  assert.deepEqual(proposeGroups([scan('a', 3), scan('b', 3.200000000000001)]), []);
});

test('total span prevents adjacent chaining and partitions deterministically', () => {
  const scans = [scan('c', 0.3), scan('b', 0.15), scan('a', 0), scan('d', 0.4)];
  assert.deepEqual(proposeGroups(scans).map(group => group.ids), [['a', 'b'], ['c', 'd']]);
  assert.deepEqual(proposeGroups([...scans].reverse()), proposeGroups(scans));
});

test('missing, null, nonfinite, or malformed required metadata never match by accident', () => {
  for (const field of ['monochromator', 'wavelength', 'collimation_all', 'mosaic', 'magnetic_field',
    'experimental_parameters', 'count_type', 'a3_range', 'a4_start', 'a4_end', 'a4_step',
    'detector_separation', 'number_of_detectors', 'monitor', 'temp_summary']) {
    for (const value of [undefined, null]) {
      const a = scan('a'); const b = scan('b');
      a.record.header[field] = value; b.record.header[field] = value;
      assert.deepEqual(proposeGroups([a, b]), [], field);
    }
  }
  const a = scan('a'); const b = scan('b');
  a.record.header.mosaic = b.record.header.mosaic = [0, null, 0];
  assert.deepEqual(proposeGroups([a, b]), []);
  a.record.header.mosaic = b.record.header.mosaic = [0, 0, 0];
  a.record.header.temp_summary.mean = b.record.header.temp_summary.mean = NaN;
  assert.deepEqual(proposeGroups([a, b]), []);
});

test('instrument, geometry, magnetic field, and detector calibration mismatches split buckets', () => {
  const mutations = [
    s => { s.record.header.monochromator = 'CU311'; },
    s => { s.record.header.wavelength += 0.00001; },
    s => { s.record.header.collimation_all[2] = 20; },
    s => { s.record.header.mosaic[0] = 1; },
    s => { s.record.header.magnetic_field = 0.1; },
    s => { s.record.header.experimental_parameters[2] = 1; },
    s => { s.record.header.a3_range[0] = 1; },
    s => { s.record.header.a4_end += 0.05; },
    s => { s.record.header.a4_start += 0.01; },
    s => { s.record.header.a4_step += 0.001; },
    s => { s.record.header.detector_separation += 0.1; },
    s => { s.record.banks[0].scale = 1.001; },
    s => { s.record.banks[0].zero_deg = 0.0001; },
    s => { s.record.banks[0].first_angle_deg += 0.001; },
    s => { s.record.banks[0].last_angle_deg += 0.001; },
  ];
  for (const mutate of mutations) {
    const a = scan('a'); const b = scan('b'); mutate(b);
    assert.deepEqual(proposeGroups([a, b]), []);
  }
});

test('partial, RAW, incomplete temperature coverage, and inconsistent acquisition counts are excluded', () => {
  const changes = [
    h => { h.complete = false; }, h => { h.complete = null; }, h => { h.format = 'raw'; },
    h => { h.recorded_points = 2; }, h => { h.planned_points = null; },
    h => { h.temp_summary.count = 2; }, h => { h.temp_summary.unit = 'C'; },
    h => { h.temp_summary.min = 3.1; }, h => { h.temp_summary.max = 2.9; },
    h => { h.count_type = 'TIME'; },
  ];
  for (const change of changes) {
    const a = scan('a'); const b = scan('b'); change(b.record.header);
    assert.deepEqual(proposeGroups([a, b]), []);
  }
});

test('missing calibration fields, bank points, duplicate or reordered banks are excluded', () => {
  for (const field of ['detector', 'points', 'first_angle_deg', 'last_angle_deg', 'scale', 'zero_deg']) {
    const a = scan('a'); const b = scan('b');
    delete a.record.banks[0][field]; delete b.record.banks[0][field];
    assert.deepEqual(proposeGroups([a, b]), [], field);
  }
  const a = scan('a'); const b = scan('b'); b.record.banks.reverse();
  assert.deepEqual(proposeGroups([a, b]), []);
  b.record.banks.reverse(); b.record.banks[0].points = 2;
  assert.deepEqual(proposeGroups([a, b]), []);
});

test('different titles remain verbatim and trigger review, without suppressing instrument candidates', () => {
  const a = scan('a', 3, 'Mn5Si2 Ge311 60min 2.5K');
  const b = scan('b', 3.1, "Mn5Si2 Ge311 60' 2.5K_2");
  const [group] = proposeGroups([a, b]);
  assert.deepEqual(group.descriptions.map(value => value.title), [a.record.header.title, b.record.header.title]);
  assert.ok(group.warnings.some(warning => warning.includes('descriptions differ')));
  assert.equal(group.requiresSampleStateConfirmation, true);
  assert.match(group.label, /confirm same sample\/state/);
});

test('within-scan drift warns without excluding nearby mean temperatures', () => {
  const a = scan('a', 3); const b = scan('b', 3.1);
  b.record.header.temp_summary.min = 2.9; b.record.header.temp_summary.max = 3.3;
  const [group] = proposeGroups([a, b]);
  assert.equal(group.temperatureSpread, 0.1);
  assert.ok(group.warnings.some(warning => warning.includes('b.bt1') && warning.includes('within the scan')));
  assert.equal(group.measuredTemperatureMin, 2.9);
  assert.equal(group.measuredTemperatureMax, 3.3);
});

test('different monitor presets are allowed; inputs and selections remain untouched', () => {
  const a = scan('b'); const b = scan('a'); b.record.header.monitor = 237;
  const before = JSON.stringify([a, b]);
  const [group] = proposeGroups([a, b]);
  assert.deepEqual(group.ids, ['a', 'b']);
  assert.equal(JSON.stringify([a, b]), before);
  assert.deepEqual(proposeGroups([a, a]), []);
  assert.deepEqual(proposeGroups([a]), []);
});

const fixture = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../artifacts/pyodide-validation/results/real-correct-combine/real_combined.json');
test('available real Mn5Si001/002 metadata produces a reviewable candidate despite title and drift differences',
  { skip: !fs.existsSync(fixture) && 'Local real-fixture conversion metadata is unavailable' }, () => {
    const records = JSON.parse(fs.readFileSync(fixture, 'utf8')).inputs;
    const scans = records.map(record => ({ id: path.basename(record.path), name: path.basename(record.path), record }));
    const [group] = proposeGroups(scans);
    assert.equal(group.count, 2);
    assert.deepEqual([...group.names].sort(), ['Mn5Si001.bt1', 'Mn5Si002.bt1']);
    assert.ok(group.temperatureSpread < 0.03);
    assert.ok(group.warnings.some(warning => warning.includes('descriptions differ')));
    assert.ok(group.warnings.some(warning => warning.includes('Mn5Si002.bt1') && warning.includes('within the scan')));
  });
