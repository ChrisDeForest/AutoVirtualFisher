from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from src import player_cli
from src.player_snapshots import compare_latest, load_history, save_snapshot
from src.player_state import parse_responses


def run_cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = player_cli.main(list(args))
        except SystemExit as exit_:
            code = exit_.code
    return code or 0, out.getvalue(), err.getvalue()


class CliSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.root = self.folder / 'snapshots'
        self.profile = self.folder / 'profile.txt'
        self.profile.write_text('Inventory of BugParticle\nBalance: $100\nLevel: 2\n', encoding='utf-8')
        self.pet = self.folder / 'pet.txt'
        self.pet.write_text('You have no pets! Find pets by fishing.\n', encoding='utf-8')

    def tearDown(self):
        self.temporary.cleanup()

    def cli(self, *args):
        return run_cli(*args, '--snapshot-dir', str(self.root))

    def test_save_valid_profile_then_duplicate_is_unchanged(self):
        code, out, _ = self.cli('--profile', str(self.profile), '--save')
        self.assertEqual(code, 0)
        self.assertIn('Snapshot saved for BugParticle', out)
        code, out, _ = self.cli('--profile', str(self.profile), '--save')
        self.assertEqual(code, 0)
        self.assertIn('unchanged', out)
        self.assertEqual(len(load_history('BugParticle', self.root)[0]), 1)

    def test_partial_save_requires_account(self):
        code, _, err = self.cli('--pet', str(self.pet), '--save')
        self.assertNotEqual(code, 0)
        self.assertIn('--account', err)
        self.assertEqual(load_history('BugParticle', self.root)[0], [])
        code, out, _ = self.cli('--pet', str(self.pet), '--save', '--account', 'BugParticle')
        self.assertEqual(code, 0)
        self.assertIn('Partial snapshot saved for BugParticle', out)
        history = load_history('BugParticle', self.root)[0]
        self.assertEqual([(item['save_mode'], item['validity']) for item in history], [('manual', 'partial')])

    def test_conflicting_account_is_rejected(self):
        code, _, err = self.cli('--profile', str(self.profile), '--save', '--account', 'Someone Else')
        self.assertEqual(code, 1)
        self.assertIn('does not match', err)

    def test_json_mode_keeps_stdout_machine_readable(self):
        code, out, err = self.cli('--profile', str(self.profile), '--save', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['profile']['account_name'], 'BugParticle')
        self.assertIn('Snapshot saved', err)

    def test_history_and_compare_for_account(self):
        for balance in (100, 250):
            self.profile.write_text(f'Inventory of BugParticle\nBalance: ${balance}\nLevel: 2\n', encoding='utf-8')
            self.cli('--profile', str(self.profile), '--save')
        code, out, _ = self.cli('--history', '--account', 'BugParticle')
        self.assertEqual(code, 0)
        self.assertEqual(out.count('automatic'), 2)
        code, out, _ = self.cli('--compare', '--account', 'BugParticle')
        self.assertEqual(code, 0)
        self.assertIn('+150', out)
        code, out, _ = self.cli('--compare', '--account', 'BugParticle', '--json')
        self.assertEqual(json.loads(out)['changes']['balance']['delta'], 150)
        code, out, _ = self.cli('--history', '--account', 'BugParticle', '--json')
        self.assertEqual(len(json.loads(out)['snapshots']), 2)

    def test_history_requires_account_and_excludes_inputs(self):
        code, _, err = self.cli('--history')
        self.assertNotEqual(code, 0)
        self.assertIn('--account', err)
        code, _, err = self.cli('--compare', '--account', 'BugParticle', '--profile', str(self.profile))
        self.assertNotEqual(code, 0)
        self.assertIn('response files', err)


class CliFormattingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_format_history_lists_metadata_and_warnings(self):
        save_snapshot(parse_responses(profile='Inventory of A\nBalance: $1\nLevel: 1'), self.root)
        history, _ = load_history('A', self.root)
        text = player_cli.format_history('A', history, ['broken.json: bad'])
        self.assertIn(history[0]['captured_at'], text)
        self.assertIn('automatic', text)
        self.assertIn('full', text)
        self.assertIn('broken.json', text)
        self.assertIn('No snapshots', player_cli.format_history('B', [], []))

    def test_format_comparison_needs_two_full_snapshots(self):
        save_snapshot(parse_responses(profile='Inventory of A\nBalance: $1\nLevel: 1'), self.root)
        text = player_cli.format_comparison('A', compare_latest('A', self.root))
        self.assertIn('Another full snapshot is needed', text)
