from pathlib import Path
import unittest

from src.fishing_log import fingerprint, parse_fishing

LIVE = (Path(__file__).parent / 'fixtures' / 'live_fishing.txt').read_text(encoding='utf-8')


class LiveFishingTests(unittest.TestCase):
    def test_crate_contents_after_you_gained(self):
        # Observed live: a super crate lists its contents under "You gained:".
        tally = parse_fishing('You caught:\n3 :salmon: Salmon\n14 :cod: Cod\nYou found a super crate :crate_super:\n'
                              'You gained:\n+13 Gold Fish :gold_fish:\n+13 Emerald Fish :emerald_fish:\n'
                              '+13 Lava Fish :lava_fish:\n+13 Diamond Fish :diamond_fish:\n+162 XP\n'
                              'Global boost by Colby  •√π× 😊!')
        self.assertEqual(tally['warnings'], [])
        self.assertEqual(tally['finds'], {'super crate': 1})
        self.assertEqual(tally['chest_contents'], {'Gold Fish': 13, 'Emerald Fish': 13, 'Lava Fish': 13,
                                                   'Diamond Fish': 13})
        self.assertEqual((tally['fishing_xp'], tally['catches']), (162, {'Salmon': 3, 'Cod': 14}))
        self.assertIn('Crate: 13 Lava Fish', tally['trip_details'][0]['events'])

    def test_crate_contents_as_ocr_read_them(self):
        # Saved live: the colon read as "-", and "+17G01d Fish" with the space lost and o/l read as 0/1.
        tally = parse_fishing('You caught:\n2 Salmon\n14 Cod\nYou found a super crate\nYou gained-\n+17G01d Fish\n'
                              '+17 Emerald Fish\n+17 Lava Fish\n+17 Diamond Fish\n+201 xp\nGlobal boost by Colby')
        self.assertEqual(tally['warnings'], [])
        self.assertEqual(tally['chest_contents'], {'Gold Fish': 17, 'Emerald Fish': 17, 'Lava Fish': 17,
                                                   'Diamond Fish': 17})

    def test_a_level_line_without_its_charm_name_belongs_to_the_charm_found(self):
        tally = parse_fishing('You caught:\n2 Cod\nYou found a Quantity Charm Q !\nYour Charm is now level 1!\n+20 xp')
        self.assertEqual(tally['charm_levels'], {'Quantity Charm': 1})

    def test_a_pet_found_on_a_trip(self):
        # Live 2026-10-14 (Discord copy text): the pet find comes before "You caught:", under "RARE FIND".
        text = ('epicmouse9925\nRARE FIND\nYou found a pet :pufferfish: Puffer!\n'
                'Since you already have this pet your pet Puffer gained 9,207 pet XP.\n'
                'epicmouse9925\nYou caught:\n35 :pufferfish: Pufferfish\n+1,542 XP\nGlobal boost by Magnus!')
        tally = parse_fishing(text)
        self.assertEqual((tally['trips'], tally['warnings'], tally['finds']), (1, [], {}))
        self.assertEqual((tally['pets_found'], tally['pet_xp']), ({'Puffer': 1}, {'Puffer': 9207}))
        self.assertEqual(tally['trip_details'][0]['events'], ['Found pet Puffer', 'Pet Puffer +9,207 pet XP'])
        self.assertEqual(tally['catches'], {'Pufferfish': 35})

    def test_misread_chests_and_charms_are_named(self):
        # Seen in live runs, 2026-10-14.
        from src.fishing_log import known_name
        for read, name in (('legendary crate e', 'legendary crate'), ('epic crate $', 'epic crate'),
                           ('Treaswe Charm', 'Treasure Charm'), ('Q�antity Charm', 'Quantity Charm'),
                           ('Qaantity Charm', 'Quantity Charm'), ('Q�ality Charm', 'Quality Charm'),
                           ('Charm', 'Charm'), ('uncommon chest', 'uncommon chest')):
            self.assertEqual(known_name(read), name, read)
        self.assertEqual(parse_fishing('You caught:\n1 Cod\nYou found 2x Worker Charm!\n+5 XP')['finds'],
                         {'Worker Charm': 2})

    def test_charm_emoji_read_as_a_letter_is_not_part_of_its_name(self):
        tally = parse_fishing('You caught:\n2 Cod\nYou found an epic crate\nYou found a Quantity Charm Q !\n+233 xp')
        self.assertEqual(tally['finds'], {'epic crate': 1, 'Quantity Charm': 1})


        tally = parse_fishing('You caught:\n1 Cod\nYou found a rare chest\nYou got 1 Ernerald Fish from the chest!\n'
                              '+5 XP')
        self.assertEqual(tally['chest_contents'], {'Emerald Fish': 1})

    def test_saved_money_chest_reply(self):
        tally = parse_fishing('You caught:\n1 Fish\n5 Salmon\n9 Cod\nYou found an uncornmon chest\nYou got $296 from the chest\n+119 xp\nGlobal boost by')
        self.assertEqual(tally['warnings'], [])
        self.assertEqual(tally['chest_money'], 296)
        self.assertEqual(tally['finds'], {'uncommon chest': 1})
        self.assertEqual(tally['fishing_xp'], 119)

    def test_wrapped_level_up_and_ocr_one_in_quest_reward(self):
        tally = parse_fishing('You caught:\n2 Fish\n4 Salmon\n9 Cod\n+117 xp\nLEVEL UP! You are\nnow level 31.\n'
                              '$6,200 received for leveling up.\nQUEST COMPLETE!\nDaily Level-ups Tier 1\n'
                              'Reward:\nlx uncommon chest\nYou got 150 XP from the chest')
        self.assertEqual(tally['warnings'], [])
        self.assertEqual(tally['level_ups'], [31])
        self.assertEqual(tally['quests_completed'][0]['items'], {'uncommon chest': 1})

    def test_reward_chest_contents_belong_to_the_quest(self):
        # Observed 2026-10-09: a quest's reward chest is opened after the trip's "+N XP" line, so its XP is not
        # part of that line. A chest fished up is reported before it and is included.
        tally = parse_fishing('\n'.join([
            'You caught:', '3 Cod', '+193 XP', 'QUEST COMPLETE!', 'Daily Level-ups Tier 1', 'Reward:',
            '+$960', '1x uncommon chest', 'You got 260 XP from the chest.', 'You got $40 from the chest']))
        self.assertEqual(tally['warnings'], [])
        quest = tally['quests_completed'][0]
        self.assertEqual((quest['money'], quest['xp'], quest['items']), (960, 260, {'uncommon chest': 1}))
        self.assertEqual((tally['chest_xp'], tally['chest_money'], tally['fishing_xp']), (0, 40, 193))

    def test_level_up_without_its_level_stays_unreadable(self):
        # Observed: OCR dropped "now level 31" entirely. A guessed level must not be recorded.
        tally = parse_fishing('You caught:\n2 Fish\n+117 xp\nLEVEL UP! You are\n$6,200 received for leveling up.')
        self.assertEqual(tally['warnings'], ['Unrecognized line: LEVEL UP! You are'])
        self.assertEqual(tally['level_ups'], [])

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
class BoostCaptionTests(unittest.TestCase):
    def test_missing_booster_name_keeps_exact_catch(self):
        from src.fishing_log import parse_fishing
        from src.live_capture import fishing_problems
        text = 'You caught:\n7 Salmon\n7 Cod\n+104 XP\nGlobal boost by'
        self.assertEqual(fishing_problems(text), [])
        tally = parse_fishing(text)
        self.assertEqual(tally['catches'], {'Salmon': 7, 'Cod': 7})
        self.assertEqual(tally['fishing_xp'], 104)
        self.assertEqual(tally['boosted_trips'], 1)
