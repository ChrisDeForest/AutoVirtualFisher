import unittest

from src.fishing_log import parse_fishing
from src.live_stats import carry_forward, level_after, project, synced_stats

PROFILE = ('Inventory of epicmouse\nBalance: $54,950.\nP4 Level 31, 1,677/6,000 XP to next level.\n'
           'Currently using Steel Rod.\nCurrent biome: River\nPet: Dolphin (Level 51)\nFish Inventory\n306 Fish\n'
           '3,943 Salmon\n4,972 Cod\nFish Value: $136,217\nExotic Fish\n4 Gold Fish\n1 Emerald Fish\nSpecial\n11 Hooks')
BUFFS = ("epicmouse9925's current multipliers (including bait):\nSell price: 2.20x\nFish catch: 1.41x\n"
         'Fish quality: 1.21x\nTreasure chance: 1.50x\nTreasure quality: 1.13x\nXP Multiplier: 2.12x\n'
         'Fishing cooldown: 3.5s.')


def trip(text):
    return parse_fishing('You caught:\n' + text)


class LevelTests(unittest.TestCase):
    def test_xp_carries_over_level_ups(self):
        # The real wiki table: 6,000 XP from level 31, 6,500 from 32 (matching the live profile).
        from src.player_planner import load_level_increments
        increments = load_level_increments()
        self.assertEqual(level_after(31, 1677, 4400, increments), (32, 77, 6500))
        self.assertEqual(level_after(31, 1677, 100, increments), (31, 1777, 6000))


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        from src.player_planner import load_level_increments
        from src.catch_check import load_fish_reference
        self.increments, self.fish = load_level_increments(), load_fish_reference()
        self.synced = synced_stats(PROFILE, BUFFS)

    def test_sync_reads_profile_inventory_and_buffs(self):
        self.assertEqual(self.synced['profile']['balance'], 54950)
        self.assertEqual(self.synced['inventory']['fish']['Salmon'], 3943)
        self.assertEqual(self.synced['buffs']['fishing_cooldown']['value'], 3.5)

    def test_trips_move_xp_inventory_value_and_balance(self):
        tallies = [trip('5 Salmon\n10 Cod\n+131 XP'),
                   trip('1 Fish\nYou found a common chest\nYou got 2 Gold Fish from the chest!\n'
                        'You got $296 from the chest\n+87 XP')]
        stats = project(self.synced, tallies, self.increments, self.fish)
        profile = stats['profile']
        self.assertEqual((profile['level'], profile['xp_current'], profile['xp_required']), (31, 1677 + 218, 6000))
        self.assertEqual(profile['balance'], 54950 + 296)
        self.assertEqual(stats['inventory']['fish']['Salmon'], 3948)
        self.assertEqual(stats['inventory']['fish']['Cod'], 4982)
        self.assertEqual(stats['inventory']['exotic_fish']['Gold Fish'], 6)
        # Each catch adds its wiki sell price times the sell multiplier: (5*3 + 10*10 + 1*1) * 2.2.
        self.assertEqual(profile['fish_value'], 136217 + round(116 * 2.2))
        self.assertEqual(stats['changes']['fish']['Salmon'], 5)

    def test_level_up_line_and_its_money(self):
        tallies = [trip('3 Cod\n+4400 XP\nLEVEL UP! You are now level 32.\n$6,200 received for leveling up.')]
        profile = project(self.synced, tallies, self.increments, self.fish)['profile']
        self.assertEqual((profile['level'], profile['xp_current']), (32, 77))
        self.assertEqual(profile['balance'], 54950 + 6200)

    def test_quest_reward_xp_and_money_count(self):
        tallies = [trip('1 Cod\n+10 XP\nQUEST COMPLETE!\nDaily Fishing Tier 1\nReward:\n+$350\n+370 XP')]
        profile = project(self.synced, tallies, self.increments, self.fish)['profile']
        self.assertEqual(profile['xp_current'], 1677 + 10 + 370)
        self.assertEqual(profile['balance'], 54950 + 350)

    def test_unread_fields_stay_unknown(self):
        synced = synced_stats(PROFILE.replace('Balance: $54,950.', 'Balance:'), '')
        stats = project(synced, [trip('1 Cod\n+5 XP')], self.increments, self.fish)
        self.assertIsNone(stats['profile']['balance'])
        self.assertEqual(stats['profile']['xp_current'], 1682)
        self.assertEqual(stats['buffs'], {})

    def test_level_shows_when_ocr_drops_the_xp_numbers(self):
        # Observed live: "P4 Level 31, xp to next level." The level and prestige are still plain to read.
        synced = synced_stats(PROFILE.replace('1,677/6,000 XP', 'xp'), '')
        profile = synced['profile']
        self.assertEqual((profile['level'], profile['prestige'], profile['xp_current']), (31, 4, None))

    def test_pet_level_is_read_and_a_lost_name_is_not_the_pet(self):
        self.assertEqual((self.synced['profile']['pet'], self.synced['profile']['pet_level']), ('Dolphin', 51))
        # Observed live: OCR dropped the emoji and name, leaving "Pet: (Level 51)".
        lost = synced_stats(PROFILE.replace('Pet: Dolphin (Level 51)', 'Pet: (Level 51)'), '')['profile']
        self.assertEqual((lost['pet'], lost['pet_level']), (None, 51))

    def test_a_resync_keeps_what_it_could_not_read_from_the_earlier_projection(self):
        earlier = project(self.synced, [trip('1 Cod\n+23 XP')], self.increments, self.fish)['profile']
        garbled = PROFILE.replace('1,677/6,000 XP', 'xp').replace('Pet: Dolphin (Level 51)', 'Pet: (Level 51)')
        profile = carry_forward(synced_stats(garbled, '')['profile'], earlier)
        self.assertEqual((profile['level'], profile['xp_current'], profile['xp_required']), (31, 1700, 6000))
        self.assertEqual((profile['pet'], profile['pet_level']), ('Dolphin', 51))
        # A different level means the earlier XP no longer applies.
        moved = carry_forward(synced_stats(garbled.replace('Level 31', 'Level 32'), '')['profile'], earlier)
        self.assertEqual((moved['level'], moved['xp_current']), (32, None))


