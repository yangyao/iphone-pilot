import argparse
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
from tools.service_manager import Services


class ServiceTests(unittest.TestCase):
    def test_install_uses_fixed_paths_and_private_plist(self):
        with tempfile.TemporaryDirectory() as folder:
            service = Services(folder)
            service.agents = Path(folder) / 'agents'
            with patch.object(service, 'ctl') as ctl:
                ctl.return_value.returncode = 0
                service.install('api', (['python3', '/repo/observe.py'], {}))
                spec = plistlib.loads(service.path('api').read_bytes())
                self.assertTrue(spec['KeepAlive'])
                self.assertEqual(spec['WorkingDirectory'], str(Path(folder).resolve()))
                self.assertEqual(service.path('api').stat().st_mode & 0o777, 0o600)
                ctl.assert_called_once_with('bootstrap', service.domain, str(service.path('api')), check=False)

    def test_unmanaged_port_is_not_killed(self):
        with tempfile.TemporaryDirectory() as folder:
            service = Services(folder)
            args = argparse.Namespace(service_action='start', api_only=True, receiver_only=False)
            with patch.object(service, 'loaded', return_value=False), patch.object(service, 'arguments', return_value=([], {})), patch.object(service, 'read', return_value={'ok': True}), patch.object(service, 'ctl') as ctl:
                with self.assertRaisesRegex(ValueError, 'unmanaged'):
                    service.run(args)
                ctl.assert_not_called()

    def test_start_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            service = Services(folder)
            args = argparse.Namespace(service_action='start', api_only=True, receiver_only=False)
            with patch.object(service, 'loaded', return_value=True), patch.object(service, 'read', return_value={'ok': True}), patch.object(service, 'install') as install:
                result = service.run(args)
                self.assertEqual(result['api'], 'ready')
                install.assert_not_called()
