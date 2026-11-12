# Working on the BT-1 converter

This project converts BT-1 detector-bank data to uniformly spaced GSAS patterns.
Read README.md, PYTHON_MODES.md and LOCAL_SETUP.md before changing algorithms.

## Scientific and implementation contracts

- Both runtime modes must remain Python/NumPy only. Fortran is an independent
  test oracle, never a runtime backend or fallback.
- `correct` is the default mode. Preserve independently selectable uncertainty
  policies and document changes to their statistical assumptions.
- `legacy` uses deliberate float32 operation boundaries, historical snapping,
  interval/mask behavior and integer serialization. Reordering arithmetic can
  change the result; numerical changes require differential oracle checks.
- Default publication in both modes is one uniform GSAS CONST ESD BANK. Preserve
  explicit zero-weight gaps, validity metadata and blank record padding.
- Preserve source files, calibration files, archived releases and original Box
  code. Never write generated outputs into acquisition or archive directories.
- Keep raw experiments, generated output, compiled executables and environments
  out of Git. Use ignored `data/`, `outputs/` and `artifacts/` as appropriate.
- Do not infer scientific equivalence from serialization parity. Monitor and
  calibration uncertainty, conditional covariance and unvalidated instrument
  profiles have the limitations documented in PYTHON_MODES.md.

## Development and checks

Run `.venv/bin/python bt1convert.py --help` for CLI options. The local virtual
environment uses already installed NumPy; no network is needed for conversion.

`make test` runs available unit tests. Optional oracle/fixture tests may skip when
their dependencies are absent. `make test-full` supplies this Mac's original
external fixture paths and checks that the archive folders are available. Inspect
the summary for skips before claiming a complete run. Override Make variables to
use another archive; do not edit scientific code to accommodate local paths.

Use `make test-full` for changes to preparation, legacy numerics or GSAS I/O.
For broader parity evidence use validate_python_legacy.py with a fresh output
directory. GSAS-II importer checks need explicit input/output paths (LOCAL_SETUP).
Treat tests as independent evidence; do not change expected values just to match
an altered implementation. Record intentional scientific differences explicitly.
