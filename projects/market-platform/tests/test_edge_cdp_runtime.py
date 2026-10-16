import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from tools import edge_cdp_runtime as runtime


class EdgeCdpRuntimeTests(unittest.TestCase):
    def test_cdp_ready_requires_websocket_url(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(
            {"webSocketDebuggerUrl": "ws://127.0.0.1/devtools/browser/1"}
        ).encode()
        with patch.object(runtime.urllib.request, "urlopen", return_value=response):
            self.assertTrue(runtime.cdp_ready("http://127.0.0.1:9223"))

    def test_ensure_edge_launches_once_under_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            (profile / "Default").mkdir(parents=True)
            with (
                patch.object(runtime, "cdp_ready", side_effect=[False, False, True]),
                patch.object(runtime, "profile_run_lock", return_value=nullcontext()),
                patch.object(runtime, "load_profile", return_value=profile),
                patch.object(runtime, "stop_stale_profile_edge", return_value=2),
                patch.object(runtime, "launch_edge", return_value=1234),
            ):
                state = runtime.ensure_edge_cdp(
                    "http://127.0.0.1:9223",
                    timeout=5,
                )
        self.assertTrue(state["ready"])
        self.assertTrue(state["launched"])
        self.assertEqual(state["pid"], 1234)
        self.assertEqual(state["stopped_stale"], 2)


if __name__ == "__main__":
    unittest.main()
