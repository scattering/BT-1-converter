# BT-1 reduction / Pyodide handoff

Updated 2026-11-13. Earlier live-deployment evidence is retained below; the combination/download revision is implemented and tested locally.
Public deployment and the browser save-dialog check are pending Mac/SSH unlock.

## Starting point

Work in `/Users/williamratcliff/bt1-converter`, the existing local Codex project,
not a new rewrite. It contains the validated v0.3.0 pure-Python port of
PROPREP/GFORMAT. Read `AGENTS.md`, `README.md`, `PYTHON_MODES.md`, and
`LOCAL_SETUP.md` before changing it. `SOURCE_PROVENANCE.json` records imported
source hashes. Native scientific baseline commit: `5f0e366` (relocation to user
home). Browser implementation commit `5cb0922` was published through source
commit `2e7de03`; the scientific modules remain unchanged from that baseline.
The former `/Users/williamratcliff/Documents/Codex/bt1-converter` location is
obsolete. The local environment was recreated after the move.

Reference release:
`/Users/williamratcliff/Documents/Codex/2026-11-03/task-4/bt1-modern-v0.3.0`.
Review archive:
`/Users/williamratcliff/Downloads/bt1-proprep-gformat-python-v0.3.0-review.zip`.
Do not modify those source archives or original acquisition files.

## November 13 combination/download revision

The browser now guards the one-scan quick-latest action when two or more inputs
are selected, so it cannot replace that selection. **Combine N selected scans**
becomes primary for multiple selections, with separate conversion still
available. Before adding DAT or exposing new downloads, the browser validates
result names/group counts, ordered source paths/SHA-256 hashes/byte counts and
generated output SHA-256 hashes/byte counts. Result cards put
combined pattern/metadata downloads first and individual prepared RAW files in
a collapsed **Prepared input files (not combined)** section. They explain the
first-input monitor normalization and inverse-variance estimate so that a
similar intensity scale is not mistaken for an uncombined raw-count result.

The real `Mn5Si001.bt1`/`Mn5Si002.bt1` audit passed in both modes and both input
orders, with both inputs recorded and combined intensities/uncertainties
different from the first scan alone. Original sources were preserved. Evidence:
`artifacts/zach-combine-audit-fxnbl99i/report.json`. This does **not** identify
the exact cause of the reported user result; do not claim that it does.

The new browser FullProf `.dat` writer uses six header records, `XYDATA`,
`INSTRM=10`, and `INTER 1 1 0 0` (unit multipliers, no interpolation). It preserves
the original valid CSV X/Y/sigma tokens, omits invalid rows without changing
surviving angles, and reports omitted/nonpositive counts and provenance. Do
not encode missing bins with `(0, 0)` rows: actual FullProf 8.50 reader checks
gave those rows variance one. The same reader clamps valid `I <= 0` to zero
and variance one even with supplied positive sigma. The exporter preserves
these valid triples and warns rather than changing scientific data. Exact file
triples therefore do not establish identical FullProf fitting weights.

Reader evidence: `artifacts/fullprof-reader-matrix-y8udnmzz/summary.json` and
per-case comparison files. It is a parser/weighting check, not a BT-1 refinement.
The final writer outputs were also read by FullProf 8.50:
`artifacts/fullprof-export-validation-wkuxgyzm/summary.json`. Both real combined
001/002 modes imported 3300/3300 rows with normal termination and matching angles
at printed precision; intensity/variance comparisons stayed within printed
precision/internal-float bounds. DAT tokens exactly matched valid CSV tokens,
and CSV hashes were unchanged. The synthetic 98-row export retained three
interior gaps; its two nonpositive-intensity reader changes are explicitly
documented/warned, not treated as exact downstream equivalence.
FullProf remains an independent native validation executable, never a runtime
backend. The Python reduction core and existing GSAS/CSV outputs remain
unchanged. Only newly converted results gain DAT; old cached results must be
converted again to obtain it.

