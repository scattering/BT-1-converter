# Local Mac project

Project folder: `/Users/williamratcliff/bt1-converter`.
Open this folder as a local project in Codex. AGENTS.md supplies the project
instructions. The folder has a local Git repository; no remote is configured.

## Run

```sh
cd /Users/williamratcliff/bt1-converter
source .venv/bin/activate
python bt1convert.py --help
python bt1convert.py inspect /path/to/scan.bt1
python bt1convert.py convert /path/to/scan.bt1 --mode correct --out-dir outputs/correct --covariance
python bt1convert.py convert /path/to/scan.bt1 --mode legacy --out-dir outputs/legacy
```

The local `.venv` is created with `--system-site-packages` using the existing
Homebrew Python and NumPy. It requires no downloads but is not an isolated lock
of those dependencies; updates to the system NumPy can affect this environment.
The converter requires Python 3.10+ and NumPy >=1.24. To recreate this same local
setup, run `python3 -m venv --system-site-packages .venv`. Running the source CLI
directly does not require a package installation or compiler.

## Tests

```sh
make test PYTHON=.venv/bin/python
make test-full PYTHON=.venv/bin/python
```

The first command runs the portable suite and may skip external checks. The full
target uses three read-only source/fixture locations, overridable Make variables:

| Variable | Default on this Mac |
|---|---|
| BT1_AUDIT_ROOT | `/Users/williamratcliff/Documents/Codex/2026-11-03/task-4/bt1_audit` |
| BT1_REAL_FIXTURE_DIR | `/Users/williamratcliff/Documents/Codex/2026-11-03/task-4/bt1_retrieval` |
| BT1_LEGACY_FIXTURE_DIR | `/Users/williamratcliff/Library/CloudStorage/Box-Box/gformat` |

Fortran must be available for the optional independent oracle tests. Inspect the
test summary: skipped checks are not equivalent to the complete 126-test run.
The regression harness writes its temporary builds outside the source archive.

For a standalone differential matrix, use a fresh output directory:

```sh
python validate_python_legacy.py \
  --audit-root /Users/williamratcliff/Documents/Codex/2026-11-03/task-4/bt1_audit \
  --legacy-fixture-dir /Users/williamratcliff/Library/CloudStorage/Box-Box/gformat \
  --out artifacts/oracle-new
```

For GSAS-II import validation, use its existing environment and explicit files:

```sh
/Users/williamratcliff/opt/anaconda3/envs/gsasii/bin/python validate_gsasii.py \
  --gsasii-root /Users/williamratcliff/GSAS-II/GSASII \
  --files outputs/correct/scan.gsas outputs/legacy/scan.gsas \
  --output artifacts/gsasii-import.json
```

Replace the example filenames with your generated files. This checks parsing,
not profile calibration or refinement. The existing evidence and important
scientific limits are linked from VALIDATION.md and PYTHON_VALIDATION.md.

## Source history

The project starts from the verified pure-Python v0.3.0 release. All runtime code,
test modules and validation scripts were copied byte-for-byte; only project
documentation and setup files were added or adjusted. SOURCE_PROVENANCE.json
records original and imported hashes. Large historical validation artifacts and
raw experiment files remain at their original locations. Keep new generated
files in ignored `outputs/` or `artifacts/` directories.
