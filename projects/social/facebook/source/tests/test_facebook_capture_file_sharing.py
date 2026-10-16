import os
import platform
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
RUN_SCRIPT = BASE_DIR / "run_facebook_capture.ps1"


POWERSHELL_HARNESS = textwrap.dedent(
    r"""
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$FunctionSource,
        [Parameter(Mandatory = $true)][string]$JsonlPath,
        [ValidateSet("missing", "empty", "normal", "shared", "exclusive")][string]$Mode,
        [string]$Content = ""
    )

    $ErrorActionPreference = "Stop"
    $source = [IO.File]::ReadAllText($FunctionSource)
    $start = $source.IndexOf("function Get-CaptureJsonlLineCount")
    $end = $source.IndexOf("function Test-TargetSpecificPostUrl", $start)
    if ($start -lt 0 -or $end -le $start) { throw "capture line-count function was not found" }
    Invoke-Expression $source.Substring($start, $end - $start)
    $script:Jsonl = $JsonlPath

    $writer = $null
    try {
        if ($Mode -eq "shared" -or $Mode -eq "exclusive") {
            $share = if ($Mode -eq "shared") { [IO.FileShare]::ReadWrite } else { [IO.FileShare]::None }
            $writer = [IO.File]::Open($JsonlPath, [IO.FileMode]::Create, [IO.FileAccess]::Write, $share)
            $bytes = [Text.Encoding]::UTF8.GetBytes($Content)
            if ($bytes.Length -gt 0) { $writer.Write($bytes, 0, $bytes.Length) }
            $writer.Flush()
        }
        $result = Get-CaptureJsonlLineCount
        Write-Output ("RESULT=" + [int]$result)
        exit 0
    }
    catch {
        Write-Output ("ERROR=" + $_.Exception.GetType().FullName + ":" + $_.Exception.Message)
        exit 17
    }
    finally {
        if ($writer) { $writer.Dispose() }
    }
    """
)


@unittest.skipUnless(platform.system() == "Windows", "requires Windows file-sharing semantics")
class CaptureJsonlFileSharingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.powershell = os.environ.get("COMSPEC", "powershell.exe")
        if cls.powershell.lower().endswith("cmd.exe"):
            cls.powershell = "powershell.exe"
        if not RUN_SCRIPT.is_file():
            raise unittest.SkipTest(f"missing capture script: {RUN_SCRIPT}")

    def _run_reader(self, path: Path, mode: str, content: str = "") -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="fb-jsonl-harness-") as temp:
            harness = Path(temp) / "invoke-line-count.ps1"
            harness.write_text(POWERSHELL_HARNESS, encoding="utf-8")
            return subprocess.run(
                [
                    self.powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness),
                    "-FunctionSource",
                    str(RUN_SCRIPT),
                    "-JsonlPath",
                    str(path),
                    "-Mode",
                    mode,
                    "-Content",
                    content,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

    def _assert_count(self, result: subprocess.CompletedProcess[str], expected: int):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"RESULT={expected}", result.stdout)

    def test_missing_file_is_zero(self):
        with tempfile.TemporaryDirectory(prefix="fb-jsonl-missing-") as temp:
            result = self._run_reader(Path(temp) / "missing.jsonl", "missing")
        self._assert_count(result, 0)

    def test_empty_file_is_zero(self):
        with tempfile.TemporaryDirectory(prefix="fb-jsonl-empty-") as temp:
            path = Path(temp) / "capture.jsonl"
            path.touch()
            result = self._run_reader(path, "empty")
        self._assert_count(result, 0)

    def test_normal_file_counts_newline_delimited_records(self):
        with tempfile.TemporaryDirectory(prefix="fb-jsonl-normal-") as temp:
            path = Path(temp) / "capture.jsonl"
            path.write_text("one\ntwo\n", encoding="utf-8")
            result = self._run_reader(path, "normal")
        self._assert_count(result, 2)

    def test_shared_writer_does_not_block_reader_and_partial_tail_is_boundary(self):
        with tempfile.TemporaryDirectory(prefix="fb-jsonl-shared-") as temp:
            path = Path(temp) / "capture.jsonl"
            result = self._run_reader(path, "shared", "complete\n{\"partial\"")
        self._assert_count(result, 2)

    def test_unreadable_file_is_explicit_failure_not_zero(self):
        with tempfile.TemporaryDirectory(prefix="fb-jsonl-locked-") as temp:
            path = Path(temp) / "capture.jsonl"
            result = self._run_reader(path, "exclusive", "complete\n")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ERROR=", result.stdout)


if __name__ == "__main__":
    unittest.main()
