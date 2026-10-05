import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.player_state import display_value, parse_responses


class PlayerStateTests(unittest.TestCase):
    def test_profile_heading_records_account_name(self):
        result = parse_responses(profile='Inventory of BugParticle')
        self.assertEqual(result['profile']['account_name'], 'BugParticle')

    def test_profile_heading_strips_discord_badge(self):
        result = parse_responses(profile='Inventory of epicmouse :badge_bronze:')
        self.assertEqual(result['profile']['account_name'], 'epicmouse')

    def test_unknown_values_inside_a_mapping_are_displayed_as_unknown(self):
        self.assertEqual(display_value({'International Ties': None}), 'International Ties: Unknown')
        self.assertEqual(display_value({'International Ties': True, 'Virtual Fisher': False}),
                         'International Ties: Maxed, Virtual Fisher: Not bought')

    def test_prestige_shop_derives_earned_azure_fish_from_balance_and_upgrades(self):
        fixture = Path(__file__).parent / 'fixtures/live_prestige_shop_p4.txt'
        result = parse_responses(prestige_shop=fixture.read_text(encoding='utf-8'))
        prestige = result['prestige']
        self.assertEqual(prestige['azure_fish'], 0)
        self.assertEqual(prestige['upgrades'], {
            'International Ties': True,
            'Business Education': True,
            'Fish Whisperer': True,
            'Ancient One': True,
            'Virtual Fisher': False,
        })
        self.assertEqual(prestige['upgrades_bought'], 4)
        self.assertEqual(prestige['azure_fish_earned_at_least'], 4)
        self.assertEqual(prestige['inferred_prestige_minimum'], 4)
        self.assertEqual(prestige['source'], 'inferred minimum')
        self.assertEqual(result['warnings'], [])

    def test_direct_profile_prestige_wins_over_shop_inference(self):
        result = parse_responses(
            profile='P4 Level 29, 5,363/5,500 XP to next level.',
            prestige_shop='You have: 0 Azure Fish.\nInternational Ties - MAXED')
        self.assertEqual(result['profile']['prestige'], 4)
        self.assertEqual(result['prestige']['source'], 'profile')
        self.assertEqual(result['prestige']['azure_fish_earned_at_least'], 1)

    def test_discord_colon_emoji_copies_are_removed_before_parsing(self):
        result = parse_responses(
            profile='''Inventory of epicmouse :badge_bronze:
Balance: $40,820.
P4 Level 29, 5,363/5,500 XP to next level.
Currently using :steel_rod: Steel Rod.
Current biome: :biome_river: River
Pet: :dolphin: Dolphin (Level 51)

Fish Inventory
283 :fish: Fish''',
            prestige_shop='''Prestige Shop
Spend your Azure Fish :azure_fish: here. (/prestige buy)
You have: 0 :azure_fish:.
International Ties - 10% boost to every multiplier. - MAXED
Business Education - 40% sell boost. - MAXED
Fish Whisperer - 25% more fish caught. - MAXED
Ancient One - 35% more experience from all sources. - MAXED
UNLOCKED AT PRESTIGE 5: Virtual Fisher - 40% more high quality fish.''')
        self.assertEqual(result['profile']['rod'], 'Steel Rod')
        self.assertEqual(result['profile']['biome'], 'River')
        self.assertEqual(result['profile']['pet'], 'Dolphin (Level 51)')
        self.assertEqual(result['inventory']['fish'], {'Fish': 283})
        self.assertEqual(result['prestige']['azure_fish'], 0)
        self.assertEqual(result['prestige']['inferred_prestige_minimum'], 4)
        self.assertNotIn('prestige shop: Azure Fish balance was not found.', result['warnings'])

    def test_pet_response_records_no_pet_state_and_average_xp(self):
        result = parse_responses(pet='''Pets are permanent, they are not lost when you prestige. They increase in effectiveness as you level them up.

+14 pet XP per fishing trip (average)

You have no pets! Find pets by fishing.

Pets are slightly more common if you have a high prestige level.''')
        self.assertEqual(result['pets']['xp_per_fishing_trip_average'], 14)
        self.assertEqual(result['pets']['status'], 'No pets')
        self.assertEqual(result['warnings'], [])

    def test_pet_response_reads_owned_pet_levels_descriptions_and_xp(self):
        result = parse_responses(pet='''+14 pet XP per fishing trip (average)
Current pet: :dolphin: Dolphin.
:pufferfish: Puffer(Lvl 30)
Increases your fish catch.
1,493/2,600 XP to next level.
:dolphin: Dolphin(Lvl 51)
Gives you bonus XP and leads you to more treasure.
13,986/18,000 XP to next level.''')
        pets = result['pets']
        self.assertEqual(pets['status'], 'Pet collection imported')
        self.assertEqual(pets['current_pet'], 'Dolphin')
        self.assertEqual(pets['owned']['Puffer'], {
            'level': 30, 'description': 'Increases your fish catch',
            'xp_current': 1493, 'xp_required': 2600, 'xp_to_next_level': 1107,
        })
        self.assertEqual(pets['owned']['Dolphin']['level'], 51)
        self.assertEqual(pets['owned']['Dolphin']['xp_to_next_level'], 4014)
        self.assertEqual(pets['current_pet_bonuses'], {
            'Treasure chance': {'value': 36.19, 'unit': 'percent'},
            'XP': {'value': 36.19, 'unit': 'percent'},
        })
        self.assertEqual(result['warnings'], [])

    def test_partial_shop_reports_a_conservative_prestige_minimum(self):
        result = parse_responses(prestige_shop='Prestige Shop\nYou have: 2 Azure Fish.')
        self.assertEqual(result['prestige']['azure_fish'], 2)
        self.assertEqual(result['prestige']['azure_fish_earned_at_least'], 2)
        self.assertEqual(result['prestige']['inferred_prestige_minimum'], 2)
        self.assertIsNone(result['prestige']['upgrades_bought'])
        self.assertTrue(result['warnings'])

    def test_live_screenshot_transcriptions(self):
        fixtures = Path(__file__).parent / 'fixtures'
        result = parse_responses((fixtures / 'live_profile.txt').read_text(encoding='utf-8'),
                                 (fixtures / 'live_buffs.txt').read_text(encoding='utf-8'))
        profile = result['profile']
        self.assertEqual(profile['balance'], 2343033)
        self.assertEqual(profile['level'], 131)
        self.assertEqual(profile['xp_current'], 270862)
        self.assertEqual(profile['xp_required'], 477500)
        self.assertEqual(profile['xp_to_next_level'], 206638)
        self.assertEqual(profile['rod'], 'Golden Rod')
        self.assertEqual(profile['biome'], 'Ocean')
        self.assertEqual(profile['bait'], 'Magic Bait')
        self.assertEqual(profile['bait_quantity'], 9509)
        self.assertEqual(profile['fish_value'], 137270)
        self.assertIsNone(profile['pet'])
        self.assertEqual(result['inventory']['fish'], {'Tropical Fish': 4, 'Pufferfish': 12, 'Squid': 2, 'Turtle': 6})
        self.assertEqual(result['inventory']['exotic_fish']['Gold Fish'], 603)
        self.assertEqual(result['inventory']['special'], {'Hooks': 10})
        self.assertEqual(result['buffs']['sell_price'], {'value': 4.83, 'unit': 'multiplier'})
        self.assertEqual(result['buffs']['fishing_cooldown'], {'value': 2.8, 'unit': 'seconds'})
        self.assertEqual(result['warnings'], [])

    def test_profile_numbers_equipment_and_explicit_no_pet(self):
        result = parse_responses(profile='''**Balance:** $2,343,033
L149, 1,250 XP to next level
Rod: <:rod:12345> Golden Rod
Current biome: Ocean
Pet: None
Bait: Magic Bait''')
        self.assertEqual(result['profile']['balance'], 2343033)
        self.assertEqual(result['profile']['level'], 149)
        self.assertEqual(result['profile']['xp_to_next_level'], 1250)
        self.assertEqual(result['profile']['rod'], 'Golden Rod')
        self.assertEqual(result['profile']['pet'], 'None')
        self.assertEqual(result['warnings'], [])

    def test_buffs_keep_multiplier_percentage_and_seconds_distinct(self):
        result = parse_responses(buffs='''Fish catch: +150%
Fish quality: 2.5x
Treasure chance: +40%
Treasure quality: +50%
XP multiplier: x3
Fishing cooldown: 3.5 seconds''')
        self.assertEqual(result['buffs']['fish_catch'], {'value': 150, 'unit': 'percent'})
        self.assertEqual(result['buffs']['fish_quality'], {'value': 2.5, 'unit': 'multiplier'})
        self.assertEqual(result['buffs']['xp_multiplier'], {'value': 3, 'unit': 'multiplier'})
        self.assertEqual(result['buffs']['fishing_cooldown'], {'value': 3.5, 'unit': 'seconds'})

    def test_missing_and_unrecognized_values_are_not_invented(self):
        result = parse_responses(profile='Balance: lots\nNew feature: something')
        self.assertIsNone(result['profile']['balance'])
        self.assertIsNone(result['profile']['pet'])
        self.assertEqual(len(result['warnings']), 2)
        self.assertEqual(len(result['unparsed']['profile']), 2)

    def test_conflicting_duplicates_are_unknown(self):
        result = parse_responses(profile='Balance: $100\nBalance: $200\nBalance: $100')
        self.assertIsNone(result['profile']['balance'])
        self.assertTrue(any('Conflicting' in warning for warning in result['warnings']))

    def test_unknown_buff_units_are_rejected(self):
        result = parse_responses(buffs='Fish catch: 25\nFishing cooldown: 3x')
        self.assertIsNone(result['buffs']['fish_catch'])
        self.assertIsNone(result['buffs']['fishing_cooldown'])
        self.assertEqual(len(result['warnings']), 2)

    def test_prestige_and_partial_responses(self):
        result = parse_responses(profile='P3 L149, 250 XP to next level')
        self.assertEqual(result['profile']['prestige'], 3)
        self.assertIsNone(result['profile']['balance'])
        self.assertIsNone(result['buffs']['fish_catch'])

    def test_empty_input_is_reported(self):
        result = parse_responses()
        self.assertTrue(result['warnings'])
        self.assertIsNone(result['profile']['account_name'])

    def test_cli_json_matches_shared_parser(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'profile.txt'
            path.write_text('Balance: $123\nPet: None', encoding='utf-8')
            run = subprocess.run([sys.executable, '-m', 'src.player_cli', '--profile', str(path), '--json'],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout), parse_responses(profile=path.read_text()))

    def test_cli_accepts_prestige_shop_file(self):
        fixture = Path(__file__).parent / 'fixtures/live_prestige_shop_p4.txt'
        run = subprocess.run([sys.executable, '-m', 'src.player_cli', '--prestige-shop', str(fixture), '--json'],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['prestige']['inferred_prestige_minimum'], 4)


if __name__ == '__main__':
    unittest.main()
