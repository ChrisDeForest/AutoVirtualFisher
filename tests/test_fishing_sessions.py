import json
from pathlib import Path
import tempfile
import unittest

from src.fishing_log import fingerprint, parse_fishing
from src.fishing_sessions import fishing_totals, load_sessions, save_session
from src.player_snapshots import account_key

LIVE = (Path(__file__).parent / 'fixtures' / 'live_fishing.txt').read_text(encoding='utf-8')


def batch(text):
    return parse_fishing(text), fingerprint(text)


class FishingSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_save_writes_tally_without_pasted_text(self):
        tally, digest = batch(LIVE + '\nA mysterious line')
        result = save_session(tally, 'epicmouse', digest, self.root)
        self.assertEqual(result['status'], 'saved')
        files = list(self.root.rglob('*.json'))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].parent.name, account_key('epicmouse'))
        text = files[0].read_text(encoding='utf-8')
        self.assertNotIn('mysterious', text)
        self.assertNotIn('epicmouse9925', text)
        stored = json.loads(text)
        self.assertEqual(stored['account_name'], 'epicmouse')
        self.assertEqual(stored['source_fingerprint'], digest)
        self.assertEqual(stored['tally']['trips'], 6)
        self.assertEqual(stored['tally']['unparsed_line_count'], 1)
        self.assertNotIn('warnings', stored['tally'])

    def test_same_batch_is_not_saved_twice(self):
        tally, digest = batch(LIVE)
        self.assertEqual(save_session(tally, 'epicmouse', digest, self.root)['status'], 'saved')
        self.assertEqual(save_session(tally, 'EPICMOUSE', digest, self.root)['status'], 'duplicate')
        self.assertEqual(len(list(self.root.rglob('*.json'))), 1)
        self.assertEqual(save_session(tally, 'other', digest, self.root)['status'], 'saved')

    def test_rejects_missing_account_and_empty_batches(self):
        tally, digest = batch(LIVE)
        self.assertEqual(save_session(tally, '  ', digest, self.root)['status'], 'rejected')
        empty, empty_digest = batch('Your cooldown: 3.5s')
        result = save_session(empty, 'epicmouse', empty_digest, self.root)
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('No fishing trips', result['reason'])
        self.assertEqual(list(self.root.rglob('*.json')), [])

    def test_load_skips_malformed_files_with_warning(self):
        tally, digest = batch(LIVE)
        save_session(tally, 'epicmouse', digest, self.root)
        (self.root / account_key('epicmouse') / 'broken.json').write_text('{', encoding='utf-8')
        sessions, warnings = load_sessions('epicmouse', self.root)
        self.assertEqual(len(sessions), 1)
        self.assertTrue(any('broken.json' in warning for warning in warnings))

    def test_totals_combine_sessions_and_compute_rates(self):
        second = 'You caught:\n4 Cod\n+50 XP\nYou found an artifact box\n\nYou caught:\n2 Cod\n+30 XP'
        for text in (LIVE, second):
            tally, digest = batch(text)
            self.assertEqual(save_session(tally, 'epicmouse', digest, self.root)['status'], 'saved')
        totals = fishing_totals('epicmouse', self.root)
        self.assertEqual(totals['sessions'], 2)
        self.assertEqual(totals['trips'], 8)
        self.assertEqual(totals['catches'], {'Fish': 6, 'Salmon': 48, 'Cod': 53})
        self.assertEqual(totals['fish_caught'], 107)
        self.assertAlmostEqual(totals['fish_per_trip'], 107 / 8)
        self.assertEqual(totals['fishing_xp'], 711)
        self.assertAlmostEqual(totals['xp_per_trip'], 711 / 8)
        self.assertEqual(totals['finds']['artifact box'], 2)
        self.assertAlmostEqual(totals['finds_per_100_trips']['artifact box'], 25.0)
        self.assertEqual(totals['chest_contents'], {'Gold Fish': 2})
        self.assertEqual(totals['quests_completed'], 2)
        self.assertEqual(totals['quest_money'], 430)
        self.assertEqual(totals['quest_xp'], 330)
        self.assertEqual(totals['level_ups'], 1)
        self.assertEqual(totals['level_up_money'], 6000)

    def test_totals_for_unknown_account_have_no_rates(self):
        totals = fishing_totals('nobody', self.root)
        self.assertEqual(totals['sessions'], 0)
        self.assertIsNone(totals['fish_per_trip'])
        self.assertIsNone(totals['xp_per_trip'])


if __name__ == '__main__':
    unittest.main()
