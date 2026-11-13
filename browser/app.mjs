import { CONFIG } from './runtime-config.mjs';
import { fetchLatestScans, formatEasternDate, LISTING } from './ncnr.mjs';
import { openStorage, loadPreferences, savePreferences } from './storage.mjs';
import { proposeGroups } from './compatibility.mjs';

const byId = id => document.getElementById(id);
const worker = new Worker(new URL('./worker.mjs', import.meta.url), { type: 'module' });
const pending = new Map();
const scans = new Map();
const results = new Map();
const blobURLs = [];
let sequence = 0;
let busy = true;
let runtimeReady = false;
let runtimeProbe;
let storage;
let storageNotice = '';

function status(message, error = false) {
  byId('status').textContent = message;
  byId('status').classList.toggle('error', error);
}
worker.onmessage = ({ data }) => {
  if (data.type === 'status') { status(data.message); return; }
  const task = pending.get(data.id);
  if (!task) return;
  pending.delete(data.id);
  if (data.type === 'error') task.reject(new Error(data.error)); else task.resolve(data.result);
};
worker.onerror = event => {
  status(`Python worker failed: ${event.message}. Reload the page to restart.`, true);
  for (const task of pending.values()) task.reject(new Error(event.message));
  pending.clear();
  runtimeReady = false;
  controls();
};
function request(action, extra = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, action, ...extra });
  });
}
async function evidence(kind, report) {
  if (!CONFIG.evidenceURL) return;
  const response = await fetch(CONFIG.evidenceURL, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, userAgent: navigator.userAgent, runtime: runtimeProbe, report }),
  });
  if (!response.ok) throw new Error(`Local evidence could not be saved: HTTP ${response.status}`);
}
function selectedScans() { return [...scans.values()].filter(scan => scan.selected); }
function currentOptions(combine = false) {
  return {
    mode: byId('mode').value, uncertainty: byId('uncertainty').value,
    method: byId('method').value, covariance: byId('covariance').checked,
    partial: byId('partial').checked, combine, combineName: byId('combine-name').value.trim(),
    allowMixedTemperature: byId('allow-mixed-temperature').checked,
  };
}
function controls() {
  for (const element of document.querySelectorAll('button:not([data-navigation]):not([data-help]),input,select')) element.disabled = busy;
  const selected = selectedScans();
  for (const id of ['ncnr-load', 'quick-convert', 'files', 'validate', 'probe', 'diagnostics', 'example']) byId(id).disabled = busy || !runtimeReady;
  byId('convert').disabled = busy || !runtimeReady || !selected.length;
  byId('combine-selected').disabled = busy || !runtimeReady || selected.length < 2;
  byId('remove-selected').disabled = busy || !selected.length;
  byId('select-complete').disabled = busy || !scans.size;
  byId('select-all').disabled = busy || !scans.size;
  byId('clear-storage').disabled = busy || (!scans.size && !results.size && !storageNotice);
  byId('covariance').disabled = busy || byId('mode').value !== 'correct' || byId('uncertainty').value === 'legacy-linear';
  byId('partial').disabled = busy || byId('mode').value !== 'correct';
  byId('validate').disabled ||= !CONFIG.validationURL;
  byId('example').disabled ||= !CONFIG.validationURL;
  byId('diagnostics').disabled ||= !CONFIG.testURL;
}
async function run(action) {
  if (busy) return;
  busy = true;
  controls();
  try { return await action(); }
  catch (error) {
    status(error.message || String(error), true);
    if (!byId('developer-tools').hidden) byId('report').textContent = String(error.stack || error);
  } finally { busy = false; controls(); }
}
function byteSize(value) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 ** 2).toFixed(1)} MiB`;
}
function storageSummary() {
  const inputBytes = [...scans.values()].reduce((sum, scan) => sum + scan.bytes.byteLength, 0);
  const outputBytes = [...results.values()].reduce((sum, result) => sum + result.files.reduce((n, file) => n + file.blob.size, 0), 0);
  const memoryOnly = [...scans.values(), ...results.values()].filter(item => !item.cached).length;
  const text = `${byteSize(inputBytes + outputBytes)} in this workspace · ${scans.size} input(s), ${results.size} conversion(s). `;
  byId('storage-summary').textContent = text + (storageNotice || (memoryOnly ? `${memoryOnly} item(s) are in memory only; download before closing this tab.` : 'Saved in this browser.'));
  byId('storage-summary').classList.toggle('storage-error', Boolean(storageNotice || memoryOnly));
}
function notePersistence(outcome) {
  if (!outcome.persisted) storageNotice = outcome.error || 'Browser storage is unavailable. Files remain in memory for this tab.';
  storageSummary();
  return outcome.persisted;
}
function temperature(scan) {
  const header = scan.record.header || {};
  if (Number.isFinite(header.temp_summary?.mean)) return { value: header.temp_summary.mean, measured: true };
  return { value: null, measured: false };
}
function acquisition(scan) {
  const h = scan.record.header || {};
  return h.complete === true ? 'Complete' : h.complete === false ? 'Partial' : 'Unknown (RAW)';
}
function renderScans() {
  const body = byId('scan-list');
  body.replaceChildren();
  if (!scans.size) {
    const row = document.createElement('tr');
    const cell = document.createElement('td'); cell.colSpan = 9; cell.className = 'empty';
    cell.textContent = 'Add files or fetch NCNR scans to get started.'; row.append(cell); body.append(row);
  }
  for (const scan of scans.values()) {
    const header = scan.record.header || {};
    const row = document.createElement('tr');
    const selectCell = document.createElement('td');
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = scan.selected;
    checkbox.setAttribute('aria-label', `Select ${scan.name}`); checkbox.dataset.scanId = scan.id;
    checkbox.onchange = () => { scan.selected = checkbox.checked; renderSelection(); };
    selectCell.append(checkbox);
    const nameCell = document.createElement('td'); nameCell.className = 'scan-name';
    const name = document.createElement('span'); name.className = 'file-name'; name.textContent = scan.name;
    const origin = document.createElement(scan.source?.contentUrl ? 'a' : 'span'); origin.className = 'source-tag';
    origin.textContent = scan.source?.contentUrl ? 'NCNR · verified source ↗' : `Local file · ${byteSize(scan.bytes.byteLength)}`;
    if (scan.source?.contentUrl) { origin.href = scan.source.contentUrl; origin.target = '_blank'; origin.rel = 'noopener noreferrer'; }
    nameCell.append(name, origin);
    const sampleCell = document.createElement('td'); sampleCell.className = 'sample'; sampleCell.textContent = header.title || 'No sample description';
    const tempCell = document.createElement('td'); const temp = temperature(scan); tempCell.className = 'temperature-cell';
    tempCell.textContent = temp.measured ? `${temp.value.toFixed(3)} K mean` : 'Unknown';
    if (Number.isFinite(header.temp_summary?.min) && Number.isFinite(header.temp_summary?.max)) {
      const range = document.createElement('span'); range.className = 'source-tag';
      range.textContent = `${header.temp_summary.min.toFixed(3)}–${header.temp_summary.max.toFixed(3)} K recorded`; tempCell.append(range);
    }
    const instrumentCell = document.createElement('td'); instrumentCell.className = 'instrument-cell';
    instrumentCell.textContent = header.monochromator || 'Not recorded';
    if (Number.isFinite(header.wavelength)) {
      const wavelength = document.createElement('span'); wavelength.className = 'source-tag'; wavelength.textContent = `λ ${header.wavelength} Å`; instrumentCell.append(wavelength);
    }
    const collimation = header.collimation_all || header.collimation;
    if (Array.isArray(collimation) && collimation.length) {
      const detail = document.createElement('span'); detail.className = 'source-tag'; detail.textContent = `Collimation ${collimation.join(' / ')} ′`; instrumentCell.append(detail);
    }
    const m4Cell = document.createElement('td'); m4Cell.className = 'm4-cell';
    if (Number.isFinite(header.a4_start) && Number.isFinite(header.a4_end)) {
      m4Cell.textContent = `${header.a4_start.toFixed(3)}° → ${header.a4_end.toFixed(3)}°`;
      const step = document.createElement('span'); step.className = 'source-tag';
      step.textContent = Number.isFinite(header.a4_step) ? `M4 step ${header.a4_step}°` : 'M4 step not recorded'; m4Cell.append(step);
      const label = document.createElement('span'); label.className = 'source-tag'; label.textContent = 'Planned motor range, not full 2θ'; m4Cell.append(label);
    } else { m4Cell.textContent = 'Not recorded'; }
    const dateCell = document.createElement('td'); dateCell.className = 'acquisition-date';
    dateCell.textContent = header.date || 'Acquisition date not recorded';
    const start = document.createElement('span'); start.className = 'source-tag'; start.textContent = 'Start time not recorded'; dateCell.append(start);
    if (header.terminal_timestamp) {
      const end = document.createElement('span'); end.className = 'source-tag'; end.textContent = `End: ${header.terminal_timestamp} (as recorded)`; dateCell.append(end);
    }
    const archiveCell = document.createElement('td'); archiveCell.className = 'archive-date';
    archiveCell.textContent = scan.source?.dateModified ? formatEasternDate(scan.source.dateModified) : 'Local input — no archive timestamp';
    const stateCell = document.createElement('td');
    const badge = document.createElement('span'); badge.className = `badge ${header.complete === false ? 'partial' : header.complete == null ? 'unknown' : ''}`;
    badge.textContent = acquisition(scan); stateCell.append(badge);
    if (header.planned_points != null) {
      const points = document.createElement('span'); points.className = 'source-tag'; points.textContent = `${header.recorded_points} / ${header.planned_points} points`; stateCell.append(points);
    }
    row.append(selectCell, nameCell, sampleCell, tempCell, instrumentCell, m4Cell, dateCell, archiveCell, stateCell); body.append(row);
  }
  byId('scan-count').textContent = `${scans.size} scan${scans.size === 1 ? '' : 's'}`;
  renderSuggestions(); renderSelection(); storageSummary();
}
function renderSuggestions() {
  const container = byId('combine-suggestions'); const list = byId('suggestion-list');
  container.hidden = scans.size === 0; list.replaceChildren();
  const groups = proposeGroups([...scans.values()]);
  byId('suggestion-empty').textContent = groups.length ? '' : 'No complete scan group currently satisfies the 0.2 K and matching-metadata checks. You can still review and combine compatible inputs manually.';
  for (const group of groups) {
    const card = document.createElement('article'); card.className = 'suggestion-card';
    const heading = document.createElement('h4'); heading.textContent = group.names.join(' + ');
    const temperature = document.createElement('p'); temperature.className = 'helper';
    temperature.textContent = `Measured scan means ${group.temperatureMin.toFixed(4)}–${group.temperatureMax.toFixed(4)} K; spread ${group.temperatureSpread.toFixed(4)} K (≤ 0.2 K).`;
    const review = document.createElement('p'); review.className = 'helper';
    review.textContent = 'Matching settings support this suggestion; confirm these files describe the same sample and measurement state.';
    card.append(heading, temperature, review);
    for (const warning of group.warnings || []) { const note = document.createElement('p'); note.className = 'warning'; note.textContent = warning; card.append(note); }
    if (group.reasons?.length) {
      const details = document.createElement('details'); const title = document.createElement('summary'); title.textContent = 'Matched metadata'; details.append(title);
      for (const reason of group.reasons) { const text = document.createElement('p'); text.textContent = reason; details.append(text); }
      card.append(details);
    }
    const toolbar = document.createElement('div'); toolbar.className = 'toolbar';
    const select = document.createElement('button'); select.textContent = 'Select suggested group';
    select.setAttribute('aria-label', `Select suggested group: ${group.names.join(', ')}`);
    select.onclick = () => {
      const ids = new Set(group.ids);
      for (const scan of scans.values()) scan.selected = ids.has(scan.id);
      renderScans();
      status(`Suggested group selected: ${group.names.join(', ')}. Review sample identity and use “Combine selected” when ready. No conversion has run.`);
    };
    toolbar.append(select); card.append(toolbar); list.append(card);
  }
}
function renderSelection() {
  const selected = selectedScans();
  byId('selection-summary').textContent = `${selected.length} of ${scans.size} scan(s) selected`;
  byId('convert').textContent = selected.length > 1 ? `Convert ${selected.length} separately` : selected.length === 1 ? 'Convert 1 scan' : 'Convert separately';
  byId('combine-selected').textContent = selected.length > 1 ? `Combine ${selected.length} selected scans` : 'Combine selected';
  byId('combine-selected').classList.toggle('primary', selected.length > 1);
  byId('convert').classList.toggle('primary', selected.length < 2);
  byId('select-all').checked = scans.size > 0 && selected.length === scans.size;
  byId('select-all').indeterminate = selected.length > 0 && selected.length < scans.size;
  byId('conversion-selection').textContent = selected.length ?
    `${selected.length} selected. For combination, ${selected[0].name} supplies the normalization monitor.` : 'Select input files above. Combined scans should represent the same sample and measurement state.';
  controls();
}
function renderResults() {
  for (const url of blobURLs.splice(0)) URL.revokeObjectURL(url);
  const container = byId('results'); container.replaceChildren();
  if (!results.size) { const p = document.createElement('p'); p.className = 'empty'; p.textContent = 'Your completed conversions will appear here.'; container.append(p); }
  for (const result of [...results.values()].sort((a, b) => b.created.localeCompare(a.created))) {
    const article = document.createElement('article'); article.className = 'result';
    const head = document.createElement('div'); head.className = 'result-head';
    const titleBlock = document.createElement('div'); const title = document.createElement('h3');
    title.textContent = result.options.combine ? `${result.options.combineName} · combined ${result.inputs.length} scans` : result.inputs.map(input => input.name).join(', ');
    const date = document.createElement('p'); date.className = 'result-summary';
    date.textContent = `${result.options.mode} · ${formatEasternDate(result.created)}${result.cached ? ' · saved locally' : ' · memory only'}`;
    titleBlock.append(title, date);
    if (result.inputs.some(input => input.complete === false)) {
      const partial = document.createElement('span'); partial.className = 'badge partial'; partial.textContent = 'Partial snapshot'; titleBlock.append(partial);
      const explanation = document.createElement('p'); explanation.className = 'warning'; explanation.textContent = 'Contains an incomplete acquisition prefix; this is not a complete scan.'; titleBlock.append(explanation);
    }
    const remove = document.createElement('button'); remove.className = 'ghost danger'; remove.textContent = 'Remove';
    remove.setAttribute('aria-label', `Remove conversion from ${formatEasternDate(result.created)}`);
    remove.onclick = () => run(async () => { const outcome = await storage.removeResult(result.id); if (!outcome.persisted && result.cached) throw new Error(outcome.error); results.delete(result.id); renderResults(); storageSummary(); });
    head.append(titleBlock, remove);
    const summary = document.createElement('p'); summary.className = 'result-summary';
    summary.textContent = result.summaries.map(item => `${item.name}: ${item.valid_points} valid points, ${item.gsas_banks} uniform bank(s)`).join(' · ');
    article.append(head, summary);
    if (result.options.combine) {
      const provenance = document.createElement('p'); provenance.className = 'result-inputs';
      provenance.textContent = `Combined inputs: ${result.inputs.map(input => input.name).join(' + ')}`;
      const normalization = document.createElement('p'); normalization.className = 'result-summary';
      const first = result.inputs[0];
      const monitor = Number.isFinite(first?.monitor) ? ` (${first.monitor})` : '';
      normalization.textContent = `Normalized to ${first?.name || 'the first input'}’s monitor${monitor}. The combined intensity is a normalized inverse-variance estimate, not a sum of raw counts. Its scale can resemble the first input; uncertainties reflect all contributing scans.`;
      article.append(provenance, normalization);
    }
    const priority = name => /\.gsas$/i.test(name) ? 0 : /\.dat$/i.test(name) ? 1 : /\.csv$/i.test(name) ? 2 : /\.json$/i.test(name) ? 3 : /\.npz$/i.test(name) ? 4 : 5;
    const ordered = [...result.files].sort((a, b) => priority(a.name) - priority(b.name) || a.name.localeCompare(b.name));
    const primary = ordered.filter(file => !/\.raw$/i.test(file.name));
    const prepared = ordered.filter(file => /\.raw$/i.test(file.name));
    function downloadLinks(files) {
      const downloads = document.createElement('div'); downloads.className = 'downloads';
      for (const file of files) {
        const url = URL.createObjectURL(file.blob); blobURLs.push(url);
        const link = document.createElement('a'); link.href = url; link.download = file.name;
        link.textContent = /\.dat$/i.test(file.name) ? `${file.name} · FullProf` : file.name;
        if (/\.(gsas|dat)$/i.test(file.name)) link.className = 'pattern-download';
        link.title = `Download ${file.name} (${byteSize(file.blob.size)})`; downloads.append(link);
      }
      return downloads;
    }
    const primaryHeading = document.createElement('p'); primaryHeading.className = 'download-heading';
    primaryHeading.textContent = result.options.combine ? 'Combined pattern & metadata' : 'Reduced patterns & metadata';
    article.append(primaryHeading, downloadLinks(primary));
    for (const item of result.summaries) {
      const fullprof = item.fullprof;
      if (!fullprof || typeof fullprof !== 'object') continue;
      const note = document.createElement('p'); note.className = 'fullprof-note';
      const format = fullprof.format || 'FullProf';
      const instrument = Number.isFinite(fullprof.instrument_code) ? `INSTRM = ${fullprof.instrument_code}` : 'INSTRM not recorded';
      const interpolation = fullprof.interpolation === 0 ? 'Interpol = 0 (no interpolation; INTER 1 1 0 0)'  : Number.isFinite(fullprof.interpolation) ? `Interpol = ${fullprof.interpolation}` : 'interpolation setting not recorded';
      const points = Number.isSafeInteger(fullprof.points) ? `${fullprof.points} points` : 'point count not recorded';
      const omitted = Number.isSafeInteger(fullprof.omitted_invalid_points) ? `${fullprof.omitted_invalid_points} invalid bins omitted` : 'omitted-bin count not recorded';
      note.textContent = `${fullprof.filename || item.name}: ${format}, ${instrument}. Columns: 2θ (degrees), intensity, sigma. ${points}; ${omitted}. Explicit angles preserve gap positions; ${interpolation}.`;
      article.append(note);
    }
    if (prepared.length) {
      const details = document.createElement('details'); details.className = 'prepared-downloads';
      const heading = document.createElement('summary'); heading.textContent = result.options.combine ? 'Prepared input files (not combined)' : 'Prepared input files';
      const explanation = document.createElement('p'); explanation.className = 'result-summary';
      explanation.textContent = result.options.combine ? 'Each RAW file contains one individual prepared scan. Use the combined pattern files above for the combined result.' : 'Each RAW file contains one prepared input before detector-bank reduction.';
      details.append(heading, explanation, downloadLinks(prepared)); article.append(details);
    }
    const warnings = [...new Set(result.summaries.flatMap(item => item.warnings || []))];
    if (warnings.length) {
      const details = document.createElement('details'); details.className = 'warning';
      const heading = document.createElement('summary'); heading.textContent = `${warnings.length} metadata note(s)`;
      details.append(heading);
      for (const warning of warnings) { const p = document.createElement('p'); p.textContent = warning; details.append(p); }
      article.append(details);
    }
    container.append(article);
  }
  byId('result-count').textContent = `${results.size} conversion${results.size === 1 ? '' : 's'}`;
  storageSummary(); controls();
}
function safeName(name) {
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*\.(bt1|raw)$/i.test(name)) throw new Error(`Unsupported filename: ${name}. Use letters, digits, _, . or - and a .bt1 or .raw extension.`);
  return name.toLowerCase();
}
async function sha256(bytes) {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(value => value.toString(16).padStart(2, '0')).join('');
}
async function addInputs(items, kind) {
  const batchNames = new Set();
  for (const item of items) {
    const id = safeName(item.file.name);
    const stem = id.replace(/\.(bt1|raw)$/, '');
    if (batchNames.has(stem)) throw new Error(`Duplicate input basename: ${item.file.name}. Each scan needs a unique basename.`);
    batchNames.add(stem);
    const conflict = [...scans.values()].find(scan => scan.id.replace(/\.(bt1|raw)$/, '') === stem && scan.id !== id);
    if (conflict) throw new Error(`${item.file.name} conflicts with ${conflict.name}. Remove or rename the existing input first.`);
  }
  status('Inspecting acquisition metadata in the browser…');
  const inspected = await request('inspect', { files: items.map(item => item.file) });
  if (!Array.isArray(inspected.records) || inspected.records.length !== items.length) throw new Error('Inspection did not return a record for every input.');
  const records = new Map(inspected.records.map(record => [String(record.path).split('/').pop(), record]));
  const additions = [];
  for (const item of items) {
    const id = safeName(item.file.name); const hash = await sha256(item.file.bytes);
    const record = records.get(item.file.name);
    if (!record || record.sha256 !== hash) throw new Error(`Input hash changed while inspecting ${item.file.name}.`);
    const existing = scans.get(id);
    if (existing && existing.sha256 !== hash && !(kind === 'ncnr' && existing.source?.contentUrl === item.source?.contentUrl)) {
      throw new Error(`${item.file.name} is already loaded with different contents. Remove that input or give the new file a unique name.`);
    }
    additions.push(existing && existing.sha256 === hash ? existing : {
      id, name: item.file.name, bytes: item.file.bytes, sha256: hash, record,
      source: item.source || null, added: new Date().toISOString(), kind,
      selected: kind === 'local' && record.header.complete !== false, cached: false,
    });
  }
  for (const scan of additions) {
    scans.set(scan.id, scan);
    scan.cached = notePersistence(await storage.putScan({ ...scan, selected: false }));
  }
  renderScans();
  return additions;
}
async function fetchNCNR() {
  const fetched = await fetchLatestScans({ onStatus: message => { status(message); byId('ncnr-summary').textContent = message; } });
  const added = await addInputs(fetched.items, 'ncnr');
  for (const scan of scans.values()) scan.selected = false;
  const latestComplete = added.find(scan => scan.record.header.complete === true);
  if (latestComplete) latestComplete.selected = true;
  renderScans();
  const partial = added.filter(scan => scan.record.header.complete === false).length;
  byId('ncnr-summary').textContent = `${added.length} Mn5Si₂ scans loaded directly from NCNR; all source hashes verified. ` +
    (latestComplete ? `${latestComplete.name} is the latest complete scan and is selected. ` : 'No complete scan in this experiment. ') +
    (partial ? `${partial} partial scan(s) remain unselected.` : '');
  status('NCNR scans are ready. Review the selection and conversion settings.');
  return { added, fetched, latestComplete };
}
async function convertSelection(combine = false) {
  const selected = selectedScans();
  if (!selected.length) throw new Error('Select at least one input scan.');
  const options = currentOptions(combine);
  if (combine && selected.length < 2) throw new Error('Select at least two scans to combine.');
  if (combine && !/^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(options.combineName)) throw new Error('The combined name must start with a letter or digit and contain only letters, digits, _, . or -.');
  if (selected.some(scan => scan.record.header.complete === false) && (options.mode !== 'correct' || !options.partial)) {
    throw new Error('The selection includes a partial acquisition. Choose correct mode and explicitly allow incomplete prefixes, or deselect the partial scan.');
  }
  const measured = selected.map(temperature).filter(value => value.measured).map(value => value.value);
  if (combine && measured.length > 1 && Math.max(...measured) - Math.min(...measured) > 1 && !options.allowMixedTemperature) {
    throw new Error('Selected scans differ by more than 1 K. Convert separately, or explicitly acknowledge mixed temperatures under “Combining scans”.');
  }
  status(combine ? 'Preparing each selected scan, then combining in the browser…' : 'Converting each selected scan in the browser…');
  const converted = await request('convert', { files: selected.map(scan => ({ name: scan.name, bytes: scan.bytes })), options });
  const result = {
    id: crypto.randomUUID(), created: new Date().toISOString(), options,
    inputs: selected.map(scan => ({ name: scan.name, sha256: scan.sha256, source: scan.source, temperature: temperature(scan).value, monitor: scan.record.header.monitor, complete: scan.record.header.complete })),
    summaries: converted.summaries,
    files: converted.downloads.map(file => ({ name: file.name, blob: new Blob([file.bytes], { type: 'application/octet-stream' }) })), cached: false,
  };
  results.set(result.id, result);
  result.cached = notePersistence(await storage.putResult(result));
  renderResults();
  byId('conversion-summary').textContent = `${selected.length} scan(s) ${combine ? 'combined' : 'converted separately'}; ${result.files.length} downloads ready.`;
  status(`Conversion complete. ${result.files.length} output files are ready${result.cached ? ' and saved in this browser' : ' in memory'}.`);
  await evidence('upload', {
    kind: selected.some(scan => scan.kind === 'ncnr') ? 'ncnr-direct' : 'local-browser',
    listing_url: selected.some(scan => scan.kind === 'ncnr') ? LISTING : null,
    sources: result.inputs, options, inspection: { records: selected.map(scan => scan.record) },
    conversion: result.summaries, output_files: result.files.map(file => ({ name: file.name, bytes: file.blob.size })),
  });
  return converted;
}

// Help stays available while computation runs. Tooltip text is associated with
// a focusable button, supports pointer/touch/keyboard use, and escapes table clipping.
let activeTooltip = null;
let tooltipTimer;
function closeTooltip() {
  clearTimeout(tooltipTimer);
  if (activeTooltip) activeTooltip.tip.hidden = true;
  activeTooltip = null;
}
function showTooltip(button, tip, pinned = false) {
  clearTimeout(tooltipTimer);
  if (activeTooltip?.tip !== tip) closeTooltip();
  activeTooltip = { button, tip, pinned: pinned || Boolean(activeTooltip?.tip === tip && activeTooltip.pinned) };
  tip.hidden = false;
  const bounds = button.getBoundingClientRect();
  const box = tip.getBoundingClientRect();
  const left = Math.max(12, Math.min(bounds.left, innerWidth - box.width - 12));
  const below = bounds.bottom + 8;
  const top = below + box.height <= innerHeight - 12 ? below : Math.max(12, bounds.top - box.height - 8);
  tip.style.left = `${left}px`; tip.style.top = `${top}px`;
}
function helpButton(label, description) {
  const button = document.createElement('button'); button.type = 'button'; button.className = 'help-tip'; button.dataset.help = '';
  button.textContent = '?'; button.setAttribute('aria-label', `About ${label}`);
  const tip = document.createElement('span'); tip.className = 'help-tooltip'; tip.setAttribute('role', 'tooltip'); tip.hidden = true;
  tip.id = `help-${document.querySelectorAll('[role="tooltip"]').length + 1}`;
  tip.textContent = description; button.setAttribute('aria-describedby', tip.id); document.body.append(tip);
  button.addEventListener('focus', () => showTooltip(button, tip));
  button.addEventListener('pointerenter', () => showTooltip(button, tip));
  button.addEventListener('click', event => { event.preventDefault(); showTooltip(button, tip, true); });
  const scheduleClose = () => {
    clearTimeout(tooltipTimer);
    tooltipTimer = setTimeout(() => {
      if (!activeTooltip?.pinned && document.activeElement !== button && !button.matches(':hover') && !tip.matches(':hover') && activeTooltip?.tip === tip) closeTooltip();
    }, 160);
  };
  button.addEventListener('blur', () => { if (activeTooltip?.button === button) activeTooltip.pinned = false; scheduleClose(); }); button.addEventListener('pointerleave', scheduleClose);
  tip.addEventListener('pointerenter', () => clearTimeout(tooltipTimer)); tip.addEventListener('pointerleave', scheduleClose);
  return button;
}
function setupHelpAndTabs() {
  const tabs = [byId('tab-workspace'), byId('tab-documentation')];
  function selectTab(tab, focus = false, updateURL = true) {
    closeTooltip();
    for (const candidate of tabs) {
      const selected = candidate === tab;
      candidate.setAttribute('aria-selected', String(selected)); candidate.tabIndex = selected ? 0 : -1;
      byId(candidate.getAttribute('aria-controls')).hidden = !selected;
    }
    if (focus) tab.focus();
    if (updateURL) history.replaceState(null, '', `${location.pathname}${location.search}#${tab === tabs[1] ? 'documentation' : 'workspace'}`);
  }
  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener('click', () => selectTab(tab));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); selectTab(tabs[next], true); }
    });
  }
  selectTab(location.hash === '#documentation' ? tabs[1] : tabs[0], false, false);
  window.addEventListener('hashchange', () => {
    if (location.hash === '#documentation' || location.hash === '#workspace') selectTab(location.hash === '#documentation' ? tabs[1] : tabs[0], false, false);
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && activeTooltip) { event.preventDefault(); closeTooltip(); } });
  document.addEventListener('pointerdown', event => { if (activeTooltip && !activeTooltip.button.contains(event.target) && !activeTooltip.tip.contains(event.target)) closeTooltip(); });
  window.addEventListener('resize', closeTooltip);
  window.addEventListener('scroll', closeTooltip, true);
  const headers = document.querySelectorAll('#scan-list').item(0).closest('table').querySelectorAll('thead th');
  const columnHelp = [
    [2, 'sample description', 'The original acquisition description is retained. Similar temperatures or matching settings do not prove that scans contain the same sample or measurement state.'],
    [3, 'measured temperature', 'Mean and minimum–maximum of the recorded temperature measurements, in kelvin. These are not the nominal temperature in the sample description or the temperature setpoint.'],
    [4, 'monochromator', 'The recorded monochromator, wavelength λ in ångströms and collimation in arcminutes. Matching detector corrections does not validate an instrument resolution profile.'],
    [5, 'planned M4 range', 'Planned motor start → stop and step, in degrees. M4 is not the full multi-detector 2θ range or the final GSAS range. A partial scan may cover only a prefix of this plan.'],
    [6, 'acquisition date and time', 'Header date and terminal end marker are shown as recorded, with no established timezone. These files do not record a precise start time; archive updates are never used to infer one.'],
    [7, 'archive update time', 'When the archive reports this file was last modified, displayed in US Eastern time. This is a separate event from acquisition start or end. Local inputs have no archive timestamp.'],
    [8, 'acquisition completeness', 'Complete means all planned acquisition points are present. Partial means an incomplete recorded prefix. Prepared RAW files cannot establish acquisition completeness.'],
  ];
  for (const [index, label, text] of columnHelp) headers[index].append(helpButton(label, text));
  const controlHelp = [
    ['mode', 'calculation mode', 'Correct uses float64 and enclosing measured intervals. Legacy preserves historical float32 arithmetic, snapping, masks and endpoints, then writes integer GSAS values. Both run in Python and NumPy.'],
    ['uncertainty', 'uncertainty policy', 'Independent propagation uses squared interpolation weights for variances. Legacy-linear interpolates sigma. This choice is separate from calculation mode; changing it does not change geometry or the float32/float64 precision.'],
    ['method', 'sampling method', 'Interpolate uses eligible neighbouring observations. Nearest assigns measured observations to nearest grid bins; overlaps can still be combined. Endpoint and mask rules depend on the selected mode.'],
    ['covariance', 'conditional covariance', 'Correct + independent only. The NPZ factor describes correlations from shared observations with observed variances and averaging weights held fixed. GSAS fitting does not automatically use it; monitor and calibration uncertainties are not included.'],
    ['partial', 'partial acquisition', 'Opt in only to a valid incomplete acquisition prefix in correct mode. Missing interior rows are not invented, and outputs remain labelled Partial snapshot. Legacy requires complete BT-1 scans.'],
    ['combine-name', 'combined output name', 'Names the combined output files. Start with a letter or digit and use only letters, digits, underscores, dots or hyphens. Inputs are prepared separately before their estimates are combined.'],
    ['allow-mixed-temperature', 'mixed-temperature acknowledgement', 'The converter normally refuses mean temperatures differing by more than 1 K. Check this only after reviewing sample/state compatibility. Instrument checks remain active. This is separate from the tighter 0.2 K suggestion rule.'],
  ];
  for (const [id, label, text] of controlHelp) {
    const control = byId(id); const original = control.closest('label');
    const wrapper = document.createElement('div'); wrapper.className = original.classList.contains('check') ? 'help-check' : 'help-field';
    original.parentNode.insertBefore(wrapper, original); wrapper.append(original, helpButton(label, text));
  }
  byId('combine-suggestions').querySelector('h3').append(helpButton('suggested combinations', 'Complete scans with matching recorded settings and detector calibration are grouped only when the full spread of measured scan means is at most 0.2 K. Review sample identity and within-scan drift. Selecting a suggestion never converts.'));
  byId('results-heading').append(helpButton('output files', 'GSAS and FullProf DAT are pattern exports. FullProf XYDATA uses INSTRM = 10; its INTER 1 1 0 0 header sets Interpol = 0 (no interpolation), with explicit angle, intensity and sigma columns; invalid bins are omitted and counted. CSV retains pre-formatting values; JSON records provenance, masks and policies. BT-1 inputs also produce prepared RAW. Correct independent mode can additionally export conditional covariance as NPZ.'));
}
setupHelpAndTabs();

