import assert from 'node:assert/strict';
import test from 'node:test';
import { cutoffGroupWarnings, fileForConversion, formatCutoffM4, isCutoffEligible, parseCutoffM4, planCutoffBatch, withDefaultCutoff } from './cutoffs.mjs';

const make = (id, format = 'bt1', selected = true, cutoffM4 = undefined) => ({
  id, name: `${id}.${format}`, selected, cutoffM4,
  record: { header: { format } }, bytes: new Uint8Array([11, 22, 33]).buffer,
});

test('finite nonnegative decimal cutoff accepts Craig1.9 and keeps default1', () => {
  assert.equal(parseCutoffM4('1.9'), 1.9);
  assert.equal(parseCutoffM4(' 1.0 '), 1);
  assert.equal(parseCutoffM4('0'), 0);
  assert.equal(parseCutoffM4('1e-3'), 0.001);
  assert.equal(formatCutoffM4(1), '1.0');
});

test('blank, nonfinite, negative and nondecimal drafts cannot become scientific settings', () => {
  for (const value of ['', ' ', null, undefined, true, NaN, Infinity, '-0.1', 'NaN', 'Infinity', '1e999', '0x10', '1.9deg']) {
    assert.throws(() => parseCutoffM4(value), Error, String(value));
  }
});

test('older BT1 records migrate to1 without modifying original objects or bytes', () => {
  const scan = make('old');
  const migrated = withDefaultCutoff(scan);
  assert.equal(migrated.cutoffM4, 1);
  assert.equal(scan.cutoffM4, undefined);
  assert.equal(migrated.bytes, scan.bytes);
  assert.equal(withDefaultCutoff(make('edited', 'bt1', true, 1.9)).cutoffM4, 1.9);
});

test('RAW is already prepared and never sends a new cutoff to the worker', () => {
  const raw = make('prepared', 'raw', true, 1.9);
  assert.equal(isCutoffEligible(raw), false);
  assert.equal(withDefaultCutoff(raw).cutoffM4, null);
  assert.deepEqual(Object.keys(fileForConversion(raw, 'bad')), ['name', 'bytes']);
});

test('batch plan touches only selected BT1 scans and does not mutate any record', () => {
  const scans = [make('one'), make('two', 'bt1', false, 2.2), make('raw', 'raw'), make('three', 'bt1', true, 1.5)];
  const before = scans.map(scan => scan.cutoffM4);
  assert.deepEqual(planCutoffBatch(scans, '1.9'), [{ id: 'one', cutoffM4: 1.9 }, { id: 'three', cutoffM4: 1.9 }]);
  assert.deepEqual(scans.map(scan => scan.cutoffM4), before);
  assert.throws(() => planCutoffBatch(scans, ''), /blank/);
  assert.deepEqual(scans.map(scan => scan.cutoffM4), before);
});

test('an invalid current draft blocks conversion instead of silently using saved1.9', () => {
  const scan = make('one', 'bt1', true, 1.9);
  assert.throws(() => fileForConversion(scan, ''), /blank/);
  assert.throws(() => fileForConversion(scan, '-1'), /0°/);
  const file = fileForConversion(scan, '2.1');
  assert.equal(file.cutoffM4, 2.1);
  assert.equal(file.bytes, scan.bytes);
  assert.equal(scan.cutoffM4, 1.9);
});

test('cutoff warning reports mixed preparation choices without mutating group membership', () => {
  const scans = [make('one', 'bt1', true, 1), make('two', 'bt1', true, 1.9)];
  assert.equal(cutoffGroupWarnings(scans).length, 1);
  assert.match(cutoffGroupWarnings(scans)[0], /legacy/);
  assert.deepEqual(scans.map(scan => scan.id), ['one', 'two']);
  assert.deepEqual(cutoffGroupWarnings(scans, new Map([['one', '1.9']])), []);
  assert.match(cutoffGroupWarnings(scans, new Map([['two', '']]))[0], /invalid/);
});
