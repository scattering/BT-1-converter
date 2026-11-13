# BT-1 reduction / Pyodide handoff

Updated 2026-11-12 (US Eastern).

## Starting point

Work in `/Users/williamratcliff/bt1-converter`, the existing local Codex project,
not a new rewrite. It contains the validated v0.3.0 pure-Python port of
PROPREP/GFORMAT. Read `AGENTS.md`, `README.md`, `PYTHON_MODES.md`, and
`LOCAL_SETUP.md` before changing it. `SOURCE_PROVENANCE.json` records imported
source hashes. Current baseline commit: `5f0e366` (relocation to user home).
The former `/Users/williamratcliff/Documents/Codex/bt1-converter` location is
obsolete. The local environment was recreated after the move.

Reference release:
`/Users/williamratcliff/Documents/Codex/2026-11-03/task-4/bt1-modern-v0.3.0`.
Review archive:
`/Users/williamratcliff/Downloads/bt1-proprep-gformat-python-v0.3.0-review.zip`.
Do not modify those source archives or original acquisition files.

## What is verified

- Runtime is Python >=3.10 and NumPy >=1.24 only. Fortran is an independent
  regression oracle, not a runtime backend or fallback.
- `correct` is the default: float64, enclosing measured interval, independently
  propagated variances using squared interpolation weights, fractional GSAS
  values, and optional conditional covariance factor export.
- `legacy` preserves historical float32 operation boundaries, snapping/masks,
  linear sigma interpolation by default, and integer GSAS serialization.
  Uncertainty policy is separately selectable. Do not reorder legacy arithmetic.
- Both modes default to a uniform GSAS CONST ESD bank with explicit zero-weight
  gaps and blank padding. Preserve metadata and source/output protections.
- Latest native baseline: **126 tests passed, no skips**, including external
  fixture/Fortran checks. Log: `/tmp/bt1-pyodide-native-baseline.log` (temporary).
  Existing durable evidence also lives in `artifacts/local-project-test-full.log`
  and the validation documentation. Native success is not a Pyodide result.

Repeat the native baseline:

```sh
cd /Users/williamratcliff/bt1-converter
make test-full PYTHON=.venv/bin/python
```

The external fixture and oracle paths are documented in `LOCAL_SETUP.md` and
configured in `Makefile`. The local `.venv` uses system-site-packages, so it is
not a frozen dependency lock.

## Installed Pyodide and actual browser outcome

The earlier execution-network download blocker is **superseded**. The official
GitHub download worked in this session, without bypassing a policy denial.
Pyodide **314.0.7**, Python **3.14.2**, and NumPy **2.4.6** are installed at
`artifacts/pyodide-runtime/314.0.7/pyodide/`. The installer pins/verifies the
official core archive digest and NumPy's matching lockfile hash. Source URLs,
versions and hashes are in `artifacts/pyodide-runtime/314.0.7/provenance.json`.

Actual Chrome WebAssembly conversion testing has now run:

- All **15 conversion cases completed**; **14 passed exact native comparison**.
  Both default modes, representative real scans, combining scans and checked
  covariance arrays matched. Scientific modules remain byte-identical to the
  imported release (`artifacts/pyodide-validation/source-integrity.json`).
- One optional case, correct mode with `--uncertainty legacy-linear`, has one
  pre-serialization sigma differing by **one ULP**: browser
  `5.500000000092105`, native `5.500000000092106` (absolute difference
  `8.881784197001252e-16`). GSAS bytes and the other numerical columns match;
  CSV bytes and metadata output hashes differ. The strict matrix deliberately
  remains `passed: false`. Do not loosen tolerance or claim universal bitwise
  equality. The native reference used Python 3.14.5/NumPy 2.4.3.
  Diagnostics now isolate the first difference to scalar power at
  `bt1modern/reduce.py:207`: `float(np.sum(scaled_noise) ** 2)`. The summed sigma
  matches, but Pyodide's variance is one ULP lower and reciprocal weighting
  propagates the difference. Square roots agree for their respective inputs.
  The compiled-library/compiler/version cause is not independently isolated.
  See `sigma-difference-analysis.json`; algorithms, thresholds and the original
  strict report remain unchanged.
- **10 browser safety checks passed**, and source hashes were unchanged.
- Portable browser tests: **112 run, 110 passed, two skipped**, no failures or
  errors. Two native external-fixture tests skipped. Two hard-link tests and
  the compiled Fortran differential class were explicitly omitted; they remain
  native evidence. The separate adapter safety suite passed **nine native tests**.

