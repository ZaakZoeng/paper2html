import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from pathlib import Path

from paper2html import __version__
from paper2html.server import Application, handler_for, version_status


class VersionGuardTests(unittest.TestCase):
    def test_disk_version_is_reported_without_executing_source(self):
        with patch.object(Path, 'read_text', return_value='__version__ = "future-test"\nraise RuntimeError("must not execute")'):
            state = version_status()
        self.assertEqual(state['version'], __version__)
        self.assertEqual(state['source_version'], 'future-test')
        self.assertTrue(state['restart_required'])

    def test_current_release_and_unavailable_file_do_not_require_restart(self):
        self.assertFalse(version_status()['restart_required'])
        with patch.object(Path, 'read_text', side_effect=OSError):
            self.assertFalse(version_status()['restart_required'])

    def test_stale_service_reports_version_and_blocks_job_before_submit(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Application(Path(tmp)/'data', reports_dir=Path(tmp)/'reports')
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(app))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = 'http://127.0.0.1:' + str(server.server_port)
            try:
                with patch('paper2html.server.version_status', return_value={
                    'version': 'old-test', 'source_version': 'new-test', 'restart_required': True
                }), patch.object(app, 'submit') as submit:
                    with urlopen(base+'/api/config') as response:
                        config = json.load(response)
                    self.assertTrue(config['restart_required'])
                    self.assertEqual(config['source_version'], 'new-test')
                    request = Request(base+'/api/jobs', data=b'{}', headers={
                        'Content-Type': 'application/json', 'X-Paper2HTML-Token': app.token,
                        'Origin': base})
                    with self.assertRaises(HTTPError) as error:
                        urlopen(request)
                    self.assertEqual(error.exception.code, 409)
                    self.assertIn('Ctrl+C', json.load(error.exception)['error'])
                    submit.assert_not_called()
                    self.assertEqual(app.store.list_jobs(), [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                app.pool.shutdown(wait=True)
