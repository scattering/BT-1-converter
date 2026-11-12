# BT-1 conversion in Python — v0.3.0

This is the standalone local development project. Start with
[LOCAL_SETUP.md](LOCAL_SETUP.md) for the Mac environment, commands and evidence
locations. Runtime code and tests were copied unchanged from the validated
v0.3.0 release; [SOURCE_PROVENANCE.json](SOURCE_PROVENANCE.json) records their
source and hashes.

Both **correct** and **legacy** modes execute entirely in Python and NumPy. No
compiler, Fortran executable, subprocess, network or authentication is used for
conversion. Compiled Fortran is used only as an independent regression-test
oracle. Requires Python 3.10+ and NumPy.

Original Box programs, acquisition files and the earlier v0.1.0/v0.2.0 releases
are preserved. This release implements the requested Python modes; the earlier
native-wrapper implementation is archived as historical validation material.

## Two modes

Correct mode is the default:

```sh
python3 bt1convert.py convert /path/Mn5Si002.bt1 \
  --mode correct --out-dir outputs/correct --covariance
```

Legacy mode reproduces the historical numerical procedure in Python:

```sh
python3 bt1convert.py convert /path/Mn5Si002.bt1 \
  --mode legacy --out-dir outputs/legacy
```

Corrected error propagation can also be used with legacy geometry/arithmetic:

```sh
python3 bt1convert.py convert /path/Mn5Si002.bt1 \
  --mode legacy --uncertainty independent --out-dir outputs/legacy_corrected_errors
```

| Choice | Correct mode | Legacy mode |
|---|---|---|
| Runtime | Python + NumPy | Python + NumPy |
| Arithmetic | float64 | Explicit historical float32 operation boundaries |
| Default uncertainty | Squared interpolation weights | Original linear interpolation of sigma |
| Angle selection | Actual enclosing measured interval | Historical snapping and current/following interval |
| Endpoints | Valid exact endpoints included | Historical final interval/mask rules |
| Zero counts | One-count raw variance floor before scaling | Final sigma=1 after detector/monitor scaling |
| PROPREP cutoff | Keep recorded M4 at/above cutoff | Historical float32/NINT cutoff |
| GSAS numeric fields | Fractional values retained where representable | Historical integer F8.0 rounding |
| Covariance export | Optional, independent-variance mode | Unavailable |

`--uncertainty independent|legacy-linear` is independent of the mode. Thus the
statistical correction can be studied without simultaneously changing geometry.
Legacy means the **official historical GFORMAT**, not the reciprocal-r expression
in the locally modified Box `gformat3.f` or the duplicated normalization in
`powpref3.py`.

The legacy profile defaults to omitted background offsets and count rescaling,
matching the audited `gformat -or` workflow. Add `--legacy-background` and/or
`--legacy-rescale` to select the corresponding historical operations. Bare
historical GFORMAT enables both; choose both switches and `legacy-linear` to
compare that profile. Absorption correction is outside this wrapper's scope.

## Uniform-grid outputs

Both modes default to **one uniformly spaced GSAS CONST ESD BANK**, including
missing bins as `(I, sigma) = (0, 0)` zero-weight placeholders. Missing observations
are never fabricated or silently removed from the grid. Final record padding is
blank, so importers cannot create extra points from unused fields.

For each input the CLI writes:

- `.raw`: prepared detector counts and calibration, unless `--no-raw` is given.
- `.gsas`: the uniform combined-detector pattern.
- `.csv`: pre-serialization angle, intensity, sigma, mask and contribution count.
  Correct mode retains float64; legacy preserves its internally rounded float32
  values promoted exactly to float64. Legacy CSV therefore retains fractions that
  its integer GSAS file intentionally discards.
- `.json`: mode, uncertainty, policies, source/output hashes, calibration metadata,
  validity masks, quantization and limitations.
- `.covariance.npz`, optionally in correct independent mode: a sparse factor F
  describing conditional covariance C=F@F.T.