CHARMS = ('Charms are found in high tier crates. Try to get them all!\n'
          '0/14 - :charm_yellow: Marketing Charm - Sell fish for more. (1 to next tier).\n'
          '1/14 - :charm_pink: Endurance Charm - Catch more fish. (2 to next tier).\n'
          '1/14 - :charm_red: Haste Charm - Fish more often. (2 to next tier).\n'
          '1/14 - :charm_white: Quantity Charm - Increase amount of items found in chests. (1 to next tier).\n'
          '1/14 - :charm_cyan: Worker Charm - Workers catch more fish. (2 to next tier).\n'
          '0/14 - :charm_lblue: Treasure Charm - Catch slightly more treasure. (1 to next tier).\n'
          '2/14 - :charm_green: Quality Charm - Increase quality of treasure. (2 to next tier).\n'
          '1/14 - :charm_blue: Experience Charm - Increase experience from all fish. (1 to next tier).\n'
          '\n11/840 - Charms found.')


class CharmReplyTests(unittest.TestCase):
    def test_counts_come_from_level_and_charms_to_next_tier(self):
        # Quality: level 2 (1 + 2 = 3 charms) with 2 of the 3 for level 3 still to find, so 4 found.
        from src.live_stats import parse_charms
        parsed = parse_charms(CHARMS)
        self.assertEqual({name: charm['counted'] for name, charm in parsed['charms'].items()},
                         {'Marketing Charm': 0, 'Endurance Charm': 1, 'Haste Charm': 1, 'Quantity Charm': 2,
                          'Worker Charm': 1, 'Treasure Charm': 0, 'Quality Charm': 4, 'Experience Charm': 2})
        self.assertEqual(parsed['charms']['Quality Charm']['level'], 2)
        self.assertEqual((parsed['found'], parsed['goal']), (11, 840))

    def test_charm_lines_wrapped_by_a_narrow_window(self):
        # Live OCR of a narrower window: the count apart from its name, the tier note wrapped to the next line.
        from src.live_stats import parse_charms
        text = chr(10).join((
            'Charms are found in high tier crates. Try to get them all!', '0/14-',
            'Marketing Charm - Sell fish for more. (1 to next tier).', "1/14-'2",
            'Endurance Charm - Catch more fish. (2 to next tier).', '1/14',
            'Haste Charm - Fish more often. (2 to next tier).',
            '1/14- Quantity Charm - Increase amount of items found in', 'chests. (1 to next tier).', '1/14-',
            'Worker Charm - Workers catch more fish. (2 to next tier).', '0/14-',
            'Treasure Charm - Catch slightly more treasure. (1 to next', 'tier).',
            '2/14 - Quality Charm - Increase quality of treasure. (2 to next', 'tier).',
            '1/14- Experience Charm - Increase experience from all fish. (1 to', 'next tier).',
            '11/840 - Charms found.'))
        parsed = parse_charms(text)
        self.assertEqual({name: charm['counted'] for name, charm in parsed['charms'].items()},
                         {'Marketing Charm': 0, 'Endurance Charm': 1, 'Haste Charm': 1, 'Quantity Charm': 2,
                          'Worker Charm': 1, 'Treasure Charm': 0, 'Quality Charm': 4, 'Experience Charm': 2})
        self.assertEqual((parsed['found'], parsed['goal'], parsed['max_level']), (11, 840, 14))

    def test_unread_charm_lines_are_named(self):
        from src.live_stats import unread_fields
        self.assertEqual(unread_fields('charms', CHARMS), [])
        lost = CHARMS.replace('(2 to next tier).\n1/14 - :charm_red:', '(2 to n~xt t1er\n1/14 - :charm_red:')
        self.assertEqual(unread_fields('charms', lost), ['Endurance Charm'])
        self.assertEqual(unread_fields('charms', CHARMS.replace('11/840', '12/840')), ['Charms found'],
                         'the total must agree with the lines')

    def test_charms_found_since_the_reply_are_added(self):
        from src.live_stats import charm_summary, parse_charms
        summary = charm_summary({'charms': {'Quality Charm': 9}}, parse_charms(CHARMS), {'Quality Charm': 1})
        quality = summary['charms']['Quality Charm']
        self.assertEqual((quality['counted'], quality['level'], quality['into'], quality['needed']), (5, 2, 2, 3))
        self.assertEqual((summary['total'], summary['types']), (12, 6))