Current local validation: `make test-full` passed 126 tests with no skips;
`browser.test_downloads` plus `browser.test_fullprof` passed 20 native Python
tests; Node grouping tests passed 10 with no skips on this Mac; eight native
CLI/finalizer end-to-end runs passed in
`artifacts/browser-finalize-e2e-arhyv3bj/report.json`. Local Chrome passed the two-selection quick-action guard and completed correct
combined (six outputs), single (five outputs), and separate legacy two-input
(ten outputs) conversions. Both source names,
GSAS/DAT-first links, collapsed individual RAW downloads and cache restoration
were verified. The browser save dialog remains unavailable while the Mac is
locked; do not claim the clicked browser DAT was saved/read by FullProf. Native
exports were independently read as above. SSH publication is also blocked: the
agent socket is present but ssh-add lists no identities, and GitHub SSH returned
agent signing failure / Permission denied (publickey). Preserve the SSH remote;
resume after the user unlocks the Mac/1Password. Keep these scopes separate from
the earlier dated browser/native evidence below.

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
1.1 MiB) and their download links. Local RAW upload/legacy conversion and
explicit partial-scan refusal/opt-in were verified. No acquisition data is
sent to a conversion server; download copies remain the durable export.

Live Chrome verification at the public Pages URL confirmed runtime loading,
direct NCNR fetch with all seven SHA-256 values verified, and quick conversion
of complete `Mn5Si006.bt1` to one bank/3300 points/four outputs. A normal browser
download of the Mn5Si006 GSAS file parsed natively as one bank/3300 points
and matched the validated local output SHA-256. Selecting the suggested
`Mn5Si002.bt1`/`Mn5Si001.bt1` group did not trigger another conversion;
explicit combination named `Mn5Si_low_temperature` produced one bank/3300
points/five outputs. Public-site reload restored seven inputs and two results
(about 1.1 MiB) from IndexedDB. The observed `Mn5Si007.bt1` snapshot was partial,
174/201 points, modified November 12 at 8:54:45 PM EST; later snapshots may grow.

The follow-up Workspace/Documentation tabs and accessible field help passed
local Chrome checks: direct `#documentation` loading, arrow/Home/End navigation,
help open/Escape close, and documentation access during runtime loading and
conversion without losing workspace state. This follow-up UI is now published
as `bb745be` through
[run 43362590637](https://github.com/scattering/BT-1-converter/actions/runs/43362590637).
Public Chrome checks verified direct documentation navigation, tab controls,
M4 help/Escape dismissal, and Mn5Si006 conversion (3300 points/four outputs).
Screenshot: `artifacts/pyodide-validation/documentation-published.jpg`.
The initial deployment record remains below.

## GitHub and publishing status

The user authorized connecting this checkout and publishing its browser app to
`https://github.com/scattering/BT-1-converter`. `origin` is the SSH URL
`git@github.com:scattering/BT-1-converter.git`. Existing 1Password SSH
authentication and `git fetch origin` succeeded; no keys/config were changed.
The repository's default branch is `main`. The authorized push succeeded using
the existing SSH/1Password setup; the earlier locked-agent blocker is resolved.

GitHub Pages uses `build_type: workflow` and is **deployed and verified live** at
[https://scattering.github.io/BT-1-converter/](https://scattering.github.io/BT-1-converter/).
[Run 43348393458](https://github.com/scattering/BT-1-converter/actions/runs/43348393458)
successfully built/deployed source `2e7de03` (implementation `5cb0922`). The
initial deployment used manual workflow dispatch. The public build manifest
matches local `dist/`, and HTML/worker/WASM requests returned HTTP 200 with
the expected MIME types. Chrome interaction checks above ran around
8:56–8:59 PM EST on November 12, 2026.

`.github/workflows/pages.yml` runs grouping tests, installs the verified runtime,
builds a static artifact, uploads and deploys it. It triggers on changes to
`browser/` source (excluding Markdown), `bt1modern/`, or the workflow on `main`,
and on manual dispatch. Documentation-only commits do not redeploy. The optional
real-fixture grouping test skips in CI because local acquisition artifacts are
not committed.
`browser/build_site.py` includes only UI assets, scientific Python modules,
browser adapter and pinned runtime, excluding fixtures/reports/acquisition
archives. Public `build-info.json` records hashes without local source paths.

## Future maintenance

The initial installation, browser validation, recorded UI checks and first public
deployment are complete. Preserve the diagnosed one-ULP result and explicit
test omissions; do not modify algorithms or thresholds merely to make exact
parity green. For future code changes, rebuild/retest the affected behavior,
observe the Pages workflow, and record the deployed commit and public checks.
Keep original acquisitions and source archives read-only and generated evidence
outside Git. The production configuration uses relative URLs and disables
local validation/evidence endpoints.

Exact installation/preparation/serving/build commands and scope details are in
`browser/README.md`. The native baseline and browser evidence are distinct.

Do not claim scientific equivalence solely from serialization parity. Existing
limits on monitor/calibration uncertainty, conditional covariance and instrument
profile validation remain in force. No scientific algorithms or original
acquisition files were changed for this browser port.
