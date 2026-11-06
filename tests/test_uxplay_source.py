import subprocess
import unittest
from unittest.mock import patch, MagicMock
import uxplay_source


class SourceTests(unittest.TestCase):
    def test_eof_terminates_and_reaps_child(self):
        child = MagicMock()
        child.wait.side_effect = [subprocess.TimeoutExpired('uxplay', 3), 0]
        with patch.dict('os.environ', {'UXPLAY_BIN': '/fake/uxplay'}), patch('uxplay_source.subprocess.Popen', return_value=child), patch('os.fdopen') as opened, patch('signal.signal'), patch('fcntl.flock'):
            opened.return_value.__enter__.return_value.read1.return_value = b''
            uxplay_source.main()
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 2)
