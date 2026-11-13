# BT-1 conversion in a local browser

This harness runs the existing `bt1modern` Python/NumPy package in a dedicated
Pyodide Web Worker. It loads the runtime and converter from this Mac, accepts
BT-1/RAW files, and exposes conversion results as browser downloads. It is a
local validation harness with a separate static build for GitHub Pages. The scientific package is
unchanged; `browser/adapter.py` handles the worker's file-publication boundary.

## Installed runtime and provenance

The pinned installation is **Pyodide 314.0.7**, **Python 3.14.2**, and
**NumPy 2.4.6**, using the Pyodide `pyemscripten_2026_0_wasm32` package ABI.

- Runtime directory: `artifacts/pyodide-runtime/314.0.7/pyodide/`.
- Download URLs, package versions, sizes and SHA-256 evidence:
  `artifacts/pyodide-runtime/314.0.7/provenance.json`.
- Core archive SHA-256 is pinned in `install_runtime.py` from the official
  GitHub release asset digest. NumPy is verified against the matching runtime's
  `pyodide-lock.json`; that lockfile's SHA-256 is recorded too.
- Sources: [official release](https://github.com/pyodide/pyodide/releases/tag/314.0.7)
  and [official installation documentation](https://pyodide.org/en/stable/usage/downloading-and-deploying.html).

Installation uses the official GitHub core archive and the official documented
CDN for its matching NumPy wheel. Installed runtime files, fixture copies and
test evidence stay in ignored `artifacts/`. There is no global Python/npm
installation, and conversion does not invoke Fortran or native subprocesses.

## Run on this Mac

From the project directory:

```sh
cd /Users/williamratcliff/bt1-converter
.venv/bin/python browser/install_runtime.py
```

Create the native comparison baseline **once**, if
`artifacts/pyodide-validation/native-baseline.json` does not already exist:

```sh
.venv/bin/python browser/prepare_validation.py
```

Preparation copies representative real inputs from the read-only fixture path
in `LOCAL_SETUP.md`, creates synthetic edge cases, and runs the same validation
cases in native Python. Existing baselines/results are deliberately refused.
`--real-fixture-dir /absolute/path` selects another input archive;
`--synthetic-only` omits the two real scans and reduces the scope of evidence.
`--out /absolute/fresh/path` creates a separate native reference, but the current
browser harness reads the default `artifacts/pyodide-validation/` location.
Archive an older validation directory before generating a replacement there.
Do not overwrite earlier evidence or modify the original acquisition files.

Start the loopback server and leave it running while using the browser:

```sh
.venv/bin/python browser/serve.py --port 8766
```

Open [the local harness](http://127.0.0.1:8766/browser/). The page first loads
Pyodide/NumPy and probes actual browser filesystem support. Then:

1. Click **Run browser validation** to compare native and browser conversions.
2. Select a `.bt1` or `.raw` file, or click **Convert bundled test fixture**.
3. Choose the mode and uncertainty policy, then convert and download outputs.
4. Download results for durable storage. The worker filesystem is temporary;
   the browser workspace adds an IndexedDB cache for inputs/results when storage
   is available. Multiple inputs are separate conversions unless **Combine
   scans** is explicitly selected.

Correct mode is the default. Conditional covariance is available only with
correct mode and independent uncertainty. An incomplete contiguous prefix
requires the explicit correct-mode option; legacy conversion requires complete
BT-1 acquisitions. The existing CLI validates incompatible choices.

The server binds only `127.0.0.1`, disables directory listings, and serves only
the harness, Python modules/tests, runtime assets and validation inputs/reports.
Browser evidence is written to the local project through a bounded JSON endpoint
that requires the loopback page's origin. No remote conversion service receives
uploaded files. Stop the server with Ctrl-C when finished.

## Static build and GitHub Pages

The connected repository is
[scattering/BT-1-converter](https://github.com/scattering/BT-1-converter).
The configured Pages address is
[https://scattering.github.io/BT-1-converter/](https://scattering.github.io/BT-1-converter/).
**Pages is configured for GitHub Actions; the initial publication is pending.**
The tested implementation is committed locally as `5cb0922`. Publication is
currently blocked because the locked 1Password SSH agent could not sign the
GitHub push. The SSH remote and credentials have not been changed. Unlocking
1Password and approving its existing GitHub key is the next action before
retrying the push. This configured URL is not yet evidence of a live deployment.

Build and preview the public artifact locally:

```sh
.venv/bin/python browser/install_runtime.py
.venv/bin/python browser/build_site.py
.venv/bin/python -m http.server 8767 --bind 127.0.0.1 --directory dist
```

Open `http://127.0.0.1:8767/`. The builder uses explicit source lists: the UI,
converter Python modules, browser publication adapter and pinned Pyodide/NumPy
runtime. It generates relative runtime paths so project-site hosting works.
It excludes native/browser test fixtures, reports, acquisition archives and
local path provenance. `dist/build-info.json` records public asset hashes and
runtime provenance. An existing output directory is replaced only if it bears
this builder's marker; unrelated directories are refused.

`.github/workflows/pages.yml` builds and uploads `dist/` on relevant source
pushes to `main` or manual workflow dispatch, then deploys through the
`github-pages` environment. Its path filter covers `browser/` source files,
`bt1modern/`, and the workflow itself; Markdown-only documentation changes do
not trigger deployment. Node's built-in test runner checks grouping behavior
before the build. The optional real-fixture grouping test skips in CI because
the local acquisition artifacts are deliberately excluded from Git.
The public configuration disables local test/evidence endpoints. Conversion
runs in each visitor's browser; NCNR retrieval requests go directly to NCNR.
No server receives uploaded acquisition data.

The new workspace UI includes IndexedDB storage for scan bytes and generated
results, with small preferences stored separately in localStorage. A storage
failure falls back to the current tab's memory and reports the limitation.
Saved work can be removed through **Clear saved workspace**. Browser storage
is scoped to the origin, so localhost and GitHub Pages have separate caches;
browser eviction/private browsing can also limit persistence. Download files
for long-term storage. Chrome reload testing of the production build restored
seven inputs and two results (about 1.1 MiB) with working download links.
Local RAW upload and legacy conversion, explicit partial-scan refusal/opt-in, and suggestion selection were verified in Chrome. Public-deployment checks remain pending.

## Browser filesystem adapter

The page records `os.link`, `os.replace`, and symlink behavior in the actual
browser runtime before testing conversions. Native CLI publication uses hard
links for exclusive creation; the browser adapter changes only `cli.os` during
the synchronous conversion call, leaving the real `os` module and scientific
code untouched. Native Python keeps the original publication path.

The adapter is valid only in a **dedicated, serialized worker with private
MEMFS**. It checks the worker and source/destination filesystem types. For new
outputs it refuses any existing destination, including a dangling symlink,
then renames the completed staged file synchronously. The worker queue never
yields between that check and rename, and MEMFS has no external writer. This
is not a general hard-link replacement and must not be used with shared,
mounted, or concurrently written filesystems. Overwrite uses `os.replace` after
the same MEMFS checks. The original CLI retains source alias protection,
source hashes, staging, and explicit overwrite rules. Publishing multiple
output files remains multiple operations, not one filesystem transaction.

## What the validation measures

`validation.py` runs both modes and independently selected uncertainty policies,
including representative real scans, zero counts, missing bins, combination,
malformed/partial input, refusal of existing output, overwrite and source
protection. It compares pre-serialization CSV arrays (angles, intensities,
sigmas, masks and contribution counts), GSAS/RAW/CSV bytes, uniform-grid/padding
invariants, metadata content, and covariance arrays/metadata. No numerical
tolerance is added to make cross-runtime differences pass.

Expected differences in validation paths, runtime versions and creation times
are retained explicitly in the comparison report. Compressed NPZ container
hashes may differ; their arrays and metadata are compared separately. These
exceptions do not suppress differences in scientific arrays or GSAS records.
Portable repository unit tests also run in the browser. Their report lists
skips and explicitly omitted native-only hard-link/Fortran checks; the native
Fortran oracle remains independent evidence, never a browser dependency.

Important evidence files under `artifacts/pyodide-validation/`:

- `manifest.json`: fixture/source hashes and original fixture provenance.
- `native-baseline.json`, `native-summary.json`: native reference and summary.
- `filesystem.json`: observed browser runtime and filesystem capabilities.
- `browser-validation.json`: browser results, exact comparisons, portable test
  log, failures, skips and omissions.
- `browser-upload.json` / `browser-portable.json`: additional reports when
  those evidence kinds are explicitly saved.
- `ncnr-complete-browser.json`: directly fetched, hash-verified NCNR scans and
  separate browser conversion summaries.
- `adapter-tests.log`: nine passing native adapter safety tests.
- `source-integrity.json`: unchanged scientific-source hashes against the
  imported release.
- `sigma-difference-analysis.json`: native/Chrome/Node operation-level diagnosis
  of the retained one-ULP comparison difference.

## Observed results — November 12, 2026 (US Eastern)

The actual Chrome/Pyodide run completed all **15 conversion cases**. **14 matched
the native reference exactly** under the explicitly recorded metadata/container
exceptions above. All **10 safety checks passed**, with sources unchanged.
Both default modes and the representative real-data cases matched; conditional
covariance arrays matched in the checked cases.

One optional combination, **correct mode with `--uncertainty legacy-linear`**,
has a recorded one-ULP difference in one pre-serialization sigma:
`5.500000000092105` in the browser versus `5.500000000092106` natively, an absolute
difference of `8.881784197001252e-16`. Its angles, intensities, validity mask,
contribution counts and GSAS bytes matched. CSV bytes and their metadata hash
differ because the sigma differs. The report therefore retains `passed: false`
for the exact-comparison matrix. No tolerance or algorithm was changed to hide
this difference; do not report all cases as bitwise identical. Native Python/
NumPy versions were 3.14.5/2.4.3, versus browser 3.14.2/2.4.6.

Operation-level diagnostics isolated the first difference to scalar power in
`bt1modern/reduce.py:207`, `float(np.sum(scaled_noise) ** 2)`, after identical
summed sigmas. Pyodide returns a variance one binary64 ULP below native;
reciprocal weighting propagates it into the final sigma. Square-root controls
agree for their respective inputs. The underlying compiled math-library,
compiler or version cause has not been independently isolated. Scientific
code, thresholds and the original comparison report remain unchanged.

The portable browser suite ran **112 tests: 110 passed, two skipped**, with no
failures/errors. The two skipped tests require native external fixture paths.
Two additional native hard-link tests and the compiled Fortran differential
class were explicitly omitted; duplicate-path and symlink source protection
were checked separately. The complete native baseline remains **126 passing
tests, no skips**. The browser adapter's separate native safety suite passed
all **nine tests**. These are distinct scopes of evidence.

The production `dist/` build was also exercised in Chrome through normal UI
controls. It fetched all seven listed NCNR scans with SHA-256 verification,
quick-converted the latest complete `Mn5Si006.bt1`, and saved a GSAS file through
Chrome's normal Save dialog. Native re-reading confirmed one bank and 3300
points. Selecting `Mn5Si002.bt1` plus `Mn5Si001.bt1` produced the named
`Mn5Si_low_temperature` combination, one bank/3300 points and five download
files. A `Mn5Si002.bt1` + `Mn5Si006.bt1` combination was refused for mixed
temperature without an explicit override. Reload restored all seven inputs
and both result sets from IndexedDB. These local production-preview checks do
not establish that the public deployment has completed.

## Direct NCNR retrieval

Direct browser retrieval was exercised against
[NCNR's Messegee JSON-LD catalog](https://ncnr.nist.gov/pub/ncnrdata/bt1/202610/Messegee/?format=jsonld).
The client checks filenames/URLs, byte counts and SHA-256 values against the
catalog before inspecting acquisitions with the existing converter. If a
growing file changes during download, it refreshes the listing and retries
once, then reports failure if the snapshot is still inconsistent.

The observed browser run fetched and verified `Mn5Si005.bt1` and
`Mn5Si006.bt1`, confirmed **201/201 points** in each, and converted them
separately in default correct/independent mode. Each produced **3300 valid
points and one GSAS bank**, with RAW/GSAS/CSV/JSON output available. Their
recorded mean temperatures were approximately 80 K and 250 K; they were not
combined. Evidence is in `ncnr-complete-browser.json`.

A catalog entry being newest does not establish a complete acquisition. The
UI inspects scan completeness and keeps incomplete prefixes distinct; partial
conversion requires explicit correct-mode selection. Combination remains
explicit and subject to the existing compatibility checks. The experiment
directory is currently fixed in `ncnr.mjs`; this is not a search of all NCNR
experiments. Retrieval requires access to NCNR, and newer server contents must
be inspected afresh rather than inferred from this recorded snapshot.

## Scientific limits

Browser parity does not establish scientific equivalence to every historical
Fortran build or validate instrument calibration. The uncertainty assumptions
and limitations in `../PYTHON_MODES.md` still apply: monitor/calibration
uncertainty is not propagated, covariance is conditional on observed variances
and fixed weights, ordinary GSAS fitting does not consume the exported
covariance automatically, and the 60-prime instrument profile remains
unvalidated. Legacy float32 operation boundaries, snapping/masks, and integer
serialization retain their documented meaning. Zero-weight gaps and blank
record padding are preserved in both modes.
