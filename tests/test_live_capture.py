import json
from pathlib import Path
import tempfile
import unittest

from src.fishing_sessions import load_sessions
from src.live_capture import LiveCapture, capture_messages, chat_lines, new_messages
from src.player_snapshots import load_history
from src.review_queue import dismiss, list_items

FIXTURES = Path(__file__).parent / 'fixtures'
WINDOW = json.loads((FIXTURES / 'capture_discord_window.json').read_text(encoding='utf-8'))


def fish(salmon, cod, xp, extra=''):
    return ['epicmouse used', '% fish', 'Virtual Fisher 11:40 AM', 'epicmouse9925', 'You caught:',
            f'{salmon} Salmon', f'{cod} Cod', f'+{xp} XP', 'Global boost by Someone!'] + \
           ([extra] if extra else []) + ['Fish Again', 'Sell', 'Return']


def reply(*lines):
    return ['epicmouse used', '% command', 'Virtual Fisher 11:41 AM', *lines]


PROFILE = reply('Inventory of epicmouse', 'Balance: $47,210.', 'P4 Level 30, 1,743/5,750 XP to next level.',
                'Current biome: River', 'Fish Inventory', '291 Fish', 'Fish Value: $291')
BUFFS = reply("epicmouse9925's current multipliers (including bait):", 'Sell price: 2.20x', 'Fish catch: 1.41x',
              'Fish quality: 1.21x', 'Treasure chance: 1.50x', 'Treasure quality: 1.13x', 'XP Multiplier: 2.12x',
              'Fishing cooldown: 3.5s.')
PET = reply('+14 pet XP per fishing trip (average)', 'Current pet: Dolphin.', 'Puffer(Lv130)',
            'Increases your fish catch.', 'XP to next level.')


def transformed(lines, factor, shift):
    """Simulate another resolution/zoom (factor) and a different layout to the left (shift)."""
    return [{**line, 'left': line['left'] * factor + shift, 'right': line['right'] * factor + shift,
             'top': line['top'] * factor, 'bottom': line['bottom'] * factor} for line in lines]


class ChatColumnTests(unittest.TestCase):
    def test_keeps_only_the_chat_column_in_reading_order(self):
        lines = chat_lines(WINDOW['lines'])
        for gone in ('Find or start a conversation', 'FriendA', 'Mutual Servers', '/verify', 'Commands'):
            self.assertNotIn(gone, lines)
        self.assertLess(lines.index('epicmouse used'), lines.index('% fish'))
        messages = capture_messages(lines)
        # The pet message's header is scrolled off the top, so it is cut off and skipped; the DM title
        # "Virtual Fisher [fish" must not pass for its header.
        self.assertEqual([message['kind'] for message in messages], ['fishing'])
        self.assertEqual(messages[0]['lines'], ['epicmouse9925', 'You caught:', '7 Salmon', '6 Cod', '+94 xp',
                                                'Global boost by BoostPlayerE!'])

    def test_no_layout_or_size_assumptions(self):
        expected = capture_messages(chat_lines(WINDOW['lines']))
        for factor, shift in ((0.6, 0), (0.6, -300), (1.3, 250), (1, -450)):
            with self.subTest(factor=factor, shift=shift):
                lines = chat_lines(transformed(WINDOW['lines'], factor, shift))
                self.assertEqual(capture_messages(lines), expected)

    def test_nothing_from_virtual_fisher_visible(self):
        sidebar_only = [line for line in WINDOW['lines'] if line['left'] < 500]
        self.assertEqual(chat_lines(sidebar_only), [])


class NewMessageTests(unittest.TestCase):
    def test_alignment(self):
        a, b, c, d = ('a',), ('b',), ('c',), ('d',)
        self.assertEqual(new_messages(None, [a, b]), [])
        self.assertEqual(new_messages([a, b], [a, b]), [])
        self.assertEqual(new_messages([a, b, c], [b, c, d]), [d])
        self.assertEqual(new_messages([a, b, a, b], [a, b, a, b, c]), [c])
        self.assertEqual(new_messages([a, b, c], [b, c, c, c]), [c, c], 'identical results both count')
        self.assertEqual(new_messages([a, b, c], [('b-partial',), c, d]), [d], 'a cut-off top message is tolerated')
        self.assertIsNone(new_messages([a, b, c], [d, ('e',)]), 'no overlap means the view moved')


class LiveRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.snapshots, self.sessions, self.review = root / 'snapshots', root / 'sessions', root / 'review'
        self.screens = []
        self.live = LiveCapture(self.snapshots, self.sessions, self.review, capture=self.next_screen)

    def tearDown(self):
        self.temporary.cleanup()

    def next_screen(self):
        return {'status': 'ok', 'lines': self.screens.pop(0)}

    def run_screens(self, *screens, bait='Worms'):
        self.screens = [sum(screen, []) for screen in screens]
        self.live.start('epicmouse', bait, thread=False)
        while self.screens:
            self.live.step()
        return self.live.status()

    def test_baseline_is_not_saved_and_new_fishing_is(self):
        status = self.run_screens([fish(7, 6, 94)], [fish(7, 6, 94), fish(8, 6, 98), fish(8, 6, 98)])
        sessions = load_sessions('epicmouse', self.sessions)[0]
        self.assertEqual(len(sessions), 1)
        tally = sessions[0]['tally']
        self.assertEqual(tally['trips'], 2, 'two identical results are two trips')
        self.assertEqual([trip['bait'] for trip in tally['trip_details']], ['Worms', 'Worms'])
        self.assertEqual(status['counts']['trips'], 2)

    def test_bait_stays_off_after_running_out(self):
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), fish(2, 2, 20, 'You ran out of worms.')],
                         [fish(2, 2, 20, 'You ran out of worms.'), fish(3, 3, 30)])
        trips = [trip for session in reversed(load_sessions('epicmouse', self.sessions)[0])
                 for trip in session['tally']['trip_details']]
        self.assertEqual([trip['bait'] for trip in trips], ['Worms', None])

    def test_profile_and_buffs_become_a_snapshot(self):
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), PROFILE], [PROFILE, BUFFS])
        history = load_history('epicmouse', self.snapshots)[0]
        self.assertEqual(history[0]['validity'], 'full')
        self.assertEqual(history[0]['state']['buffs']['sell_price'], {'value': 2.2, 'unit': 'multiplier'})
        self.assertEqual(history[0]['state']['profile']['balance'], 47210)

    def test_problem_messages_go_to_review(self):
        other_profile = reply('Inventory of someoneelse', 'Balance: $5.', 'Level: 3')
        mystery = reply('A sea serpent appears! Type /catch to try.')
        harmless = reply("You don't have enough Emerald Fish.")
        odd_fish = fish(1, 1, 10, 'Something strange happened')
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), PET, other_profile, mystery, harmless, odd_fish])
        items = list_items(self.review)[0]
        reasons = {item['kind']: item['reasons'] for item in items}
        self.assertEqual(set(reasons), {'pet', 'profile', 'unknown', 'fishing'})
        self.assertTrue(any('XP' in reason for reason in reasons['pet']))
        self.assertTrue(any('someoneelse' in reason for reason in reasons['profile']))
        self.assertTrue(any('Something strange happened' in reason for reason in reasons['fishing']))
        self.assertEqual(self.live.status()['counts']['ignored'], 1)
        self.assertEqual(load_sessions('epicmouse', self.sessions)[0], [])
        dismiss(self.review, items[0]['id'])
        self.assertEqual(len(list_items(self.review)[0]), 3)

    def test_scrolling_away_pauses_without_saving(self):
        status = self.run_screens([fish(1, 1, 10), fish(2, 2, 20)], [PROFILE, BUFFS])
        self.assertIn('paused', status['state'].lower())
        self.assertEqual(load_history('epicmouse', self.snapshots)[0], [])

    def test_brief_occlusion_resumes_without_losing_trips(self):
        covered = [fish(1, 1, 10)[:6]]  # bottom of the message hidden, e.g. by the slash-command menu
        self.run_screens([fish(1, 1, 10)], *([covered] * 3), [fish(1, 1, 10), fish(2, 2, 20)])
        sessions = load_sessions('epicmouse', self.sessions)[0]
        self.assertEqual([session['tally']['trips'] for session in sessions], [1])

    def test_long_view_change_flags_instead_of_saving_or_skipping(self):
        from src.live_capture import RESYNC_AFTER
        moved = [fish(5, 5, 50), PROFILE]
        self.run_screens([fish(1, 1, 10)], *([moved] * RESYNC_AFTER))
        self.assertEqual(load_sessions('epicmouse', self.sessions)[0], [])
        self.assertEqual(load_history('epicmouse', self.snapshots)[0], [])
        items = list_items(self.review)[0]
        self.assertEqual(sorted(item['kind'] for item in items), ['fishing', 'profile'])
        self.assertTrue(all('may already be saved' in item['reasons'][0] for item in items))
        self.assertIn('Watching', self.live.status()['state'])

    def test_window_problems_are_reported(self):
        self.live = LiveCapture(self.snapshots, self.sessions, self.review,
                                capture=lambda: {'status': 'minimized', 'lines': []})
        self.live.start('epicmouse', None, thread=False)
        self.live.step()
        self.assertIn('minimized', self.live.status()['state'].lower())


if __name__ == '__main__':
    unittest.main()
