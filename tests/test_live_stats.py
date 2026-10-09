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
