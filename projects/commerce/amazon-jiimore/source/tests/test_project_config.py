import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_config import get_proxies, load_config


class ConfigTests(unittest.TestCase):
    def test_missing_private_config_uses_empty_credentials_and_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(Path(directory) / 'not-created.json')
        self.assertEqual('', config['jiimore']['phone_num'])
        self.assertEqual('', config['jiimore']['password'])
        self.assertGreater(config['request_timeout'], 0)

    def test_partial_local_settings_keep_other_defaults_and_can_disable_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'local.json'
            path.write_text(json.dumps({'proxy': '', 'jiimore': {'phone_num': 'fixture'}}), encoding='utf-8')
            config = load_config(path)
        self.assertEqual('fixture', config['jiimore']['phone_num'])
        self.assertEqual('', config['jiimore']['password'])
        self.assertEqual({}, get_proxies(config))

    def test_invalid_configuration_does_not_echo_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'local.json'
            path.write_text('{"request_timeout": "secret-fixture"}', encoding='utf-8')
            with self.assertRaises(ValueError) as caught:
                load_config(path)
        self.assertNotIn('secret-fixture', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
