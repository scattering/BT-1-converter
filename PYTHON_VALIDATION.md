# Python-mode validation — v0.3.0

This report describes the original release validation. Its `validation/` and
`outputs/` paths refer to the
[preserved release directory](/Users/williamratcliff/Documents/Codex/2026-11-03/task-4/bt1-modern-v0.3.0).
Those artifacts are intentionally not copied into this development repository.
Use `make test-full` to repeat the suite with the existing Mac fixtures; see
[LOCAL_SETUP.md](LOCAL_SETUP.md). Write new evidence under `artifacts/`.

Validated on 2026-11-04. **Both runtime modes are Python/NumPy implementations.**
Compiled Fortran is used only as an independent oracle in the validation harness.
The earlier native-wrapper release and original Box/experiment files are preserved.

## Completed checks

- **126 tests passed with zero skips.** The full run is saved in
  `validation/v0.3-unit-tests.log`.
- **56 of 56 differential reductions matched exactly**, with zero differing
  declared GSAS angles, intensities or standard deviations. Both legacy-original
  and legacy-independent uncertainties were compared against independently
  compiled, hash-pinned official sources. No numerical tolerance was used to
  waive discrepancies. Evidence is in
  `validation/python-legacy-differential/validation.json`, alongside the oracle
  source/build records, input files and outputs.
- **Six of six Python preparations matched native PROPREP numerically**, including
  real scans, historical data, a non-grid 2.334-degree cutoff, detector-1 retention
  and excluded detectors. Titles/padding need not be byte-identical.
- The **12 independent differential test methods passed under NumPy 2.4.3 and
  NumPy 1.26.4**. The saved full suite uses the system Python/NumPy environment;
  the NumPy 1.26.4 cross-check used the existing GSAS-II environment. Oracle
  compiler: GNU Fortran (Homebrew GCC 15.2.0) 15.2.0.
- **Seven CLI tests run both modes with no compiler on PATH**, with subprocess
  creation, shell execution and imports of the former native backend blocked.
  They pass analytical uncertainty, uniform-gap/padding, source-preservation,
  mode/default/override and output checks. See `tests/test_cli_python_modes.py`.
  A static runtime-module dependency check is recorded in
  `validation/python-runtime-audit.json`.
- **Six real CLI reductions ran with PATH set to a nonexistent compiler path**:
  Mn5Si001 and Mn5Si002 in correct mode, legacy-original mode, and
  legacy-independent mode. Each produces one uniform BANK with **3,300 points,
  1.30–166.25 degrees, step 0.05 degrees**. The four legacy outputs also match
  the preserved v0.2.0 native references exactly in declared grid/I/sigma. No
  `.fortran.*` runtime artifacts are produced. See
  `validation/python-cli-end-to-end.json` and the three CLI logs.
- The **actual GSAS-II ContentsValidator and Reader** accepted all six real files
  plus newly generated correct/legacy uniform-gap fixtures. The fixture counts
  were exactly 3299 and 3298 (200 valid observations each), with zero-weight gaps
  preserved and no padding points invented. See
  `validation/python-modes-gsasii-parser.json`. This validates import, not a
  GSAS-II profile calculation, instrument calibration or refinement.

## What the tests distinguish

`tests/test_legacy_kernel.py` contains 19 focused analytical and policy tests.
It checks float32 operation boundaries, NINT/INT, snapped extrapolation, exact
endpoint and both-endpoint mask behavior, zero-count rules, two-stage weighting,
unequal monitor normalization, contribution counts and safe range checks. Its
runtime tests disable external process/compiler discovery.

The differential suite additionally covers both uncertainty expressions,
interpolation and nearest modes, real Mn5Si001/Mn5Si002 and the historical Box
fixture, combined scans, threshold offsets, curved/noisy synthetic data,
background matching, rescaling, large counts and monitors around 2**24,
calibration factors, masks, zero values, partial final records and signed
half-integer F8.0 ties. The compiler's actual F8.0 byte fields are checked
independently, not assumed from a Python rounding rule.

