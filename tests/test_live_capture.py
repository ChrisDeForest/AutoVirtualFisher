import json
import sys
from pathlib import Path
import tempfile
import unittest

from src.fishing_sessions import load_sessions
from src.live_capture import LiveCapture, align, capture_messages, chat_lines, new_messages
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


def frame(*entries, fragment=()):
    return {'messages': [{'key': (key,), 'lines': [f'{key} line 1', f'{key} line 2'], 'sent': sent}
                         for key, sent in (entry if isinstance(entry, tuple) else (entry, None) for entry in entries)],
            'fragment': tuple(fragment)}


class AlignmentCertaintyTests(unittest.TestCase):
    def test_unchanged_identical_screen_without_context_is_ambiguous(self):
        result = align(frame('A', 'A'), frame('A', 'A'))
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual((result['start'], result['earliest_start']), (2, 1), 'zero or one new; never guessed')

    def test_identical_replacement_after_scroll_is_seen_from_the_content_above(self):
        result = align(frame('A', 'A', fragment=['Z line 2']), frame('A', 'A', fragment=['A line 2']))
        self.assertEqual((result['status'], result['start']), ('certain', 1))

    def test_unchanged_identical_screen_with_context_above_is_certain(self):
        result = align(frame('A', 'A', fragment=['Z line 2']), frame('A', 'A', fragment=['Z line 2']))
        self.assertEqual((result['status'], result['start']), ('certain', 2))

    def test_send_times_tell_identical_replies_apart(self):
        result = align(frame(('A', '11:40 AM'), ('A', '11:40 AM')), frame(('A', '11:40 AM'), ('A', '11:41 AM')))
        self.assertEqual((result['status'], result['start']), ('certain', 1))

    def test_three_equal_replies_with_distinguishable_overlap(self):
        result = align(frame('B', 'C', 'A'), frame('C', 'A', 'A', 'A'))
        self.assertEqual((result['status'], result['start']), ('certain', 2))

    def test_cropped_top_reply(self):
        current = frame('B-cut', 'C', 'D')
        current['messages'][0]['lines'] = ['B line 2']  # only the bottom of B is still readable
        result = align(frame('A', 'B', 'C'), current)
        self.assertEqual((result['status'], result['start']), ('certain', 2))
        self.assertEqual(align(frame('A', 'B', 'C'), frame('X', 'C', 'D'))['status'], 'lost',
                         'an unrelated top message is not taken for a cut-off one')

    def test_no_overlap_is_lost(self):
        self.assertEqual(align(frame('A', 'B'), frame('C', 'D'))['status'], 'lost')


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

    def run_screens(self, *screens, bait='Worms', stop=True):
        """Run one step per screen, then stop (which processes the held newest message).

        Returns the status from just before stopping; self.states records the state after each step.
        """
        self.screens = [sum(screen, []) for screen in screens]
        self.states = []
        self.live.start('epicmouse', bait, thread=False, username='epicmouse9925')
        while self.screens:
            self.live.step()
            self.states.append(self.live.status()['state'])
        status = self.live.status()
        if stop:
            self.live.stop()
        return status

    def saved_trips(self):
        return sum(session['tally']['trips'] for session in load_sessions('epicmouse', self.sessions)[0])

    def test_message_box_text_does_not_break_alignment(self):
        typing = fish(2, 2, 20)[:-3] + ['fishing is fun']  # newest result with the message box text below it
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), typing], [fish(1, 1, 10), typing[:-1] + ['/fi']],
                         [fish(1, 1, 10), fish(2, 2, 20)])
        self.assertFalse(any(state.startswith('Paused') for state in self.states), self.states)
        self.assertEqual(self.saved_trips(), 1)
        self.assertEqual(list_items(self.review)[0], [])

    def test_newest_message_waits_until_it_reads_the_same_twice(self):
        typing = fish(2, 2, 20)[:-3] + ['fishing is fun']
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), typing], stop=False)
        self.assertEqual(self.saved_trips(), 0, 'held while it may still change')
        self.screens = [sum([fish(1, 1, 10), fish(2, 2, 20)], [])] * 2
        self.live.step()
        self.assertEqual(self.saved_trips(), 0, 'it changed, so it is held again')
        self.live.step()
        self.assertEqual(self.saved_trips(), 1, 'the same in two captures')
        self.live.stop()
        self.assertEqual(self.saved_trips(), 1, 'not saved twice on stop')

    def test_baseline_is_not_saved_and_new_fishing_is(self):
        self.run_screens([fish(7, 6, 94)], [fish(7, 6, 94), fish(8, 6, 98), fish(8, 6, 98)])
        trips = [trip for session in load_sessions('epicmouse', self.sessions)[0]
                 for trip in session['tally']['trip_details']]
        self.assertEqual(len(trips), 2, 'two identical results are two trips; the baseline is not saved')
        self.assertEqual([trip['catches'] for trip in trips], [{'Salmon': 8, 'Cod': 6}] * 2)
        self.assertEqual([trip.get('bait', 'not recorded') for trip in trips], ['not recorded'] * 2,
                         'the bait chosen on the page is an assumption, not a recorded fact')
        session = load_sessions('epicmouse', self.sessions)[0][0]
        self.assertEqual((session['tally']['assumed_bait'], session['tally']['bait_status']), ('Worms', 'assumed'))
        self.assertEqual(self.live.status()['counts']['trips'], 2)

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

    def test_buffs_do_not_make_an_old_profile_look_fresh(self):
        from datetime import datetime, timedelta, timezone
        from src.player_snapshots import growth_series
        moments = iter(datetime(2026, 10, 6, 12, tzinfo=timezone.utc) + timedelta(minutes=step) for step in range(10))
        self.live.clock = lambda: next(moments)
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), PROFILE], [PROFILE, BUFFS])
        history = load_history('epicmouse', self.snapshots)[0]
        sections = history[0]['sections']
        # PROFILE was first seen in the second capture (12:01) and BUFFS in the third (12:02).
        self.assertEqual(sections['profile']['observed_at'], '2026-10-06T12:01:00+00:00')
        self.assertEqual(sections['buffs']['observed_at'], '2026-10-06T12:02:00+00:00')
        self.assertEqual(sections['buffs']['identity_status'], 'verified')
        self.assertEqual(len(growth_series('epicmouse', self.snapshots)['series']['balance']), 1)

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
        status = self.run_screens([fish(1, 1, 10)], *([moved] * RESYNC_AFTER))
        self.assertEqual(load_sessions('epicmouse', self.sessions)[0], [])
        self.assertEqual(load_history('epicmouse', self.snapshots)[0], [])
        items = list_items(self.review)[0]
        self.assertEqual(sorted(item['kind'] for item in items), ['fishing', 'profile'])
        self.assertTrue(all('may already be saved' in item['reasons'][0] for item in items))
        self.assertIn('Watching', status['state'])

    def test_another_players_fishing_saves_nothing(self):
        theirs = ['someoneelse used', '% fish', 'Virtual Fisher 11:41 AM', 'someoneelse77', 'You caught:',
                  '9 Salmon', '+90 XP']
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), theirs, fish(2, 2, 20)])
        self.assertEqual(self.saved_trips(), 1, 'only the configured player’s result is saved')
        items = list_items(self.review)[0]
        self.assertEqual(len(items), 1)
        self.assertIn('someoneelse', items[0]['reasons'][0])
        self.assertEqual(items[0]['identity_status'], 'mismatch')

    def test_another_players_buffs_do_not_change_state(self):
        theirs = reply("someoneelse77's current multipliers (including bait):", 'Sell price: 9.00x')
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), PROFILE], [PROFILE, theirs])
        history = load_history('epicmouse', self.snapshots)[0]
        self.assertEqual(len(history), 1)
        self.assertIsNone(history[0]['state']['buffs']['sell_price'])

    def test_reply_without_a_player_name_goes_to_review(self):
        unnamed = ['Virtual Fisher 11:41 AM', 'You have no pets!']
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), unnamed])
        self.assertEqual(load_history('epicmouse', self.snapshots)[0], [])
        items = list_items(self.review)[0]
        self.assertEqual([item['identity_status'] for item in items], ['unknown'])
        self.assertIn('cannot be confirmed', items[0]['reasons'][0])

    def test_command_marker_confirms_a_reply_without_a_name(self):
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), reply('You have no pets!')])
        history = load_history('epicmouse', self.snapshots)[0]
        self.assertEqual(history[0]['state']['pets']['status'], 'No pets')

    def test_display_name_and_username_must_both_match_their_own_field(self):
        swapped = ['epicmouse9925 used', '% fish', 'Virtual Fisher 11:41 AM', 'epicmouse', 'You caught:',
                   '9 Salmon', '+90 XP']
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), swapped, fish(2, 2, 20)])
        self.assertEqual(self.saved_trips(), 1)
        self.assertEqual(len(list_items(self.review)[0]), 1)

    def test_another_bot_with_the_same_text_is_not_virtual_fisher(self):
        fake = ['Fake Fisher APP 11:41 AM', 'epicmouse9925', 'You caught:', '9 Salmon', '+90 XP']
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), fake], [fish(1, 1, 10), fake, fish(2, 2, 20)])
        trips = [trip for session in load_sessions('epicmouse', self.sessions)[0]
                 for trip in session['tally']['trip_details']]
        self.assertEqual([trip['catches'] for trip in trips], [{'Salmon': 2, 'Cod': 2}])

    def test_trips_record_conditions_from_the_confirmed_profile(self):
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), fish(2, 2, 20), PROFILE, fish(3, 3, 30), fish(4, 4, 40)])
        sessions = list(reversed(load_sessions('epicmouse', self.sessions)[0]))
        self.assertNotIn('conditions', sessions[0]['tally'], 'no profile had been seen yet')
        self.assertEqual(sessions[-1]['tally']['conditions']['biome'], 'River')

    def test_saved_trips_record_their_observation(self):
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), fish(2, 2, 20)])
        session = load_sessions('epicmouse', self.sessions)[0][0]
        provenance = session['provenance']
        self.assertEqual((provenance['source'], provenance['identity_status']), ('live_capture', 'verified'))
        self.assertEqual(len(provenance['observations']), 1)

    def test_another_channel_pauses_until_capture_returns_to_the_first(self):
        dm, other = {'window': 7, 'title': '@Virtual Fisher - Discord'}, {'window': 7, 'title': '#fishing | Club - Discord'}
        screens = [(dm, [fish(1, 1, 10)]), (other, [fish(1, 1, 10), fish(5, 5, 50), fish(5, 5, 50)]),
                   (dm, [fish(1, 1, 10), fish(2, 2, 20)]), (dm, [fish(1, 1, 10), fish(2, 2, 20)])]
        self.screens = [{'status': 'ok', 'lines': sum(lines, []), 'context': context} for context, lines in screens]
        self.live.capture = lambda: self.screens.pop(0)
        self.live.start('epicmouse', 'Worms', thread=False, username='epicmouse9925')
        self.live.step()
        self.live.step()
        self.assertIn('different', self.live.status()['state'])
        self.live.step()
        self.live.step()
        self.live.stop()
        trips = [trip['catches'] for session in load_sessions('epicmouse', self.sessions)[0]
                 for trip in session['tally']['trip_details']]
        self.assertEqual(trips, [{'Salmon': 2, 'Cod': 2}], 'nothing from the other channel is saved')

    def test_ambiguous_repeat_is_flagged_once_and_never_counted(self):
        status = self.run_screens([fish(1, 1, 10), fish(1, 1, 10)], [fish(1, 1, 10), fish(1, 1, 10)],
                                  [fish(1, 1, 10), fish(1, 1, 10)])
        self.assertEqual(self.saved_trips(), 0)
        self.assertEqual(status['alignment']['status'], 'ambiguous')
        self.assertTrue(status['unresolved'])
        items = list_items(self.review)[0]
        self.assertEqual(len(items), 1, 'the same evidence is not queued again')
        self.assertIn('may be new or a repeat', items[0]['reasons'][0])

    def test_repeat_confirmed_by_content_above_is_counted(self):
        status = self.run_screens([['An older reply line'], fish(1, 1, 10), fish(1, 1, 10)],
                                  [['Global boost by Someone!'], fish(1, 1, 10), fish(1, 1, 10)])
        self.assertEqual(status['alignment']['status'], 'certain')
        self.assertEqual(self.saved_trips(), 1)

    def test_missed_captures_do_not_lose_or_double_count(self):
        screens = [[fish(1, 1, 10)], None, None, [fish(1, 1, 10), fish(2, 2, 20), fish(3, 3, 30)]]
        self.screens = [{'status': 'blank', 'lines': []} if screen is None else {'status': 'ok', 'lines': sum(screen, [])}
                        for screen in screens]
        self.live.capture = lambda: self.screens.pop(0)
        self.live.start('epicmouse', 'Worms', thread=False, username='epicmouse9925')
        while self.screens:
            self.live.step()
        self.live.stop()
        self.assertEqual(self.saved_trips(), 2)

    def test_channel_change_with_identical_content_is_not_read(self):
        dm, other = {'window': 7, 'title': '@Virtual Fisher - Discord'}, {'window': 7, 'title': '#fish | Club - Discord'}
        self.screens = [{'status': 'ok', 'lines': fish(1, 1, 10), 'context': dm},
                        {'status': 'ok', 'lines': fish(1, 1, 10) + fish(2, 2, 20), 'context': other},
                        {'status': 'ok', 'lines': fish(1, 1, 10) + fish(2, 2, 20), 'context': other}]
        self.live.capture = lambda: self.screens.pop(0)
        self.live.start('epicmouse', 'Worms', thread=False, username='epicmouse9925')
        while self.screens:
            self.live.step()
        self.live.stop()
        self.assertEqual(self.saved_trips(), 0)

    def trip_baits(self):
        return [trip.get('bait', 'not recorded') for session in reversed(load_sessions('epicmouse', self.sessions)[0])
                for trip in session['tally']['trip_details']]

    def test_profile_bait_switch_applies_to_later_trips_only(self):
        leeches = PROFILE[:-3] + ['Bait: Leeches (5)'] + PROFILE[-3:]
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), fish(2, 2, 20), leeches, fish(3, 3, 30), fish(4, 4, 40)])
        self.assertEqual(self.trip_baits(), ['not recorded', 'Leeches', 'Leeches'])
        self.assertEqual(self.live.status()['bait_state'], {'bait': 'Leeches', 'status': 'verified'})

    def test_confirmed_selection_replaces_the_assumed_bait(self):
        self.assertEqual(self.run_screens([fish(1, 1, 10)], bait='')['bait_state']['status'], 'unknown')
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), reply('You are already using Worms'), fish(2, 2, 20),
                                            reply('Current bait: NONE'), fish(3, 3, 30), fish(4, 4, 40)], bait='')
        self.assertEqual(self.trip_baits(), ['Worms', None, None])

    def test_depletion_then_replenishment(self):
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), fish(2, 2, 20, 'You ran out of worms.'), fish(3, 3, 30),
                                            reply('Bait Shop', 'Selected: Worms (10)'), fish(4, 4, 40), fish(5, 5, 50)])
        self.assertEqual(self.trip_baits(), ['Worms', None, 'Worms', 'Worms'])

    def test_unreadable_depleted_trip_still_ends_the_bait(self):
        broken = fish(2, 2, 20, 'You ran out of worms.')
        broken[5] = 'Salmon'  # count lost: the trip goes to review
        status = self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), broken, fish(3, 3, 30), fish(4, 4, 40)])
        self.assertEqual(self.trip_baits(), [None, None])
        self.assertEqual(status['bait_state'], {'bait': None, 'status': 'verified'})
        self.assertEqual([item['kind'] for item in list_items(self.review)[0]], ['fishing'])

    def test_another_players_bait_confirmation_changes_nothing(self):
        theirs = ['someoneelse used', '% bait', 'Virtual Fisher 11:41 AM', 'You are already using Leeches']
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), theirs, fish(2, 2, 20), fish(3, 3, 30)])
        self.assertEqual(self.trip_baits(), ['not recorded', 'not recorded'])
        self.assertEqual(self.live.status()['bait_state']['status'], 'assumed')

    def test_depletion_names_the_bait_that_was_in_use(self):
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), fish(2, 2, 20, 'You ran out of leeches.'),
                                            fish(3, 3, 30), fish(4, 4, 40)])
        self.assertEqual(self.trip_baits(), ['Leeches', None, None],
                         'the game says which bait ran out, which beats the assumed Worms')

    def test_depletion_contradicting_confirmed_bait_leaves_that_trip_unknown(self):
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), reply('You are already using Worms'),
                                            fish(2, 2, 20, 'You ran out of leeches.'), fish(3, 3, 30), fish(4, 4, 40)])
        self.assertEqual(self.trip_baits(), ['not recorded', None, None])

    def test_restart_during_a_blocked_capture_discards_the_old_result(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        old_screen = fish(1, 1, 10) + fish(9, 9, 90)

        def blocked():
            entered.set()
            release.wait(5)
            return {'status': 'ok', 'lines': old_screen}

        self.live = LiveCapture(self.snapshots, self.sessions, self.review, capture=blocked, interval=60)
        self.live.start('epicmouse', 'Worms', username='epicmouse9925')
        self.assertTrue(entered.wait(5))
        old_worker = self.live.thread
        self.live.stop(timeout=0.1)  # the old worker is still inside its capture
        self.live.capture = lambda: {'status': 'ok', 'lines': fish(5, 5, 50)}
        self.live.start('epicmouse', 'Worms', thread=False, username='epicmouse9925')
        release.set()
        old_worker.join(5)
        self.assertFalse(old_worker.is_alive(), 'the old worker ended instead of being revived')
        self.assertEqual(self.live.status()['last_capture'], None, 'its late capture was not accepted')
        self.live.step()
        self.live.stop()
        self.assertEqual(self.saved_trips(), 0)

    def test_continuous_fishing_at_the_cooldown_loses_nothing(self):
        from src.live_capture import DEFAULT_INTERVAL
        cooldown, visible = 3.5, 3  # fixture cooldown; a small window that shows three results
        replies = [fish(n % 9 + 1, n % 7 + 1, 10 + n) for n in range(60)]
        screens, arrived, moment = [], 0, 0.0
        while arrived < len(replies):
            arrived = min(len(replies), int(moment / cooldown) + 1)
            screens.append(replies[max(0, arrived - visible):arrived])
            moment += DEFAULT_INTERVAL
        per_capture = max(len(range(int(t / cooldown), int((t + DEFAULT_INTERVAL) / cooldown)))
                          for t in [i * DEFAULT_INTERVAL for i in range(100)])
        self.assertLess(per_capture, visible, 'every capture still overlaps the previous one')
        status = self.run_screens(*screens)
        self.assertFalse(any(state.startswith('Paused') for state in self.states))
        self.assertEqual(status['alignment']['status'], 'certain')
        self.assertEqual(self.saved_trips(), len(replies) - 1, 'every reply after the first capture')

    def buffs(self, cooldown):
        return reply("epicmouse9925's current multipliers (including bait):", 'Sell price: 2.20x',
                     f'Fishing cooldown: {cooldown}s.')

    def test_capture_pace_follows_the_observed_cooldown(self):
        from src.live_capture import MAX_INTERVAL, MIN_INTERVAL
        self.assertEqual(self.run_screens([fish(1, 1, 10)], stop=False)['interval'], MAX_INTERVAL,
                         'no cooldown known yet: the safe default')
        self.screens = [sum([fish(1, 1, 10), self.buffs(2.0), fish(2, 2, 20)], [])]
        self.live.step()
        status = self.live.status()
        self.assertEqual(status['interval'], 1.5, 'three quarters of a 2 s cooldown')
        self.assertIn('2', status['interval_reason'])
        self.screens = [sum([fish(2, 2, 20), self.buffs(5.0), fish(3, 3, 30)], [])]
        self.live.step()
        self.assertEqual(self.live.status()['interval'], MAX_INTERVAL, 'after prestige: slower, but capped')
        self.screens = [sum([fish(3, 3, 30), self.buffs(0.5), fish(4, 4, 40)], [])]
        self.live.step()
        self.assertEqual(self.live.status()['interval'], MIN_INTERVAL, 'never faster than OCR can keep up')
        self.live.stop()

    def test_start_uses_the_saved_cooldown(self):
        from src.player_snapshots import save_snapshot
        from src.player_state import parse_responses
        save_snapshot(parse_responses(profile='\n'.join(PROFILE[3:]), buffs='\n'.join(self.buffs(2.4)[3:])),
                      self.snapshots)
        self.assertEqual(self.run_screens([fish(1, 1, 10)])['interval'], 1.8)

    def test_several_new_replies_in_one_capture_speed_capture_up(self):
        from src.live_capture import MAX_INTERVAL
        status = self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), fish(2, 2, 20), fish(3, 3, 30), fish(4, 4, 40)],
                                  stop=False)
        self.assertLess(status['interval'], MAX_INTERVAL / 2, 'three new replies: capture three times as often')
        self.assertIn('arriving faster', status['interval_reason'])
        self.live.stop()

    def test_faster_cooldown_without_buffs_loses_nothing(self):
        cooldown, visible = 1.6, 3  # quicker than the default interval assumes, and never shown in /buffs
        replies = [fish(n % 9 + 1, n % 7 + 1, 10 + n) for n in range(60)]
        self.live.start('epicmouse', 'Worms', thread=False, username='epicmouse9925')
        moment, arrived, paused = 0.0, 0, []
        while arrived < len(replies):
            arrived = min(len(replies), int(moment / cooldown) + 1)
            self.screens = [sum(replies[max(0, arrived - visible):arrived], [])]
            self.live.step()
            paused.append(self.live.status()['state'].startswith('Paused'))
            moment += self.live.status()['interval']
        self.live.stop()
        self.assertFalse(any(paused))
        self.assertEqual(self.saved_trips(), len(replies) - 1)

    def test_review_items_about_you_block_actions_until_dismissed(self):
        broken = fish(2, 2, 20)
        broken[5] = 'Salmon'
        theirs = ['someoneelse used', '% fish', 'Virtual Fisher 11:41 AM', 'someoneelse77', 'You caught:',
                  '9 Salmon', '+90 XP']
        status = self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), theirs, broken, fish(3, 3, 30)], stop=False)
        self.assertEqual(status['counts']['flagged'], 2)
        self.assertEqual(status['unresolved'], 1, 'your unreadable trip blocks; another player\'s reply does not')
        mine = next(item for item in list_items(self.review)[0] if item['kind'] == 'fishing'
                    and item['identity_status'] == 'verified')
        dismiss(self.review, mine['id'])
        self.assertEqual(self.live.status()['unresolved'], 0)
        self.live.stop()

    def test_capture_without_a_fresh_overlap_is_never_certain(self):
        from datetime import datetime, timezone
        from src.automation_state import automation_view
        dm = {'window': 7, 'title': '@Virtual Fisher - Discord', 'channel': 'Virtual Fisher'}
        other = {**dm, 'channel': 'Fishing Club'}
        ok = lambda *screens: {'status': 'ok', 'lines': sum(screens, []), 'context': dm}
        cases = [(ok(fish(1, 1, 10)), 'certain'),
                 (ok(['Some other conversation']), 'lost'),           # no Virtual Fisher messages visible
                 (ok(fish(1, 1, 10)), 'certain'),                     # overlap established again
                 ({'status': 'blank', 'lines': []}, 'unknown'),
                 (ok(fish(1, 1, 10), fish(2, 2, 20)), 'certain'),
                 ({'status': 'ok', 'lines': fish(1, 1, 10), 'context': other}, 'unknown'),
                 (ok(fish(1, 1, 10), fish(2, 2, 20), fish(3, 3, 30)), 'certain')]
        self.screens = [capture for capture, _ in cases]
        self.live.capture = lambda: self.screens.pop(0)
        self.live.clock = lambda: datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        self.live.start('epicmouse', 'Worms', thread=False, username='epicmouse9925')
        fresh_snapshot = {'schema_version': 2, 'captured_at': '', 'state': {}, 'sections': {}}
        for capture, expected in cases:
            self.live.step()
            status = self.live.status()
            self.assertEqual(status['alignment']['status'], expected, capture)
            view = automation_view(fresh_snapshot, datetime(2026, 10, 6, 12, tzinfo=timezone.utc), live=status)
            self.assertEqual(view['actions_allowed'], expected == 'certain', (capture, view['blockers']))
        self.live.stop()
        self.assertEqual(self.saved_trips(), 2, 'the empty and unreadable captures lost nothing')

    def test_capture_and_channel_status_are_reported(self):
        dm = {'window': 7, 'title': '@Virtual Fisher - Discord', 'channel': 'Virtual Fisher'}
        cases = [({'status': 'blank', 'lines': []}, 'blank', None),
                 ({'status': 'ok', 'lines': fish(1, 1, 10), 'context': dm}, 'ok', 'verified'),
                 ({'status': 'ok', 'lines': fish(1, 1, 10), 'context': {**dm, 'channel': None}}, 'ok', 'unverified'),
                 ({'status': 'ok', 'lines': fish(1, 1, 10), 'context': {**dm, 'channel': 'Fishing Club'}},
                  'other_context', 'changed'),
                 ({'status': 'ok', 'lines': fish(1, 1, 10), 'context': dm}, 'ok', 'verified')]
        self.screens = [case for case, _, _ in cases]
        self.live.capture = lambda: self.screens.pop(0)
        self.live.start('epicmouse', 'Worms', thread=False, username='epicmouse9925')
        for _, capture_status, context_status in cases:
            self.live.step()
            status = self.live.status()
            self.assertEqual(status['capture_status'], capture_status)
            if context_status:
                self.assertEqual(status['context']['status'], context_status)
            self.assertTrue(status['captured_at'])
        self.live.stop()
        self.assertFalse(self.live.status()['running'])

    def test_window_problems_are_reported(self):
        self.live = LiveCapture(self.snapshots, self.sessions, self.review,
                                capture=lambda: {'status': 'minimized', 'lines': []})
        self.live.start('epicmouse', None, thread=False)
        self.live.step()
        self.assertIn('minimized', self.live.status()['state'].lower())

    def test_shop_pages_are_ignored_not_flagged(self):
        rods = reply('Rod Shop', 'Your balance: $40,780', 'Steel Rod - $5,000')
        self.run_screens([fish(1, 1, 10)], [fish(1, 1, 10), rods, fish(2, 2, 20)])
        self.assertEqual(list_items(self.review)[0], [])
        self.assertEqual(self.live.status()['counts']['ignored'], 1)


