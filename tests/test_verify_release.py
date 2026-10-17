"""Portable, strict manifest checks: source newlines versus data bytes."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("published_release_verifier", ROOT/"tools"/"verify_release.py")
VERIFIER=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class PortableReleaseTests(unittest.TestCase):
    def test_source_matches_exact_published_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/"script.py"
            file.write_bytes(b"def hello():\n    return 42\n")
            self.assertTrue(VERIFIER.source_sha256_matches(file, VERIFIER.sha256(file)))

    def test_source_crlf_checkout_matches_lf_git_blob(self):
        import hashlib
        git_blob=b"line one\nline two\nline three\n"
        expected=hashlib.sha256(git_blob).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/"README.md"
            file.write_bytes(b"line one\r\nline two\nline three\r\n")
            self.assertTrue(VERIFIER.source_sha256_matches(file, expected))
            # The historical data manifest does NOT use source normalization.
            self.assertNotEqual(VERIFIER.sha256(file), expected)

    def test_source_other_mutations_and_binary_changes_still_fail(self):
        import hashlib
        expected=hashlib.sha256(b"intended\noriginal\n").hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/"code.py"
            file.write_bytes(b"intended\r\nCHANGED\r\n")
            self.assertFalse(VERIFIER.source_sha256_matches(file, expected))
            file.write_bytes(b"intended\x00\r\noriginal\r\n")
            expected_binary=hashlib.sha256(b"intended\x00\noriginal\n").hexdigest()
            self.assertFalse(VERIFIER.source_sha256_matches(file, expected_binary))


if __name__=="__main__":
    unittest.main()