The Python writer does not generate an instrument calibration. Supply a measured
profile with `--instrument-file /path/calibrated.inst`; this copies/references the
file without validating it. The current 60-prime resolution profile still needs
independent validation.

A uniform grid does not guarantee identical weighting in every crystallographic
program. GSAS-II's legacy ESD reader ignores nonpositive intensities even with a
positive sigma. Codes that do not accept zero-weight holes need explicit excluded
ranges. These cases are reported in metadata. Legacy publication rejects a
positive intensity whose sigma would round to zero, rather than inventing an ESD;
use correct mode's fractional output for that case.

Correct mode can opt into the previous segmented export with `--gap-policy split`.
Legacy mode requires uniform publication. Both modes refuse existing output
files unless `--overwrite` is explicit, and protect input paths, symlinks and
hardlinks even then. Sources are hashed before parsing and again before output
publication; changing inputs abort publication. Multiple-file publication is not
a single filesystem transaction.

## Inputs and combining scans

Inspect without reducing:

```sh
python3 bt1convert.py inspect /path/Mn5Si002.bt1
```

`.bt1` and prepared `.raw` inputs are supported. BT-1 conversion requires NEUT
monitor-preset counting. RAW does not establish acquisition completeness,
temperature or counting mode; unknown information is never invented. Detector
scale and monitor factors are treated as exact.

Multiple inputs are reduced separately unless combination is explicit:

```sh
python3 bt1convert.py convert /path/Mn5Si001.bt1 /path/Mn5Si002.bt1 \
  --mode legacy --combine low_temperature --out-dir outputs/combined
```

The first scan supplies the normalization monitor. Instrument compatibility,
duplicated inputs and measured temperature differences are checked; more than
1 K difference requires `--allow-mixed-temperature`. These checks do not establish
sample/field/state equivalence. Legacy mode additionally requires compatible
ordered 32-detector grids and calibrations because the historical indexing assumes
the first file's bank origins.

`--cutoff 1` is the default; older instructions used 3 degrees. Confirm the
appropriate low-angle acceptance. `--keep-detector1` and repeated
`--drop-detector N` select preparation masks. Correct mode can explicitly reduce a
contiguous incomplete prefix with `--allow-partial`; legacy mode requires complete
BT-1 acquisition. Neither mode fills missing interior acquisition rows.

Legacy rejects arbitrary `--step`, `--monitor`, alternate endpoint/zero-count
rules and covariance instead of silently ignoring them. Correct mode supports
these explicit choices; `--endpoint fortran` in correct mode changes only its
endpoint rule and does not reproduce full legacy behavior.

## Code, validation and limits

The implementation is heavily documented in `bt1modern/legacy.py`, the
`prepare_legacy_bt1` function in `bt1modern/io.py`, the GSAS writers in
`bt1modern/gsas.py`, and the mode/publication logic in `bt1modern/cli.py`.

See **[PYTHON_MODES.md](PYTHON_MODES.md)** for the numerical contract and deliberate
publication differences, and **[PYTHON_VALIDATION.md](PYTHON_VALIDATION.md)** for
measured exactness, test coverage and commands. No equivalence claim is made
beyond the recorded compiler/source/input/flag comparisons. Historical validation
material remains in the original release archive; [VALIDATION.md](VALIDATION.md)
links to that evidence. Generated data and compiled oracle artifacts are excluded
from this project's Git history.

Run runtime and unit checks without Fortran:

```sh
python3 -m unittest discover -s tests -p '*test*.py' -v
```

Compiler-dependent differential tests are optional oracle checks, never a runtime
fallback. With the audited source and fixture paths available, follow the complete
commands in `PYTHON_VALIDATION.md` to include them.

Independent error propagation is conditional on observed variances and fixed
averaging weights, not an exact low-count Poisson model. Interpolation correlations
remain even when the marginal variance is corrected. Correct-mode NPZ files retain
a conditional covariance factor; ordinary GSAS fitting does not automatically use
it. Calibration and monitor uncertainty, absorption, five-detector acquisition and
time-preset normalization are not modeled.