class CountRepairTests(unittest.TestCase):
    def box(self, text, left, top):
        return {'text': text, 'left': left, 'top': top, 'right': left + 120, 'bottom': top + 28}

    def test_bare_name_is_reread_and_gets_its_count(self):
        from src.live_capture import repair_counts
        calls, answers = [], iter([['5', 'Salmon'], ['xp to next level']])

        def reread(left, top, right, bottom):
            calls.append((left, top, right, bottom))
            return next(answers)

        boxes = [self.box('You caught:', 697, 720), self.box('Salmon', 747, 753), self.box('7 Cod', 697, 790),
                 self.box('XP to next level.', 816, 804), self.box('Global boost by A!', 697, 840)]
        repaired = repair_counts(boxes, edge=697, height=28, reread=reread)
        self.assertEqual([box['text'] for box in repaired],
                         ['You caught:', '5 Salmon', '7 Cod', 'XP to next level.', 'Global boost by A!'])
        self.assertEqual(len(calls), 2, 'only short digit-free lines right of the text edge are reread')
        left, top, right, bottom = calls[0]
        self.assertLess(left, 697, 'the strip starts left of the text edge, where the count is')
        self.assertTrue(top < 753 and bottom > 781)
        self.assertEqual(boxes[1]['text'], 'Salmon', 'the input boxes are not modified')

    def test_failed_reread_keeps_the_original(self):
        from src.live_capture import repair_counts
        boxes = [self.box('Salmon', 747, 753)]
        for answer in ([], ['Salmon'], ['5', 'Cod'], ['oops']):
            with self.subTest(answer=answer):
                repaired = repair_counts(boxes, edge=697, height=28, reread=lambda *_: answer)
                self.assertEqual(repaired[0]['text'], 'Salmon')




