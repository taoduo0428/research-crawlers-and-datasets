import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from macos.prepare_device import Device, SetupError, doctor, validate_elf


@patch.dict('os.environ', {'ANDROID_SDK_ROOT': '', 'ANDROID_HOME': ''})
class MacDevicePreparationTests(unittest.TestCase):
    def test_arm64_binary_validation_rejects_other_platforms(self):
        good = bytearray(20)
        good[:6] = b'\x7fELF\x02\x01'
        good[18:20] = b'\xb7\x00'
        validate_elf(good)
        for data in (b'MZ' + bytes(32), b'\xcf\xfa\xed\xfe' + bytes(32), bytes(20)):
            with self.assertRaises(SetupError):
                validate_elf(data)

    def test_doctor_does_not_install_or_enable_root(self):
        calls = []
        def run(argv, timeout):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, 'offline', '')
        with patch('macos.prepare_device.platform.system', return_value='Darwin'), patch('macos.prepare_device.shutil.which', return_value='/sdk/adb'):
            report = doctor('emulator-5554', runner=run)
        self.assertFalse(report['ok'])
        self.assertEqual(report['actions'], [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], 'get-state')

    def test_private_key_rejected_before_external_commands(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'mitmproxy-ca-cert.pem').write_text('-----BEGIN PRIVATE KEY-----', encoding='ascii')
            with patch('macos.prepare_device.platform.system', return_value='Darwin'), patch('macos.prepare_device.shutil.which', return_value='/sdk/adb'):
                device = Device('emulator-5554', ca_dir=directory, runner=lambda *a, **kw: calls.append(a))
                with self.assertRaisesRegex(SetupError, 'private key'):
                    device.certificate()
            self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
