# Numerical contract for the Python modes

Both conversion modes use Python/NumPy only. Compiled Fortran appears in the
independent tests. No runtime branch, missing-feature fallback or executable cache
can invoke a compiler or Fortran program. The earlier native wrapper remains in
its preserved v0.2.0 release and archived validation source, not the runtime
package for this release.

## Correct mode

`reduce_datasets` in `bt1modern/reduce.py` retains float64 arithmetic, independently
brackets each target using the actual observed angles, and forbids extrapolation.
An exact usable observation is retained, including the final endpoint. Negative
raw counts mask missing observations and block adjacent interpolations.

For interpolation fraction r, default independent propagation is:

```text
I = (1-r)*I_left + r*I_right
variance = (1-r)^2*variance_left + r^2*variance_right
```

Detector scales and monitor ratios multiply both intensity and sigma. The default
zero-count convention is a one-count raw variance before those scale factors.
Overlapping observations are combined by inverse variance. If requested, a sparse
factor retains correlations between bins sharing raw observations. This is
conditional linear propagation with data-estimated variances and weights held
fixed, not a full likelihood/covariance model for their random estimation.

Correct-mode preparation uses an actual angle predicate at the cutoff. Exported
RAW requires representable integer-centidegree regular grids; the in-memory
reducer can handle irregular increasing spacing, but the BT-1 parser rejects
missing interior acquisition rows rather than interpreting them as planned gaps.

## Legacy mode

`reduce_legacy` in `bt1modern/legacy.py` implements the historical algorithm with
explicit NumPy float32 rounding at the Fortran REAL operation/assignment
boundaries. It preserves loop order and the two-stage weighting procedure.
A float32 dtype alone would be insufficient: re-associating additions, eliminating
a reciprocal/square-root step, or accumulating in float64 can change final F8.0
rounding. Comments identify these compatibility-critical operations.

The historical geometry uses degree-valued corrected angles `x = angle - zero`,
with input step h. For nonnegative x, `INT(x/h)` truncates toward zero. A remainder
less than **0.00011 degrees** chooses the lower grid target; otherwise the next
higher target is used. The current/following input pair supplies that target even
when the target lies slightly below the current point, so r can be negative.

For detector 18 in the supplied scans, corrected positions are
86.3001–96.3001 degrees with h=0.05 degrees. Interior targets nominally use:

```text
legacy: I = 1.002*I_current - 0.002*I_next
correct: I = 0.002*I_previous + 0.998*I_current
```

The legacy first-boundary contribution at 86.30 degrees is extrapolated; the
96.30-degree contribution is omitted by the historical final-interval loop.
An interval with either negative endpoint is discarded, including an otherwise
valid exact left observation when r=0. These are deliberate compatibility rules,
not claims of superior measurement interpretation.

Legacy default sigma is linear interpolation of standard deviations. Choosing
`--uncertainty independent` changes only its interpolation variance expression;
legacy geometry, float32 arithmetic, mask rules and endpoint behavior remain.
Zero raw counts receive final sigma=1 after scaling, matching the historical rule.
Nearest mode uses NINT ties away from zero and includes all valid samples.

Contributions accumulate first by file within a detector, are normalized through
historical reciprocal/square-root operations, then accumulate by detector on the
master grid. The first/last detector define that grid. Optional historical
background offsets use detector 20 as reference; optional count rescaling runs
after the combined pattern is formed. Both are off by default to match the
explicitly audited `-or` profile. Absorption is not implemented.

`prepare_legacy_bt1` reproduces PROPREP's float32/NINT cutoff, detector masks and
RAW field quantization in Python. A non-grid cutoff is rounded to an input row,
which can differ from correct mode's strict predicate. Both modes preserve
original source arrays and acquisition metadata.

## Serialization, grids and intentional boundaries

Both modes form uniform output grids and preserve missing bin positions. The
main output uses one CONST ESD BANK with zero-weight placeholders and explicit
validity metadata. No gap is filled with an inferred measurement. Correct mode
can optionally split into individually uniform banks; legacy publication remains
one bank for historical compatibility.

Legacy GSAS fields reproduce historical F8.0 nearest-even integer rounding with
trailing decimal points. The writer uses the final internally rounded float32
sigma without recomputing it in float64. Correct GSAS fields preserve fractional
values where the fixed width permits. JSON reports quantization, and CSV preserves
the pre-serialization values in both modes. Legacy CSV retains internal float32
values promoted exactly to float64; its greater displayed decimal count does not
imply greater calculation precision.

The CLI's per-input `--input-cutoff PATH=DEGREES` overrides and the browser's
per-scan M4 controls select the existing preparation functions independently
for each acquisition. They do not change cutoff arithmetic, variance policies,
normalization or reduction order. Global `--cutoff` still defaults to 1 degree;
an override is recorded in that input's preparation metadata. Correct mode can
combine different prepared starts. Legacy combination retains the identical
per-detector-grid requirement: different requested cutoffs may still work if
their historical rounding produces the same grids, but incompatible grids are
rejected rather than recut or padded to match. Prepared RAW cannot receive a new
per-input cutoff because it does not retain the original acquisition points.

Intentional differences from raw historical files are explicit:

- Unused final-record fields are blank. Native GFORMAT can write undeclared array
  values, which some readers incorrectly treat as additional observations.
- No hardcoded instrument calibration is generated or accepted. A supplied
  calibrated profile is referenced explicitly.
- Invalid or overflowing output is rejected instead of writing asterisks, NaNs
  or infinities. Positive serialized intensity with zero-rounded sigma is rejected
  rather than becoming a division-by-zero error in GSAS-II.
- Legacy INT-based angle headers that would mislabel the actual grid are rejected.
  For example, native float32 arithmetic can print a 0.53-degree step as 0.52.
- Unsupported geometry, source shapes, combined-scan assumptions or CLI conflicts
  produce errors rather than reproducing undefined legacy array access.

Whole-file byte identity is not the contract: title/instrument metadata, blank
padding and line endings can differ. Exact declared angle/intensity/sigma parity
is tested separately against compiled reference programs. Platform/compiler
changes and untested legacy options require new evidence rather than an assumed
universal equality claim.

GSAS-II's ESD reader zero-weights nonpositive intensity even with positive sigma.
Other crystallographic programs may require explicit excluded ranges for missing
bins. Uniform spacing alone cannot standardize every program's weighting policy.
The existing 60-prime instrument calibration remains unvalidated.

## RAW fixed-field parsing correction

Oracle stress tests exposed an I/O difference independent of the legacy reduction
kernel. GFORMAT reads the monitor using G14.1. Without a decimal point in the
mantissa, that format implies one fractional digit: `137` means 13.7, while
`137.` means 137. An exponent does not remove this rule: `1E+08` means 1E+07,
whereas `1.E+08` means 1E+08. F8.4 calibration fields similarly imply four digits.

The RAW reader now follows the actual input descriptors, including E/D exponents,
embedded blank-null spaces and sign-only exponents. Original field text and its
descriptor are retained in metadata. The writer always emits an explicit monitor
decimal point and checks that Fortran would recover the intended value.

Earlier Python-generated RAW files may contain bare integer monitor fields. This
release interprets those fields as Fortran actually does rather than guessing
the author's intention. Regenerate prepared RAW from the original BT-1 acquisition
when the intended monitor value needs restoration; original data are not edited.
Native PROPREP output already includes the necessary decimal notation. The
correct-mode statistical algorithm is unchanged by this format correction.