@unittest.skipUnless(sys.platform == 'win32', 'native capture needs Windows')
class NativeCaptureTests(unittest.TestCase):
    """Native checks only; the synthetic-BMP tests below do not exercise the Windows calls."""

    def test_handle_types_are_declared_pointer_sized(self):
        import ctypes
        from ctypes import wintypes
        from src.window_capture import _apis
        user32, gdi32 = _apis()
        self.assertEqual(user32.GetWindowDC.restype, wintypes.HDC)
        self.assertEqual(gdi32.CreateCompatibleBitmap.restype, wintypes.HBITMAP)
        self.assertEqual(ctypes.sizeof(wintypes.HDC), ctypes.sizeof(ctypes.c_void_p))

    def test_failed_capture_raises_and_writes_nothing(self):
        from src.window_capture import capture_window
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'shot.bmp'
            with self.assertRaises(OSError):
                capture_window(0x7FFFFFF0, target)  # no such window
            self.assertFalse(target.exists())


class LightCaptureTests(unittest.TestCase):
    def test_a_light_read_skips_the_chat_rereads_controls_and_buttons(self):
        # Typing a command checks only the window's text and the message box: the rest cost most of a read.
        from unittest.mock import patch
        from src.live_capture import make_capture
        boxes = [dict(text='epicmouse used', left=300, top=100, right=420, bottom=118),
                 dict(text='% fish', left=300, top=120, right=360, bottom=138),
                 dict(text='Virtual Fisher 11:16 PM', left=300, top=140, right=480, bottom=158)]
        used = []
        with patch('src.window_capture.capture_supported', return_value=True), \
                patch('src.screenshot_ocr.ocr_available', return_value=True), \
                patch('src.window_capture.capture_window', return_value={'minimized': False, 'blank': False,
                                                                         'width': 1000, 'height': 900}), \
                patch('src.screenshot_ocr.read_image_boxes', return_value=boxes), \
                patch('src.screenshot_ocr.read_region_boxes', side_effect=lambda *a: used.append('region') or []), \
                patch('src.live_capture.repair_counts', side_effect=lambda column, *a: used.append('rereads') or column), \
                patch('src.reply_ocr.repair_reply_regions', side_effect=lambda column, *a: used.append('controls') or []), \
                patch('src.ocr_image.button_rows', side_effect=lambda *a: used.append('buttons') or []),                 patch('src.live_capture.chat_column', return_value=(boxes, 300, 18)):
            capture = make_capture(window_provider=lambda: (7, 'Discord'), input_region=True)
            self.assertEqual(capture(light=True)['status'], 'ok')
            self.assertEqual(used, ['region'])                  # the message box region only
            used.clear()
            capture()
            self.assertEqual(sorted(used), ['buttons', 'controls', 'region', 'region', 'rereads'])

    def test_a_deep_read_also_reads_the_picker_zoomed_in(self):
        # Live: "/boosts" went unread in the picker at x2 and was read at x4.
        from unittest.mock import patch
        from src.live_capture import make_capture
        scales = []
        with patch('src.window_capture.capture_supported', return_value=True), \
                patch('src.screenshot_ocr.ocr_available', return_value=True), \
                patch('src.window_capture.capture_window', return_value={'minimized': False, 'blank': False,
                                                                         'width': 1000, 'height': 900}), \
                patch('src.screenshot_ocr.read_image_boxes', return_value=[]), \
                patch('src.screenshot_ocr.read_region_boxes',
                      side_effect=lambda image, region, scale: scales.append(scale) or [{'scale': scale}]):
            capture = make_capture(window_provider=lambda: (7, 'Discord'), input_region=True)
            self.assertNotIn('deep_boxes', capture(light=True))
            self.assertEqual(scales, [2])
            scales.clear()
            frame = capture(light=True, deep=True)
        self.assertEqual(scales, [2, 3, 4])
        self.assertEqual(frame['boxes'], [{'scale': 2}])
        self.assertEqual(frame['deep_boxes'], [[{'scale': 3}], [{'scale': 4}]])

    def test_the_automation_rereads_only_the_newest_message(self):
        # Live: the rereads of every message on screen cost about 1.4 s of a 1.9 s read; the reply is the newest.
        from unittest.mock import patch
        from src.live_capture import make_capture
        old = [dict(text='epicmouse used', left=300, top=100, right=420, bottom=118),
               dict(text='Salmon', left=340, top=120, right=400, bottom=138)]
        new = [dict(text='epicmouse used', left=300, top=140, right=420, bottom=158),
               dict(text='Cod', left=340, top=160, right=380, bottom=178)]
        seen = {}
        with patch('src.window_capture.capture_supported', return_value=True), \
                patch('src.screenshot_ocr.ocr_available', return_value=True), \
                patch('src.window_capture.capture_window', return_value={'minimized': False, 'blank': False,
                                                                         'width': 1000, 'height': 900}), \
                patch('src.screenshot_ocr.read_image_boxes', return_value=old + new), \
                patch('src.screenshot_ocr.read_region_boxes', return_value=[]), \
                patch('src.live_capture.repair_counts',
                      side_effect=lambda column, *a: seen.setdefault('rereads', [b['text'] for b in column]) and
                      [{**b, 'text': b['text'] + '*'} for b in column]), \
                patch('src.reply_ocr.repair_reply_regions',
                      side_effect=lambda column, *a: seen.setdefault('repairs', [b['text'] for b in column])), \
                patch('src.ocr_image.button_rows', return_value=[]), \
                patch('src.live_capture.chat_column', return_value=(old + new, 300, 18)):
            lines = make_capture(window_provider=lambda: (7, 'Discord'), input_region=True,
                                 newest_only=True)()['lines']
        self.assertEqual(seen['rereads'], ['epicmouse used', 'Cod'])
        self.assertEqual(seen['repairs'], ['epicmouse used*', 'Cod*'])
        self.assertEqual(lines, ['epicmouse used', 'Salmon', 'epicmouse used*', 'Cod*'])


