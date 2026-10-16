import unittest

from tools.run_liepin_camoufox_forward import OUTPUT_DEFAULT, STATE_DEFAULT


class LiepinCamoufoxForwardTests(unittest.TestCase):
    def test_forward_worker_has_isolated_output_and_state(self):
        self.assertEqual(OUTPUT_DEFAULT.name, "worker-camoufox-forward.jsonl")
        self.assertIn("liepin-camoufox-forward", str(STATE_DEFAULT))


if __name__ == "__main__":
    unittest.main()
