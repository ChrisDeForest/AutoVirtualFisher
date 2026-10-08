import io
from pathlib import Path
import tempfile
import unittest
from src.run_output import RunOutput


class RunOutputTests(unittest.TestCase):
    def test_previous_run_block_identifies_action_and_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            console = io.StringIO()
            action = '20261007T154114.353417Z-f2577e12-00011-fish'
            with RunOutput(Path(folder), console=console) as output:
                output.update(dict(phase='NEEDS_REVIEW', run_id='new-run', trips=0,
                                   unresolved=[action], reason=action + ': Unreadable chest reply.'))
            self.assertIn('Earlier run', console.getvalue())
            self.assertIn(action, console.getvalue())
            self.assertIn('--resolve', console.getvalue())

    def test_log_matches_readable_console_and_keeps_only_five_owned_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'personal.txt').write_text('keep')
            for index in range(7):
                console = io.StringIO()
                with RunOutput(root, run_id=f'20261007T12000{index}.000000Z-abcd1234', console=console) as output:
                    output.say('Profile confirmed.')
                    path = output.path
                self.assertEqual(path.read_text(encoding='utf-8'), console.getvalue())
            self.assertEqual(len(list(root.glob('avf-run-*.log'))), 5)
            self.assertEqual((root / 'personal.txt').read_text(), 'keep')

    def test_status_uses_readable_messages_without_json_or_countdown_spam(self):
        with tempfile.TemporaryDirectory() as folder:
            console = io.StringIO()
            with RunOutput(Path(folder), console=console) as output:
                status = dict(phase='WAITING_REPLY', pending='run-refresh_profile', trips=0, last_result=None,
                              reason='Waiting for the reply to refresh_profile.', startup={'failures': {}})
                output.update(status)
                output.update(status)
                output.update({**status, 'phase': 'WAITING_COOLDOWN', 'reason': 'Next fish in 3.4 s.'})
                output.update({**status, 'phase': 'WAITING_COOLDOWN', 'reason': 'Next fish in 2.4 s.'})
                output.update({**status, 'phase': 'NEEDS_REVIEW', 'reason': 'Fishing result could not be read.'})
            text = console.getvalue()
            self.assertEqual(text.count('Reading /profile'), 1)
            self.assertIn('Needs review:', text)
            self.assertNotIn('{', text)
            self.assertNotIn('Next fish in', text)