The 20 Python legacy I/O tests cover PROPREP cutoff/RAW rounding, source
preservation, fixed-width limits, uniform serialization, full-precision versus
integer output, invalid/missing masks and input/output guards. Existing correct
mode tests remain in the suite, including independent variance, scale/monitor
normalization, changing brackets, exact endpoints and conditional covariance.

## Format defect found and fixed

Initial large-monitor stress cases found a RAW parsing/writing defect rather than
a legacy arithmetic mismatch. Fortran G14.1 gives a monitor token without a
decimal point an implied fractional digit. For example, the bare values
16777217 and 33554435 are read into REAL as 1677721.75 and 3355443.5, yielding
normalization 0.5. With explicit decimal points they become 16777216 and
33554436 in float32, yielding approximately 0.49999994. Ignoring that distinction
can change integer output at high counts.

The reader now implements the descriptor rules and preserves original lexemes;
the writer makes the monitor decimal explicit. Eight matrix cases exercise
implied-decimal monitor/calibration fields in addition to explicit-decimal
precision cases. The exact-equality criterion was retained. No change to the
legacy arithmetic kernel was needed to resolve these mismatches. Previously
generated bare-monitor RAW files are interpreted as Fortran would read them;
regenerate from original BT-1 files if restoring intended monitor values.

## Parity scope and deliberate differences

Exactness is established for the recorded numerical inputs, selected flag
profiles, pinned sources and tested compiler/NumPy environments. It is not a
claim of universal bit identity across platforms or every historical option.
The default legacy profile corresponds to `gformat -or`; background matching and
count rescaling are independently enabled by explicit legacy switches.

Whole-file byte identity is not required: Python supplies safe title/instrument
metadata, blank unused final fields and normalized line endings. It does not
reproduce uninitialized padding or unsafe array access. Mislabelled INT-truncated
angle headers, overflow/nonfinite output, unsupported grid combinations and
positive-intensity zero-rounded ESDs are rejected explicitly.

Correct mode deliberately differs from legacy in bracketing, endpoints, variance,
precision and cutoff policy. Legacy CSV retains internally rounded float32 values
before F8.0 serialization; its GSAS file intentionally has integer intensities and
ESDs. Correct mode retains fractional GSAS values and offers conditional
covariance export. Neither mode models calibration/monitor uncertainty or the
complete sampling distribution of data-estimated weights.

Uniform-grid zero-weight holes are explicitly masked. GSAS-II ignores nonpositive
intensity even when sigma is positive; other crystallographic programs may require
excluded ranges. The current 60-prime instrument calibration remains unvalidated.
No physical equivalence or fit quality conclusion is inferred from conversion
parity. No remaining implementation blocker was identified for the tested scope.

## Reproduce

From the release directory, the complete suite uses the existing audit and files:

```sh
BT1_AUDIT_ROOT=/Users/williamratcliff/Documents/Codex/2026-11-03/task-4/bt1_audit \
BT1_REAL_FIXTURE_DIR=/Users/williamratcliff/Documents/Codex/2026-11-03/task-4/bt1_retrieval \
BT1_LEGACY_FIXTURE_DIR=/Users/williamratcliff/Library/CloudStorage/Box-Box/gformat \
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -p '*test*.py' -v
```

The self-contained independent matrix harness requires a new output directory:

```sh
python3 validate_python_legacy.py \
  --audit-root /Users/williamratcliff/Documents/Codex/2026-11-03/task-4/bt1_audit \
  --legacy-fixture-dir /Users/williamratcliff/Library/CloudStorage/Box-Box/gformat \
  --out /tmp/bt1-python-legacy-check-new
```

To repeat the NumPy 1.26 differential-only check, set the same audit/legacy fixture
environment variables and run:

```sh
PYTHONDONTWRITEBYTECODE=1 \
/Users/williamratcliff/opt/anaconda3/envs/gsasii/bin/python \
  -m unittest discover -s tests -p test_python_legacy_differential.py -v
```

Compiler-free conversion examples are in README.md. The saved JSON retains
original temporary execution paths as provenance; evidence files are copied into
this release and identified by hashes. Fortran executables in validation evidence
are test artifacts only and are never loaded by the runtime package.