class UnreadFieldTests(unittest.TestCase):
    def test_complete_replies_have_nothing_unread(self):
        from src.live_stats import unread_fields
        self.assertEqual(unread_fields('profile', PROFILE), [])
        self.assertEqual(unread_fields('buffs', BUFFS), [])

    def test_each_missing_field_is_named(self):
        from src.live_stats import unread_fields
        self.assertEqual(unread_fields('buffs', BUFFS.replace('Sell price: 2.20x\n', '')), ['Sell price'])
        garbled = PROFILE.replace('1,677/6,000 XP', 'xp').replace('Pet: Dolphin (Level 51)', 'Pet: (Level 51)')
        self.assertEqual(unread_fields('profile', garbled), ['XP progress', 'Pet'])


class CharmTests(unittest.TestCase):
    def test_max_level_and_goal_follow_prestige(self):
        # Buff calculator vB1.13.4: max charm level is 10 + prestige below P100, then 2 x prestige - 88.
        # Matches the P0 guide (440 charms) and this account at P4 (14 levels, 840).
        from src.live_stats import charm_goal, charm_max_level, charms_for_level
        self.assertEqual(charms_for_level(14), 105)
        self.assertEqual([charm_max_level(p) for p in (0, 4, 5, 99, 100, 110)], [10, 14, 15, 109, 112, 132])
        self.assertEqual([charm_goal(p) for p in (0, 4, 5)], [440, 840, 960])
        self.assertIsNone(charm_max_level(None))

    def test_level_and_progress_from_charms_found(self):
        from src.live_stats import charm_progress
        # The first charm reaches level 1; level 2 needs 2 more, level 3 needs 3 more.
        self.assertEqual(charm_progress(0, max_level=14), {'found': 0, 'counted': 0, 'level': 0, 'into': 0,
                                                           'needed': 1, 'at_least': False})
        self.assertEqual(charm_progress(1, max_level=14)['level'], 1)
        self.assertEqual(charm_progress(3, max_level=14), {'found': 3, 'counted': 3, 'level': 2, 'into': 0,
                                                           'needed': 3, 'at_least': False})
        self.assertEqual(charm_progress(5, max_level=14)['into'], 2)
        maxed = charm_progress(200, max_level=14)
        self.assertEqual((maxed['level'], maxed['counted'], maxed['needed']), (14, 105, None))
        uncapped = charm_progress(200)
        self.assertEqual((uncapped['counted'], uncapped['level']), (200, 19), 'no cap known: none applied')

    def test_a_reported_level_above_the_count_raises_it(self):
        # Charms found before tracking began: the game's "now level 4" means at least 10 were found.
        from src.live_stats import charm_progress
        progress = charm_progress(2, reported_level=4)
        self.assertEqual((progress['level'], progress['counted'], progress['into'], progress['at_least']),
                         (4, 10, 0, True))
        self.assertFalse(charm_progress(3, reported_level=2)['at_least'], 'the count agrees')

    def test_summary_totals_toward_840_and_names_a_nameless_level_line(self):
        from src.live_stats import charm_summary, summarize_finds
        finds = summarize_finds([{'finds': {'Quality Charm': 1}, 'charm_levels': {'Quality Charm': 1}},
                                 {'finds': {'Quality Charm': 1}},
                                 # Saved live: OCR lost the name in "Your Charm is now level 2!".
                                 {'finds': {'Quality Charm': 1}, 'charm_levels': {'Charm': 2}},
                                 {'finds': {'Haste Charm': 1}, 'charm_levels': {'Haste Charm': 1}}])
        self.assertEqual(finds['charm_levels'], {'Quality Charm': 2, 'Haste Charm': 1})
        summary = charm_summary(finds, prestige=4)
        self.assertEqual((summary['total'], summary['goal'], summary['max_level'], summary['types']),
                         (4, 840, 14, 2))
        self.assertEqual(summary['charms']['Quality Charm']['level'], 2)
        unknown = charm_summary(finds)
        self.assertEqual((unknown['goal'], unknown['max_level']), (None, None), 'never assumed')

    def test_the_charms_reply_states_the_cap_and_goal(self):
        # Its "x/14" lines and "11/840" total win over the prestige formula.
        from src.live_stats import charm_summary, parse_charms
        parsed = parse_charms(CHARMS.replace('/14 -', '/15 -').replace('11/840', '11/960'))
        self.assertEqual((parsed['max_level'], parsed['goal']), (15, 960))
        summary = charm_summary({}, parsed, prestige=4)
        self.assertEqual((summary['max_level'], summary['goal']), (15, 960))


