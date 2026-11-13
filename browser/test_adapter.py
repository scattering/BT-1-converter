"""Safety tests for the browser-only publication adapter (no numerical changes)."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from browser.adapter import _PublicationOS, publication_context, probe_filesystem
from bt1modern import cli


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "stage"
        self.target = self.root / "output"
        self.source.write_bytes(b"fully staged content")
        self.guard_calls = []
        self.adapter = _PublicationOS(lambda *paths: self.guard_calls.append(paths))

    def test_complete_staged_content_moves_without_modification(self):
        self.adapter.link(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), b"fully staged content")
        self.assertFalse(self.source.exists())
        self.assertEqual(self.guard_calls, [(self.source, self.target)])

    def test_file_appearing_after_cli_preflight_is_refused(self):
        self.target.write_bytes(b"new owner")
        with self.assertRaises(FileExistsError):
            self.adapter.link(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), b"new owner")
        self.assertEqual(self.source.read_bytes(), b"fully staged content")

    def test_dangling_symlink_is_also_an_existing_destination(self):
        self.target.symlink_to(self.root / "absent")
        with self.assertRaises(FileExistsError):
            self.adapter.link(self.source, self.target)
        self.assertTrue(self.target.is_symlink())
        self.assertTrue(self.source.exists())

    def test_unexpected_staged_symlink_is_refused(self):
        alias = self.root / "stage-alias"
        alias.symlink_to(self.source)
        with self.assertRaises(ValueError):
            self.adapter.link(alias, self.target)
        self.assertFalse(self.target.exists())
        self.assertTrue(self.source.exists())

    def test_explicit_replace_preserves_complete_staged_content(self):
        self.target.write_bytes(b"old content")
        self.adapter.replace(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), b"fully staged content")
        self.assertFalse(self.source.exists())
        self.assertEqual(self.guard_calls, [(self.source, self.target)])

    def test_filesystem_guard_fails_before_publication(self):
        def reject(*paths):
            raise RuntimeError("not private MEMFS")
        guarded = _PublicationOS(reject)
        for method in (guarded.link, guarded.replace):
            with self.assertRaisesRegex(RuntimeError, "not private MEMFS"):
                method(self.source, self.target)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.target.exists())

    def test_native_context_keeps_original_os_module(self):
        with patch("browser.adapter.sys.platform", "darwin"):
            with publication_context():
                self.assertIs(cli.os, os)

    def test_context_restores_cli_os_on_exception_without_global_patch(self):
        with patch("browser.adapter.sys.platform", "emscripten"):
            with self.assertRaisesRegex(ValueError, "conversion error"):
                with publication_context():
                    self.assertIsInstance(cli.os, _PublicationOS)
                    self.assertIsNot(cli.os, os)
                    raise ValueError("conversion error")
        self.assertIs(cli.os, os)

    def test_nested_context_is_refused(self):
        with patch("browser.adapter.sys.platform", "emscripten"):
            with publication_context():
                with self.assertRaisesRegex(RuntimeError, "nested"):
                    with publication_context():
                        pass
        self.assertIs(cli.os, os)


if __name__ == "__main__":
    unittest.main()
