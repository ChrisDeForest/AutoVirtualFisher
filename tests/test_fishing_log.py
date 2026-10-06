from pathlib import Path
import unittest

from src.fishing_log import fingerprint, parse_fishing

LIVE = (Path(__file__).parent / 'fixtures' / 'live_fishing.txt').read_text(encoding='utf-8')


class LiveFishingTests(unittest.TestCase):
    def setUp(self):
        self.tally = parse_fishing(LIVE)

    def test_trips_catches_and_fishing_xp(self):
        self.assertEqual(self.tally['trips'], 6)
        self.assertEqual(self.tally['boosted_trips'], 6)
        self.assertEqual(self.tally['catches'], {'Fish': 6, 'Salmon': 48, 'Cod': 47})
        self.assertEqual(self.tally['fishing_xp'], 631, 'quest XP must not count as fishing XP')

    def test_level_up_events(self):
        self.assertEqual(self.tally['level_ups'], [30])
        self.assertEqual(self.tally['level_up_money'], 6000)
        self.assertEqual(self.tally['unlocks'], ['NEW BAIT'])

    def test_quest_completions_and_rewards(self):
        self.assertEqual(self.tally['quests_completed'], [
            {'quest': 'Daily Level-ups', 'tier': 1, 'money': 430, 'xp': 0, 'items': {'uncommon chest': 1}},
            {'quest': 'Daily Fishing', 'tier': 1, 'money': 0, 'xp': 330, 'items': {}},
        ])
        self.assertEqual(self.tally['chest_contents'], {'Gold Fish': 2})

    def test_finds_charms_and_bait(self):
        self.assertEqual(self.tally['finds'], {'artifact box': 1, 'Endurance Charm': 1,
                                               'Quality Charm': 1, 'Experience Charm': 1})
        self.assertEqual(self.tally['charm_levels'], {'Endurance Charm': 1, 'Quality Charm': 1,
                                                      'Experience Charm': 1})
        self.assertEqual(self.tally['bait_ran_out'], ['worms'])

    def test_author_lines_and_cooldowns_are_not_warnings(self):
        self.assertEqual(self.tally['warnings'], [])
        self.assertEqual(self.tally['unparsed_line_count'], 0)

    def test_each_trip_records_catches_xp_and_events(self):
        trips = self.tally['trip_details']
        self.assertEqual(len(trips), 6)
        self.assertEqual(trips[0]['catches'], {'Fish': 2, 'Salmon': 7, 'Cod': 12})
        self.assertEqual([trip['xp'] for trip in trips], [140, 102, 97, 81, 91, 120])
        self.assertTrue(all(trip['boosted'] for trip in trips))
        self.assertEqual(trips[0]['events'], ['Reached level 30', 'Unlocked NEW BAIT',
                                              'Completed Daily Level-ups tier 1', 'Chest: 2 Gold Fish'])
        self.assertEqual(trips[3]['events'], ['Found artifact box', 'Found Endurance Charm', 'Found Quality Charm',
                                              'Found Experience Charm', 'Endurance Charm now level 1',
                                              'Quality Charm now level 1', 'Experience Charm now level 1'])
        self.assertEqual(trips[4]['events'], ['Completed Daily Fishing tier 1'])
        self.assertEqual(trips[5]['events'], ['Ran out of worms'])


class FishingEdgeTests(unittest.TestCase):
    def test_unknown_lines_become_warnings_including_the_last_line(self):
        tally = parse_fishing('You caught:\n3 Cod\nA strange wave appears\n+10 XP\nTrailing mystery')
        self.assertEqual(tally['catches'], {'Cod': 3})
        self.assertEqual(tally['fishing_xp'], 10)
        self.assertEqual(tally['unparsed_line_count'], 2)
        self.assertEqual(tally['warnings'], ['Unrecognized line: A strange wave appears',
                                             'Unrecognized line: Trailing mystery'])

    def test_unreadable_catch_line_does_not_end_the_catch_list(self):
        tally = parse_fishing('epicmouse9925\nYou caught:\nFish\n9 Salmon\n5 Cod\n+93 XP\nGlobal boost by Someone!')
        self.assertEqual(tally['catches'], {'Salmon': 9, 'Cod': 5})
        self.assertEqual(tally['fishing_xp'], 93)
        self.assertEqual(tally['warnings'], ['Unrecognized line: Fish'])

    def test_custom_discord_emoji_and_markdown_are_cleaned(self):
        tally = parse_fishing('**You caught:**\n4 <:salmon:123456> Salmon\n+1,200 XP')
        self.assertEqual(tally['catches'], {'Salmon': 4})
        self.assertEqual(tally['fishing_xp'], 1200)

    def test_chest_xp_is_recorded_separately(self):
        tally = parse_fishing('You caught:\n1 Fish\n8 Salmon\n13 Cod\nYou found an uncommon chest :crate_uncommon:\n'
                              'You got 217 XP from the chest.\n+382 XP')
        self.assertEqual(tally['chest_contents'], {})
        self.assertEqual(tally['chest_xp'], 217)
        self.assertEqual(tally['fishing_xp'], 382, 'the trip line already includes chest XP')
        self.assertEqual(tally['trip_details'][0]['chest_xp'], 217)
        self.assertEqual(tally['trip_details'][0]['events'], ['Found uncommon chest', 'Chest: 217 XP'])

    def test_empty_input(self):
        tally = parse_fishing('  \n')
        self.assertEqual(tally['trips'], 0)
        self.assertEqual(tally['warnings'], ['Paste fishing results to begin.'])

    def test_fingerprint_ignores_spacing_but_not_content(self):
        base = fingerprint('You caught:\n3 Cod\n+10 XP')
        self.assertEqual(base, fingerprint('\nYou caught:  \n\n3 Cod\r\n+10 XP\n'))
        self.assertNotEqual(base, fingerprint('You caught:\n4 Cod\n+10 XP'))
        self.assertRegex(base, r'^[0-9a-f]{64}$')


if __name__ == '__main__':
    unittest.main()
