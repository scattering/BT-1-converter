/** Conservative suggestions only: selecting or converting remains a user action.
 *
 * Equal instrument/scan/calibration metadata and nearby measured temperature
 * means do not prove sample identity or measurement-state equivalence. Titles
 * are retained verbatim for review, never normalized into an identity guess.
 */
export const TEMPERATURE_LIMIT_K = 0.2;

const HEADER_FIELDS = [
  'monochromator', 'wavelength', 'collimation_all', 'mosaic', 'magnetic_field',
  'experimental_parameters', 'count_type', 'a3_range', 'a4_start', 'a4_end',
  'a4_step', 'detector_separation', 'number_of_detectors',
];
const BANK_FIELDS = ['detector', 'points', 'first_angle_deg', 'last_angle_deg', 'scale', 'zero_deg'];
const numeric = value => typeof value === 'number' && Number.isFinite(value);
const positiveInteger = value => Number.isInteger(value) && value > 0;
const numbers = (value, length) => Array.isArray(value) && value.length === length && value.every(numeric);
const text = value => typeof value === 'string' && value.trim().length > 0;
const compareText = (left, right) => left < right ? -1 : left > right ? 1 : 0;

// Compare the serialized decimal values, so e.g. 10.3 - 10.1 is exactly the
// inclusive 0.2 K boundary without adding an arbitrary numerical tolerance.
function decimalParts(value) {
  const [coefficient, exponent = '0'] = value.toString().split('e');
  const fractionLength = (coefficient.split('.')[1] || '').length;
  return { integer: BigInt(coefficient.replace('.', '')), exponent: Number(exponent) - fractionLength };
}
function decimalSpan(minimum, maximum) {
  const parts = [minimum, maximum, TEMPERATURE_LIMIT_K].map(decimalParts);
  const exponent = Math.min(...parts.map(part => part.exponent));
  const integers = parts.map(part => part.integer * 10n ** BigInt(part.exponent - exponent));
  const difference = integers[1] - integers[0];
  return { withinLimit: difference <= integers[2], value: Number(`${difference}e${exponent}`) };
}

function candidate(scan) {
  if (!scan || !text(scan.id) || !text(scan.name)) return null;
  const header = scan.record?.header;
  const banks = scan.record?.banks;
  if (!header || header.format !== 'bt1' || header.complete !== true || header.count_type !== 'NEUT') return null;
  if (!positiveInteger(header.planned_points) || header.recorded_points !== header.planned_points) return null;
  const temperature = header.temp_summary;
  if (!temperature || temperature.unit !== 'K' || temperature.count !== header.recorded_points ||
      ![temperature.min, temperature.max, temperature.mean].every(numeric) ||
      temperature.min < 0 || temperature.min > temperature.mean || temperature.mean > temperature.max) return null;
  if (!text(header.monochromator) || !numeric(header.wavelength) || header.wavelength <= 0 ||
      !numbers(header.collimation_all, 4) || !numbers(header.mosaic, 3) ||
      !numeric(header.magnetic_field) || !numbers(header.experimental_parameters, 5) ||
      !numbers(header.a3_range, 3) || ![header.a4_start, header.a4_end, header.a4_step].every(numeric) ||
      header.a4_step <= 0 || header.a4_end < header.a4_start ||
      !numeric(header.detector_separation) || header.detector_separation <= 0 ||
      !positiveInteger(header.number_of_detectors) || !numeric(header.monitor) || header.monitor <= 0) return null;
  if (!Array.isArray(banks) || banks.length !== header.number_of_detectors ||
      !banks.every((bank, index) => bank && BANK_FIELDS.every(field => numeric(bank[field])) &&
        bank.detector === index + 1 && bank.points === header.recorded_points &&
        bank.first_angle_deg <= bank.last_angle_deg && bank.scale > 0)) return null;
  return {
    id: scan.id, name: scan.name,
    title: typeof header.title === 'string' ? header.title : '',
    temperatureMean: temperature.mean, temperatureMin: temperature.min, temperatureMax: temperature.max,
    temperatureSpan: decimalSpan(temperature.min, temperature.max).value,
    temperatureDriftWarning: !decimalSpan(temperature.min, temperature.max).withinLimit,
    signature: JSON.stringify([HEADER_FIELDS.map(field => header[field]), banks.map(bank => BANK_FIELDS.map(field => bank[field]))]),
  };
}