Reports: `artifacts/pyodide-validation/browser-validation.json`,
`native-baseline.json`, `native-summary.json`, `filesystem.json`,
`adapter-tests.log`, `native-sigma-diagnostics.json`,
`chrome-sigma-diagnostics.json`, and `sigma-difference-analysis.json`.

## Browser publication compatibility

The actual Pyodide probe found **`os.link` absent**, while `os.replace` and
symlinks work. `browser/adapter.py` adapts only the CLI's `os` reference during
synchronous browser conversion. New files use a destination-existence check
followed by rename of the complete staged file; overwrite keeps `os.replace`.
This is restricted to a dedicated serialized worker with private MEMFS, with
explicit filesystem checks. It must not be generalized to shared/mounted filesystems
or asynchronous concurrent writers. The original CLI still controls input
protection, source hashes, staging and explicit overwrite. Native CLI behavior
and scientific algorithms are unchanged. Multiple-file publication remains
multiple filesystem operations.

## Direct NCNR retrieval and UI

Browser fetch/CORS and SHA-256 verification were exercised directly against
`https://ncnr.nist.gov/pub/ncnrdata/bt1/202610/Messegee/?format=jsonld`.
`Mn5Si005.bt1` and `Mn5Si006.bt1` were verified and inspected as complete
201/201-point scans, then converted separately in correct/independent mode.
Each produced 3300 valid points and one GSAS bank, with RAW/GSAS/CSV/JSON output.
Evidence: `artifacts/pyodide-validation/ncnr-complete-browser.json`.
Their temperatures differ (approximately 80 K and 250 K); they were not combined.
Newest does not imply complete. Partial scans require explicit correct-mode
permission, and combination must remain an explicit compatible choice.

The production `dist/` preview was exercised in Chrome: all seven listed NCNR
scans fetched with SHA verification; the latest complete scan `Mn5Si006.bt1`
quick-converted; Chrome's normal Save dialog wrote `Downloads/Mn5Si006.gsas`.
Native re-reading confirmed one bank and 3300 points. The explicit
`Mn5Si002.bt1` + `Mn5Si001.bt1` combination named `Mn5Si_low_temperature`
produced one bank/3300 points and five downloads. Mixed-temperature
`Mn5Si002.bt1` + `Mn5Si006.bt1` was refused without an explicit override.

IndexedDB caching with memory fallback is implemented, with preferences in
localStorage. Reload testing restored seven inputs and two results (about
1.1 MiB) and their download links. Local RAW upload/legacy conversion and explicit partial-scan refusal/opt-in were verified. Live public-site checks remain pending. No acquisition data is sent to a conversion
server; download copies remain the durable export.

## GitHub and publishing status

The user authorized connecting this checkout and publishing its browser app to
`https://github.com/scattering/BT-1-converter`. `origin` is the SSH URL
`git@github.com:scattering/BT-1-converter.git`. Existing 1Password SSH
authentication and `git fetch origin` succeeded; no keys/config were changed.
The public repository was empty at inspection, with `main` as default branch.

Root configured GitHub Pages with `build_type: workflow`. Configured address:
`https://scattering.github.io/BT-1-converter/`. **Publishing is pending; this is
not yet an observed live deployment.** `.github/workflows/pages.yml` installs
the verified runtime, builds a static artifact, uploads and deploys it.
`browser/build_site.py` includes only UI assets, scientific Python modules,
browser adapter and pinned runtime, excluding fixtures/reports/acquisition
archives. Public `build-info.json` records hashes without local source paths.

## Continue here

1. Complete upload/partial-scan UI tests. Preserve the diagnosed one-ULP result
   without changing scientific code or comparison thresholds merely to achieve
   a green report.
2. Rebuild with `.venv/bin/python browser/build_site.py` and preview `dist/` on a
   loopback static server. The production config has relative URLs and disables
   local validation/evidence endpoints.
3. Root owns final commit/push to the authorized repository. Follow the SSH skill
   checks; never force-push or expose credentials. Observe the Pages workflow
   and test the resulting public site before reporting it as live.
4. Record deployment URL/commit and final browser/cache checks in the docs and
   durable ignored artifacts. Preserve the numerical mismatch and test omissions.

Exact installation/preparation/serving/build commands and scope details are in
`browser/README.md`. The native baseline and browser evidence are distinct.

Do not claim scientific equivalence solely from serialization parity. Existing
limits on monitor/calibration uncertainty, conditional covariance and instrument
profile validation remain in force. No scientific algorithms or original
acquisition files were changed for this browser port.