class KeptCaptureTests(unittest.TestCase):
    def test_only_the_newest_kept_captures_remain(self):
        from src.live_capture import prune_kept
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            stamps = [f'20261006-1200{second:02d}-000000' for second in range(5)]
            for stamp in stamps:
                (folder / f'{stamp}.png').write_bytes(b'png')
                (folder / f'{stamp}.json').write_text('{}', encoding='utf-8')
            (folder / 'notes.png').write_bytes(b'mine')
            prune_kept(folder, limit=2)
            self.assertEqual(sorted(path.name for path in folder.iterdir()),
                             sorted([f'{stamp}{suffix}' for stamp in stamps[-2:] for suffix in ('.json', '.png')]
                                    + ['notes.png']))


class ChannelEvidenceTests(unittest.TestCase):
    def test_channel_is_read_from_the_search_box_in_the_capture(self):
        from src.live_capture import channel_name
        self.assertEqual(channel_name(WINDOW['lines'], WINDOW['info']['height']), 'Virtual Fisher')
        self.assertEqual(channel_name([{'text': 'Search Fishing Club', 'top': 40}], 1000), 'Fishing Club')
        self.assertIsNone(channel_name([{'text': 'Search Fishing Club', 'top': 600}], 1000),
                          'only the top bar counts, not chat text')
        self.assertIsNone(channel_name([], 1000))

    def test_a_misread_tag_after_the_name_is_still_the_tag(self):
        # The DM search box shows "Search Virtual Fisher#7036"; OCR can misread or space out the tag.
        from src.live_capture import channel_hints, channel_name
        for text in ('Search Virtual Fisher#7036', 'Search Virtual Fisher#7O36', 'Search Virtual Fisher # 7036'):
            self.assertEqual(channel_name([{'text': text, 'top': 40}], 1000), 'Virtual Fisher')
        box = {'text': 'IMessage@Virtual Fisher', 'top': 1340}
        self.assertEqual(channel_hints([box], 1399)['composer'], 'Virtual Fisher')   # the caret read as I


