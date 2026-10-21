import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from paper2html.locking import directory_lock
from paper2html.server import main


class StartupTests(unittest.TestCase):
    def test_denied_port_reports_remedy_and_releases_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with patch('paper2html.server.Application') as application, patch(
                'paper2html.server.ThreadingHTTPServer',
                side_effect=PermissionError(10013, 'Access denied'),
            ), redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_:
                main(['--data-dir', tmp, '--port', '8765'])
            self.assertEqual(exit_.exception.code, 1)
            application.return_value.pool.shutdown.assert_called_once_with(wait=True)
            application.return_value.store.recover.assert_not_called()
            self.assertIn('--port 0', stderr.getvalue())
            self.assertIn('excludedportrange', stderr.getvalue())
            # A failed launch must not leave the data directory locked.
            with directory_lock(Path(tmp) / '.server.lock'):
                pass

    def test_invalid_port_is_rejected_before_application_creation(self):
        with patch('paper2html.server.Application') as application, redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exit_:
            main(['--port', '65536'])
        self.assertEqual(exit_.exception.code, 2)
        application.assert_not_called()
