import { CONFIG } from './runtime-config.mjs';

const assetURL = (base, name = '') => new URL(name, new URL(base, import.meta.url)).href;
const sourceHashes = {};
const sha256 = async bytes => [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(v => v.toString(16).padStart(2, '0')).join('');

const status = (message) => postMessage({ type: 'status', message });
const fetchOK = async (url) => {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response;
};
const ready = (async () => {
  status('Loading local Pyodide and NumPy…');
  const { loadPyodide } = await import(assetURL(CONFIG.runtimeURL, 'pyodide.mjs'));
  const py = await loadPyodide({ indexURL: assetURL(CONFIG.runtimeURL) });
  globalThis.pyodide = py;
  await py.loadPackage('numpy');
  py.FS.mkdirTree('/app/bt1modern');
  py.FS.mkdirTree('/app/browser');
  for (const name of ['__init__', '__main__', 'cli', 'io', 'reduce', 'legacy', 'gsas']) {
    const bytes = await (await fetchOK(assetURL(CONFIG.packageURL, `${name}.py`))).arrayBuffer();
    sourceHashes[`bt1modern/${name}.py`] = await sha256(bytes);
    py.FS.writeFile(`/app/bt1modern/${name}.py`, new Uint8Array(bytes));
  }
  py.runPython('import sys; sys.path.insert(0, "/app")');
  return py;
})();

async function loadPython(py, name) {
  py.FS.writeFile(`/app/browser/${name}.py`, await (await fetchOK(assetURL(CONFIG.browserPythonURL, `${name}.py`))).text());
}

async function handle({ action, files, options }) {
  const py = await ready;
  if (action === 'probe') {
    return JSON.parse(py.runPython(`
import json, os, sys, tempfile
from pathlib import Path
import numpy as np
def filesystem_probe():
    report = {"pyodide": "314.0.7", "python": sys.version, "numpy": np.__version__, "platform": sys.platform}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source, target = root/'source', root/'target'
        source.write_bytes(b'new')
        try:
            os.link(source, target)
            report['os_link'] = {'supported': True, 'bytes': target.read_text()}
        except Exception as error:
            report['os_link'] = {'supported': False, 'error': repr(error)}
        target.write_bytes(b'old')
        os.replace(source, target)
        report['os_replace'] = target.read_bytes() == b'new' and not source.exists()
        alias = root/'alias'
        alias.symlink_to(target)
        report['symlink'] = alias.resolve() == target
    return report
json.dumps(filesystem_probe())
`));
  }
  if (action === 'diagnostics') {
    if (!CONFIG.validationURL) throw new Error('Diagnostics are available in the local test harness.');
    await loadPython(py, 'diagnostics');
    py.FS.mkdirTree('/bt1-diagnostics');
    py.FS.writeFile('/bt1-diagnostics/controlled.raw', new Uint8Array(await (await fetchOK(assetURL(CONFIG.validationURL, 'inputs/controlled.raw'))).arrayBuffer()));
    return JSON.parse(py.runPython(`
import json
from browser.diagnostics import diagnose_sigma
json.dumps(diagnose_sigma('/bt1-diagnostics/controlled.raw'), allow_nan=False)
`));
  }
  await loadPython(py, 'adapter');
  if (action === 'convert' || action === 'inspect') {
    if (!files?.length) throw new Error('Choose at least one BT-1 or RAW input.');
    const job = `/bt1-upload/${crypto.randomUUID()}`;
    py.FS.mkdirTree(`${job}/inputs`);
    const names = new Set();
    for (const file of files) {
      if (!/^[^/\\\u0000]+\.(bt1|raw)$/i.test(file.name) || file.name.startsWith('.')) throw new Error('Input names must be plain .bt1 or .raw filenames.');
      if (names.has(file.name)) throw new Error(`Duplicate input filename: ${file.name}`);
      names.add(file.name);
      py.FS.writeFile(`${job}/inputs/${file.name}`, new Uint8Array(file.bytes));
    }
    if (action === 'inspect') {
      py.globals.set('browser_inspect_argv', JSON.stringify(['inspect', ...files.map(f => `${job}/inputs/${f.name}`)]));
      const result = JSON.parse(py.runPython(`
import contextlib, io, json
from bt1modern import cli
out, err = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    code = cli.main(json.loads(browser_inspect_argv))
json.dumps({'code': code, 'stdout': out.getvalue(), 'stderr': err.getvalue()})
`));
      if (result.code !== 0) throw new Error(result.stderr || result.stdout);
      return { records: JSON.parse(result.stdout) };
    }
    const args = ['convert', ...files.map(f => `${job}/inputs/${f.name}`), '--out-dir', `${job}/outputs`, '--mode', options.mode, '--method', options.method];
    if (options.uncertainty !== 'default') args.push('--uncertainty', options.uncertainty);
    if (options.covariance) args.push('--covariance');
    if (options.combine) args.push('--combine', options.combineName || 'combined');
    if (options.partial) args.push('--allow-partial');
    if (options.allowMixedTemperature) args.push('--allow-mixed-temperature');
    py.globals.set('browser_argv', JSON.stringify(args));
    const result = JSON.parse(py.runPython(`
import contextlib, io, json
from bt1modern import cli
from browser.adapter import publication_context
out, err = io.StringIO(), io.StringIO()
with publication_context(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    code = cli.main(json.loads(browser_argv))
json.dumps({'code': code, 'stdout': out.getvalue(), 'stderr': err.getvalue()})
`));
    if (result.code !== 0) throw new Error(result.stderr || result.stdout);
    const downloads = py.FS.readdir(`${job}/outputs`).filter(n => n !== '.' && n !== '..').map(name => ({ name, bytes: py.FS.readFile(`${job}/outputs/${name}`) }));
    return { ...result, summaries: JSON.parse(result.stdout), downloads };
  }
  if (action === 'validate') {
    if (!CONFIG.validationURL || !CONFIG.testURL) throw new Error('Validation fixtures are available in the local test harness.');
    status('Loading native comparison fixtures…');
    await loadPython(py, 'validation');
    const base = assetURL(CONFIG.validationURL);
    const manifest = await (await fetchOK(`${base}manifest.json`)).json();
    for (const [name, expected] of Object.entries(manifest.source_sha256)) {
      if (sourceHashes[name] !== expected) throw new Error(`Converter source differs from the native baseline: ${name}. Regenerate the native baseline before comparing.`);
    }
    const validationRoot = `/bt1-validation/${crypto.randomUUID()}`;
    py.FS.mkdirTree(`${validationRoot}/inputs`);
    for (const input of manifest.inputs) {
      const name = typeof input === 'string' ? input : input.name;
      const bytes = await (await fetchOK(`${base}inputs/${encodeURIComponent(name)}`)).arrayBuffer();
      if (typeof input !== 'string' && await sha256(bytes) !== input.sha256) throw new Error(`Validation fixture hash mismatch: ${name}`);
      py.FS.writeFile(`${validationRoot}/inputs/${name}`, new Uint8Array(bytes));
    }
    py.FS.mkdirTree('/app/tests');
    for (const name of ['test_cli_python_modes', 'test_cli_safety', 'test_gsas', 'test_io', 'test_legacy_kernel', 'test_native_serialization', 'test_python_legacy_io', 'tests_reduce']) {
      py.FS.writeFile(`/app/tests/${name}.py`, await (await fetchOK(assetURL(CONFIG.testURL, `${name}.py`))).text());
    }
    py.globals.set('validation_root', validationRoot);
    py.globals.set('native_baseline_json', await (await fetchOK(`${base}native-baseline.json`)).text());
    status('Running conversions, safety checks, and exact native comparisons…');
    return JSON.parse(py.runPython(`
import json
from browser.validation import run_validation, run_portable_tests
from browser.adapter import publication_context
report = run_validation(validation_root, expected=json.loads(native_baseline_json), publication_context=publication_context)
report['portable_tests'] = run_portable_tests('/app/tests', publication_context=publication_context)
report['passed'] = report['passed'] and report['portable_tests']['passed']
json.dumps(report, allow_nan=False)
`));
  }
  throw new Error(`Unknown action: ${action}`);
}

// A single queue owns MEMFS; conversion/publication never yields to another job.
let queue = Promise.resolve();
self.onmessage = ({ data }) => {
  queue = queue.then(async () => {
    try { postMessage({ id: data.id, type: 'result', result: await handle(data) }); }
    catch (error) { postMessage({ id: data.id, type: 'error', error: String(error.stack || error) }); }
  });
};