class CapturePngTests(unittest.TestCase):
    def test_bmp_capture_converts_to_png(self):
        import struct
        import zlib
        from src.window_capture import _BitmapInfoHeader, bmp_to_png
        with tempfile.TemporaryDirectory() as folder:
            bmp, png = Path(folder) / 'shot.bmp', Path(folder) / 'shot.png'
            header = _BitmapInfoHeader(40, 2, -1, 1, 32, 0, 0, 0, 0, 0, 0)
            pixels = bytes([10, 20, 30, 0, 40, 50, 60, 0])  # two BGRA pixels
            bmp.write_bytes(struct.pack('<2sIHHI', b'BM', 54 + len(pixels), 0, 0, 54) + bytes(header) + pixels)
            bmp_to_png(bmp, png)
            data = png.read_bytes()
            self.assertEqual(data[:8], bytes([0x89]) + b'PNG' + bytes([13, 10, 26, 10]))
            self.assertEqual(struct.unpack('>II', data[16:24]), (2, 1))
            idat = data[data.index(b'IDAT') + 4:data.index(b'IEND') - 8]
            self.assertEqual(zlib.decompress(idat), bytes([0, 30, 20, 10, 255, 60, 50, 40, 255]))


if __name__ == '__main__':
    unittest.main()


class ChannelTests(unittest.TestCase):
    """Observed 2026-10-10: snapped to half the screen, Discord shortens the search box to "Search Virtual …"."""

    @staticmethod
    def box(text, top, left=600):
        return {'text': text, 'top': top, 'bottom': top + 20, 'left': left, 'right': left + 200}

    def hints(self, *boxes):
        from src.live_capture import channel_hints
        return channel_hints(list(boxes), 1525)

    def test_full_search_name_still_works(self):
        from src.live_capture import channel_matches
        context = {'channel': 'Virtual Fisher', 'channel_hints': self.hints(self.box('Search Virtual Fisher', 70))}
        self.assertTrue(channel_matches(context, 'Virtual Fisher'))

    def test_shortened_search_with_the_header_or_message_box_confirms_it(self):
        from src.live_capture import channel_matches
        for name_box in (self.box('Virtual Fisher /fish', 74, left=610), self.box('Message @Virtual Fisher', 1460)):
            with self.subTest(name_box=name_box['text']):
                hints = self.hints(self.box('Search Virtual ...', 70, left=1060), name_box)
                self.assertTrue(channel_matches({'channel': None, 'channel_hints': hints}, 'Virtual Fisher'))

    def test_another_dm_never_matches(self):
        from src.live_capture import channel_matches
        farmer = self.hints(self.box('Search Virtual ...', 70, left=1060), self.box('Virtual Farmer /play', 74))
        self.assertFalse(channel_matches({'channel': None, 'channel_hints': farmer}, 'Virtual Fisher'))
        other = self.hints(self.box('Search BugParticle', 70), self.box('Message @Virtual Fisher', 1460))
        self.assertFalse(channel_matches({'channel': 'BugParticle', 'channel_hints': other}, 'Virtual Fisher'),
                         'a full, different search name always wins')

    def test_a_shortened_search_alone_is_not_enough(self):
        from src.live_capture import channel_matches
        hints = self.hints(self.box('Search Virtual ...', 70, left=1060))
        self.assertFalse(channel_matches({'channel': None, 'channel_hints': hints}, 'Virtual Fisher'))


class HeaderTests(unittest.TestCase):
    def test_a_reply_whose_time_lost_its_am_pm_is_still_a_reply(self):
        # Live, Oct 13: "Virtual Fisher 1:49" (PM not read) hid three /profile replies in a row; the run stopped.
        from src.live_capture import capture_frame
        lines = ['epicmouse used', 'profile', 'Virtual Fisher 1:49', 'Inventory of epicmouse', 'Balance: $416,556.',
                 'P4 Level 70, 42,925/46,500 XP to next leveL', 'Message @Virtual Fisher']
        [message] = capture_frame(lines)['messages']
        self.assertEqual((message['kind'], message['invoked_by']), ('profile', 'epicmouse'))
        self.assertEqual(capture_frame(['Virtual Fisher [fish', 'Balance: $1.'])['messages'], [])   # the DM title