class FindNameTests(unittest.TestCase):
    def test_finds_merge_ocr_spellings_of_one_fish(self):
        # Saved live: one trip read "Ernerald Fish" (rn for m); it is the same Emerald Fish.
        from src.live_stats import summarize_finds
        tallies = [{'chest_contents': {'Emerald Fish': 1}}, {'chest_contents': {'Ernerald Fish': 1}},
                   {'chest_contents': {'emerald fish': 2}}]
        self.assertEqual(summarize_finds(tallies)['special_fish'], {'Emerald Fish': 4})
        saved = summarize_finds([{'finds': {'Quantity Charm Q': 1}}, {'finds': {'Quantity Charm': 1}}])
        self.assertEqual(saved['charms'], {'Quantity Charm': 2})


class HiddenExoticTests(unittest.TestCase):
    def test_exotics_the_profile_does_not_list_come_from_finds(self):
        # Below level 50 the profile does not list Lava or Diamond Fish, though super crates give them.
        from src.live_stats import add_unlisted_finds
        inventory = {'fish': {'Cod': 5}, 'exotic_fish': {'Gold Fish': 12}}
        shown = add_unlisted_finds(inventory, {'Gold Fish': 13, 'Lava Fish': 26, 'Diamond Fish': 13})
        self.assertEqual(shown['exotic_fish'], {'Gold Fish': 12, 'Lava Fish': 26, 'Diamond Fish': 13})
        self.assertEqual(add_unlisted_finds(inventory, {'Gold Fish': 13})['exotic_fish'], {'Gold Fish': 12},
                         'what the profile lists is what you have')


