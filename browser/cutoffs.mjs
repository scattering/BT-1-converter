// Browser preparation choices. These helpers never alter acquisition bytes.
export const DEFAULT_CUTOFF_M4 = 1.0;
const DECIMAL = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;

export function isCutoffEligible(scan) {
  return scan?.record?.header?.format === 'bt1';
}

export function parseCutoffM4(value) {
  if (typeof value !== 'string' && typeof value !== 'number') throw new Error('Enter a finite M4 cutoff of 0° or greater.');
  const text = String(value).trim();
  if (!text) throw new Error('Enter an M4 cutoff; a blank value cannot be used.');
  if (!DECIMAL.test(text)) throw new Error('Enter a finite M4 cutoff of 0° or greater.');
  const number = Number(text);
  if (!Number.isFinite(number) || number < 0) throw new Error('Enter a finite M4 cutoff of 0° or greater.');
  return number === 0 ? 0 : number;
}

export function formatCutoffM4(value) {
  const number = parseCutoffM4(value);
  return Number.isInteger(number) ? number.toFixed(1) : String(number);
}

export function withDefaultCutoff(scan) {
  return { ...scan, cutoffM4: isCutoffEligible(scan) ?
    (scan.cutoffM4 === undefined ? DEFAULT_CUTOFF_M4 : scan.cutoffM4) : null };
}

export function planCutoffBatch(scans, draft) {
  // Parse before proposing any update, even when the current selection is empty.
  const cutoffM4 = parseCutoffM4(draft);
  return Array.from(scans).filter(scan => scan.selected && isCutoffEligible(scan))
    .map(scan => ({ id: scan.id, cutoffM4 }));
}

export function fileForConversion(scan, draft = undefined) {
  const file = { name: scan.name, bytes: scan.bytes };
  if (isCutoffEligible(scan)) {
    // A present but invalid draft must never fall back to a previously saved value.
    file.cutoffM4 = parseCutoffM4(draft === undefined ? withDefaultCutoff(scan).cutoffM4 : draft);
  }
  return file;
}

export function cutoffGroupWarnings(scans, drafts = new Map()) {
  const eligible = Array.from(scans).filter(isCutoffEligible);
  let values;
  try { values = eligible.map(scan => fileForConversion(scan, drafts.get(scan.id)).cutoffM4); }
  catch { return ['An M4 cutoff in this group is invalid. Correct it before conversion.']; }
  if (new Set(values).size > 1) return [
    'M4 cutoffs differ across these scans. Preparation can produce different detector grids; legacy combination may reject them. Use correct mode, or apply the same cutoff to matching acquisitions.',
  ];
  return [];
}
