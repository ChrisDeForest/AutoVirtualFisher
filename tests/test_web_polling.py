import unittest
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch
from src.automation_web import render_panel, _level_line

class SelectivePanelTests(unittest.TestCase):
    def test_unknown_eta_is_not_a_minute(self):
        self.assertNotIn('to level 11', _level_line({'level':10}, {}))
        self.assertIn('about 2 min to level 11', _level_line({'level':10}, {'next_level_in_seconds':120}))

    def test_active_tab_skips_hidden_renderers(self):
        status = {'running': True, 'phase':'READY', 'reason':'Fishing.', 'trips':0,
                  'config':{'max_trips':None}}
        with patch('src.automation_web._model_tab', side_effect=AssertionError('hidden model rendered')), patch('src.automation_web._progress_tab', side_effect=AssertionError('hidden progress rendered')):
            html = render_panel(status, active_tab='run')
        self.assertIn('<h2>Profile</h2>', html)
        self.assertIn('data-tab="plan" hidden></section>', html)

    def test_plan_skips_run_rendering(self):
        status = {'running':True, 'phase':'READY', 'reason':'Fishing.'}
        html = render_panel(status, active_tab='plan')
        self.assertNotIn('<h2>Profile</h2>', html)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for browser script behavior tests')
    def test_browser_polling_behavior(self):
        result = subprocess.run([shutil.which('node'), 'tests/web_polling_behavior.js'],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_panel_rejects_oversized_query(self):
        from src.player_web import Handler
        class Request:
            path = '/automation/panel?' + '&'.join('x=1' for _ in range(6))
            automation = None
            automation_mode = None
            def with_disk_progress(self, status): return status
            def send_error(self, code, message): self.error = code
        request = Request()
        Handler.do_GET(request)
        self.assertEqual(request.error, 400)