class FindsTests(unittest.TestCase):
    def test_chests_special_fish_and_charms_are_counted(self):
        from src.live_stats import summarize_finds
        tallies = [trip('1 Fish\nYou found a common chest\nYou got 2 Gold Fish from the chest!\n+87 XP'),
                   trip('2 Cod\nYou found an uncornmon chest\nYou got 1 Emerald Fish from the chest!\n+20 XP'),
                   trip('3 Cod\nYou found an epic crate\nYou found an Endurance Charm :charm_pink:!\n'
                        'Your Endurance Charm is now level 2!\n+30 XP')]
        finds = summarize_finds(tallies)
        self.assertEqual(finds['chests'], {'common chest': 1, 'uncommon chest': 1, 'epic crate': 1})
        self.assertEqual(finds['special_fish'], {'Gold Fish': 2, 'Emerald Fish': 1})
        self.assertEqual(finds['charms'], {'Endurance Charm': 1})
        self.assertEqual(finds['charm_levels'], {'Endurance Charm': 2})
        self.assertEqual(summarize_finds(tallies[:1], finds)['chests']['common chest'], 2, 'adds onto earlier finds')


class SourceTests(unittest.TestCase):
    def test_quest_and_chest_rewards_are_split_out(self):
        from src.player_planner import load_level_increments
        from src.catch_check import load_fish_reference
        tallies = [trip('1 Cod\nYou found a rare chest\nYou got $296 from the chest\nYou got 150 XP from the chest\n'
                        'You got 2 Gold Fish from the chest!\n+160 XP'),
                   trip('1 Cod\n+10 XP\nQUEST COMPLETE!\nDaily Fishing Tier 1\nReward:\n+$350\n+370 XP\n1x uncommon chest'),
                   trip('1 Cod\n+5 XP\nLEVEL UP! You are now level 32.\n$6,200 received for leveling up.')]
        sources = project(synced_stats(PROFILE, BUFFS), tallies, load_level_increments(),
                          load_fish_reference())['gained']['sources']
        self.assertEqual(sources['chests'], {'money': 296, 'xp': 150, 'other': {'Gold Fish': 2}})
        self.assertEqual(sources['quests'], {'money': 350, 'xp': 370, 'other': {'uncommon chest': 1}})
        self.assertEqual(sources['level_ups'], {'money': 6200})


class SourceTests(unittest.TestCase):
    def test_quest_and_chest_rewards_are_split_out(self):
        from src.player_planner import load_level_increments
        from src.catch_check import load_fish_reference
        tallies = [trip('1 Cod\nYou found a rare chest\nYou got $296 from the chest\nYou got 150 XP from the chest\n'
                        'You got 2 Gold Fish from the chest!\n+160 XP'),
                   trip('1 Cod\n+10 XP\nQUEST COMPLETE!\nDaily Fishing Tier 1\nReward:\n+$350\n+370 XP\n1x uncommon chest'),
                   trip('1 Cod\n+5 XP\nLEVEL UP! You are now level 32.\n$6,200 received for leveling up.')]
        sources = project(synced_stats(PROFILE, BUFFS), tallies, load_level_increments(),
                          load_fish_reference())['gained']['sources']
        self.assertEqual(sources['chests'], {'money': 296, 'xp': 150, 'other': {'Gold Fish': 2}})
        self.assertEqual(sources['quests'], {'money': 350, 'xp': 370, 'other': {'uncommon chest': 1}})
        self.assertEqual(sources['level_ups'], {'money': 6200})
