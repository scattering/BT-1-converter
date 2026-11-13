"""Filesystem compatibility for the converter's dedicated Pyodide worker.

The scientific package and the process-wide ``os`` module remain untouched.
``probe_filesystem`` records actual runtime behavior before enabling an adapter.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
import sys
import tempfile


def _require_private_memfs(source, destination):
    """Reject publication outside the isolated worker's in-memory filesystem."""
    import js

    if js.globalThis.constructor.name != "DedicatedWorkerGlobalScope":
        raise RuntimeError("MEMFS publication requires a dedicated browser worker")
    filesystem = js.globalThis.pyodide.FS
    memfs = filesystem.filesystems.MEMFS
    same = getattr(js.Object, "is")
    # The destination may not exist. Its parent determines its filesystem.
    # Inspect source itself too, so an unexpected mounted source is rejected.
    for path in (Path(source), Path(source).parent, Path(destination).parent):
        node = filesystem.lookupPath(str(path.resolve())).node
        if not same(node.mount.type, memfs):
            raise RuntimeError(f"MEMFS publication refuses a non-MEMFS path: {path}")


class _PublicationOS:
    """Delegate OS operations, adapting only publication in private MEMFS.

    Checking and renaming are synchronous, without yielding to the worker's
    event loop. A private MEMFS has no concurrent external writers; therefore
    the destination cannot appear between lstat and rename. This is deliberately
    not an implementation of general hard links and must not replace os.link.
    """

    def __init__(self, require_memfs=_require_private_memfs):
        self._require_memfs = require_memfs

    def __getattr__(self, name):
        return getattr(os, name)

    def link(self, source, destination):
        self._require_memfs(source, destination)
        if not stat.S_ISREG(os.lstat(source).st_mode):
            raise ValueError("Staged publication requires a regular source file")
        try:
            os.lstat(destination)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(errno.EEXIST, "Output exists", str(destination))
        # All computation and source hashing already finished inside cli.convert.
        # Rename publishes the complete staged file in one MEMFS operation.
        os.rename(source, destination)

    def replace(self, source, destination):
        self._require_memfs(source, destination)
        os.replace(source, destination)


@contextmanager
def publication_context():
    """Use safe staged publication in a serialized, dedicated Pyodide worker.

    Native Python retains the original CLI unchanged. On Pyodide this changes
    only the CLI's reference to ``os`` for the duration of the synchronous call.
    Root inputs, symlink checks, source hashes, and overwrite policy remain in
    the original CLI. Multiple output files still are not a single transaction.
    The worker must expose its runtime as ``globalThis.pyodide`` and must never
    run or yield concurrent conversion work inside this context.
    """
    if sys.platform != "emscripten":
        yield
        return
    from bt1modern import cli

    if isinstance(cli.os, _PublicationOS):
        raise RuntimeError("MEMFS publication contexts must not be nested")
    original = cli.os
    cli.os = _PublicationOS()
    try:
        yield
    finally:
        cli.os = original


def probe_filesystem() -> dict:
    """Exercise hard-link publication and replacement in the current filesystem."""
    report = {}
    with tempfile.TemporaryDirectory(prefix="bt1-fs-probe-") as directory:
        root = Path(directory)
        source = root / "source"
        source.write_bytes(b"staged content")
        target = root / "linked"
        try:
            os.link(source, target)
            if not source.samefile(target):
                raise RuntimeError("os.link did not create an alias of the source")
            try:
                os.link(source, target)
            except FileExistsError:
                pass
            else:
                raise RuntimeError("os.link did not refuse an existing destination")
            report["hardlink"] = {"supported": True}
        except (AttributeError, OSError, RuntimeError) as error:
            report["hardlink"] = {"supported": False, "error_type": type(error).__name__,
                                  "error": str(error)}
        replacement = root / "replacement"
        replacement.write_bytes(b"new content")
        destination = root / "destination"
        destination.write_bytes(b"old content")
        try:
            os.replace(replacement, destination)
            if destination.read_bytes() != b"new content" or replacement.exists():
                raise RuntimeError("os.replace did not move the staged content")
            report["replace"] = {"supported": True}
        except (AttributeError, OSError, RuntimeError) as error:
            report["replace"] = {"supported": False, "error_type": type(error).__name__,
                                 "error": str(error)}
    return report