function makeGroup(members) {
  const temperatureMin = members[0].temperatureMean;
  const temperatureMax = members.at(-1).temperatureMean;
  const warnings = [];
  if (new Set(members.map(member => member.title)).size > 1) {
    warnings.push('Sample descriptions differ. Review each full description and confirm the same sample and measurement state.');
  }
  const missingDescriptions = members.filter(member => !text(member.title));
  if (missingDescriptions.length) warnings.push(`Sample description missing for ${missingDescriptions.map(member => member.name).join(', ')}.`);
  for (const member of members) {
    if (member.temperatureDriftWarning) {
      warnings.push(`${member.name}: measured temperature varies by ${member.temperatureSpan.toPrecision(5)} K within the scan. This suggestion compares means; review temperature stability.`);
    }
  }
  return {
    id: `compatible:${members.map(member => encodeURIComponent(member.id)).join('|')}`,
    ids: members.map(member => member.id), names: members.map(member => member.name), count: members.length,
    label: 'Instrument/temperature compatible; confirm same sample/state',
    temperatureMin, temperatureMax, temperatureSpread: decimalSpan(temperatureMin, temperatureMax).value,
    temperatureLimit: TEMPERATURE_LIMIT_K, temperatureBasis: 'measured means',
    measuredTemperatureMin: Math.min(...members.map(member => member.temperatureMin)),
    measuredTemperatureMax: Math.max(...members.map(member => member.temperatureMax)),
    descriptions: members.map(({ id, name, title, temperatureMean, temperatureMin: min, temperatureMax: max, temperatureSpan }) =>
      ({ id, name, title, temperatureMean, temperatureMin: min, temperatureMax: max, temperatureSpan })),
    reasons: [
      'Complete BT-1 scans with a measured temperature for every recorded point.',
      'Measured mean temperatures span at most 0.2 K across the whole group.',
      'Recorded monochromator, wavelength, collimation, mosaic, field, and experiment settings match exactly.',
      'Recorded scan geometry, detector layout, scales, and zero corrections match exactly.',
    ],
    warnings,
    requiresSampleStateConfirmation: true,
  };
}

/** Return disjoint candidate groups; never mutate selection or combine scans.
 * Groups are deterministic: exact metadata buckets, then ascending measured
 * mean temperature and stable id, partitioned from each lowest remaining mean.
 * A 0, 0.15, 0.3 K chain consequently never becomes one 0.3 K-wide group.
 * Ineligible scans and singleton partitions are omitted, not certified unsafe.
 */
export function proposeGroups(scans) {
  const supplied = Array.from(scans || []);
  const idCounts = new Map();
  for (const scan of supplied) idCounts.set(scan?.id, (idCounts.get(scan?.id) || 0) + 1);
  const buckets = new Map();
  for (const scan of supplied) {
    if (idCounts.get(scan?.id) !== 1) continue;
    const entry = candidate(scan);
    if (!entry) continue;
    if (!buckets.has(entry.signature)) buckets.set(entry.signature, []);
    buckets.get(entry.signature).push(entry);
  }
  const groups = [];
  for (const entries of buckets.values()) {
    entries.sort((a, b) => a.temperatureMean - b.temperatureMean || compareText(a.id, b.id));
    let members = [];
    for (const entry of entries) {
      if (members.length && !decimalSpan(members[0].temperatureMean, entry.temperatureMean).withinLimit) {
        if (members.length > 1) groups.push(makeGroup(members));
        members = [];
      }
      members.push(entry);
    }
    if (members.length > 1) groups.push(makeGroup(members));
  }
  return groups.sort((a, b) => a.temperatureMin - b.temperatureMin || compareText(a.id, b.id));
}