// Data-specific partial/mixed-temperature acknowledgements reset on page load.
const prefFields = ['mode', 'uncertainty', 'method', 'covariance', 'combine-name'];
function reconcileOptions() {
  if (byId('mode').value !== 'correct') { byId('partial').checked = false; byId('covariance').checked = false; }
  if (byId('uncertainty').value === 'legacy-linear') byId('covariance').checked = false;
  byId('mode-description').textContent = byId('mode').value === 'correct' ?
    'Correct mode uses float64 and defaults to independent variance propagation. Fractional GSAS values are retained where representable.' :
    'Legacy mode uses the historical float32 operation order and defaults to linear sigma interpolation. GSAS output uses historical integer rounding.';
  controls();
}
const preferences = loadPreferences();
for (const id of prefFields) {
  const element = byId(id); const saved = preferences[id];
  if (element.type === 'checkbox' && typeof saved === 'boolean') element.checked = saved;
  else if (typeof saved === 'string' && (element.tagName !== 'SELECT' || [...element.options].some(option => option.value === saved))) element.value = saved;
  element.addEventListener('change', () => {
    reconcileOptions();
    savePreferences(Object.fromEntries(prefFields.map(key => { const field = byId(key); return [key, field.type === 'checkbox' ? field.checked : field.value]; })));
  });
}
reconcileOptions();
byId('files').onchange = () => run(async () => {
  try {
    const files = [...byId('files').files];
    if (!files.length) return;
    await addInputs(await Promise.all(files.map(async file => ({ file: { name: file.name, bytes: await file.arrayBuffer() } }))), 'local');
    status(`${files.length} local input(s) inspected. Review their metadata before conversion.`);
  } finally { byId('files').value = ''; }
});
byId('ncnr-load').onclick = () => run(fetchNCNR);
byId('quick-convert').onclick = () => run(async () => {
  const count = selectedScans().length;
  if (count > 1) {
    status(`You have ${count} scans selected. Use “Combine ${count} selected scans” or “Convert ${count} separately” below. The quick action converts one latest complete scan; your selection and results have been preserved.`, true);
    return;
  }
  const loaded = await fetchNCNR();
  if (!loaded.latestComplete) throw new Error('No complete scan is available in this experiment. Review the partial scans explicitly.');
  await convertSelection(false);
});
byId('convert').onclick = () => run(() => convertSelection(false));
byId('combine-selected').onclick = () => run(() => convertSelection(true));
byId('select-all').onchange = event => { for (const scan of scans.values()) scan.selected = event.target.checked; renderScans(); };
byId('select-complete').onclick = () => { for (const scan of scans.values()) scan.selected = scan.record.header.complete === true; renderScans(); };
byId('remove-selected').onclick = () => run(async () => {
  const selected = selectedScans(); const outcome = await storage.removeScans(selected.map(scan => scan.id));
  if (!outcome.persisted && selected.some(scan => scan.cached)) throw new Error(outcome.error);
  for (const scan of selected) scans.delete(scan.id);
  renderScans(); status(`${selected.length} input(s) removed from this browser workspace. Saved conversion results remain available.`);
});
byId('clear-storage').onclick = () => run(async () => {
  const outcome = await storage.clear();
  if (!outcome.persisted && [...scans.values(), ...results.values()].some(item => item.cached)) throw new Error(outcome.error);
  scans.clear(); results.clear(); storageNotice = outcome.persisted ? '' : outcome.error;
  renderScans(); renderResults(); byId('conversion-summary').textContent = '';
  status(outcome.persisted ? 'Saved inputs and results cleared from this browser workspace. Original source files are unaffected.' : `Inputs and results cleared from this tab, but browser storage could not be cleared: ${outcome.error}`, !outcome.persisted);
});
const developerMode = Boolean(CONFIG.validationURL || CONFIG.testURL || CONFIG.evidenceURL);
byId('developer-tools').hidden = !developerMode;
byId('validate').onclick = () => run(async () => {
  byId('report-summary').textContent = 'Running native/browser comparisons…';
  const report = await request('validate');
  byId('report').textContent = JSON.stringify(report, null, 2);
  byId('report-summary').textContent = report.passed ? 'Browser validation passed.' : 'Validation completed with differences; inspect the report.';
  byId('report-summary').className = report.passed ? 'pass' : 'fail';
  await evidence('validation', report); status('Browser validation completed.');
});
byId('probe').onclick = () => run(async () => { runtimeProbe = await request('probe'); byId('report').textContent = JSON.stringify(runtimeProbe, null, 2); await evidence('filesystem', runtimeProbe); status('Browser filesystem check completed.'); });
byId('diagnostics').onclick = () => run(async () => { const report = await request('diagnostics'); byId('report').textContent = JSON.stringify(report, null, 2); await evidence('portable', report); status('Numerical diagnostics completed.'); });
byId('example').onclick = () => run(async () => {
  const base = new URL(CONFIG.validationURL, location.href);
  const manifest = await (await fetch(new URL('manifest.json', base))).json();
  const item = manifest.inputs.find(input => input.name === 'controlled.raw');
  const bytes = await (await fetch(new URL(item.url, base))).arrayBuffer();
  const added = await addInputs([{ file: { name: item.name, bytes } }], 'local');
  for (const scan of scans.values()) scan.selected = false;
  added[0].selected = true; renderScans(); await convertSelection(false);
});

(async () => {
  try {
    storage = await openStorage();
    const cached = await storage.load();
    storageNotice = storage.error || '';
    for (const scan of cached.scans) scans.set(scan.id, { ...scan, selected: false, cached: true });
    for (const result of cached.results) results.set(result.id, { ...result, cached: true });
    const complete = [...scans.values()].filter(scan => scan.record.header.complete === true).sort((a, b) => Date.parse(b.source?.dateModified || b.added) - Date.parse(a.source?.dateModified || a.added));
    if (complete[0]) complete[0].selected = true;
    renderScans(); renderResults();
    runtimeProbe = await request('probe'); runtimeReady = true;
    status(`Ready. Python and NumPy run in this browser${scans.size ? `; ${scans.size} saved input(s) restored` : ''}.`);
    if (CONFIG.evidenceURL) await evidence('filesystem', runtimeProbe);
  } catch (error) { status(`Startup failed: ${error.message}`, true); }
  finally { busy = false; controls(); }
})();
