import json
import tempfile
import unittest
from pathlib import Path

from src.game_model_data import GameDataError, load_game_data


class GameDataTests(unittest.TestCase):
    def test_loads_tables_with_tiers_from_fish_reference(self):
        data = load_game_data()
        river = data['biomes']['River']
        self.assertEqual([tier['fish'] for tier in river['tiers']], ['Fish', 'Salmon', 'Cod', 'Tropical fish',
                                                                     'Pufferfish'])
        self.assertEqual([tier['price'] for tier in river['tiers']], [1, 3, 10, 50, 150])
        self.assertEqual([tier['xp'] for tier in data['biomes']['Ocean']['tiers']], [10, 25, 175, 400, 800])
        self.assertEqual(data['rods']['Fiberglass Rod']['mean_catch'], 8.5)
        self.assertTrue(data['rods']['Supporter Rod']['scales_with_biome'])
        self.assertEqual(data['baits']['Fish']['bonus_fish'], 1)
        self.assertEqual(data['pets']['Dolphin'], {'treasure_chance': 0.75, 'xp': 0.75})
        self.assertEqual(data['chest_order'][0], 'artifact')

    def test_shares_are_normalized(self):
        data = load_game_data()
        shares = data['rods']['Oceanium Rod']['shares']['Ocean']  # the wiki's row sums to 100.1%
        self.assertAlmostEqual(sum(shares), 1.0, places=12)
        self.assertAlmostEqual(shares[0], 0.35 / 1.001, places=12)
        self.assertAlmostEqual(sum(data['example_shares']), 1.0, places=12)

    def test_rods_follow_the_miraheze_wiki(self):
        # docs/guides/miraheze/Rod.wikitext (edited 2026-08-13) overrides the older guides.
        rods = load_game_data()['rods']
        self.assertEqual([rods[name]['mean_catch'] for name in ('Steel Rod', 'Magma Rod', 'Oceanium Rod',
                                                                 'Floating Rod')], [6.5, 11.5, 12.5, 22.5])
        self.assertEqual(rods['Alien Rod']['treasure_quality'], 1.05)
        self.assertEqual(rods['Meteor Rod']['biomes'], ['Space', 'Alien'])
        self.assertEqual(rods['Steel Rod']['shares']['River'], [0.2, 0.35, 0.45, 0, 0], '"35$" is 35%')
        self.assertEqual(rods['Infinity Rod']['shares']['Ocean'][0], 0, 'N/A is 0')
        self.assertAlmostEqual(rods['Infinity Rod']['shares']['Sky'][4], 0.008, places=12)
        # The Space table's columns are Fish, Salmon, Rainbow Fish, Space Fish, Galactic Crab: matched by name.
        self.assertEqual([round(s, 9) for s in rods['Sky Rod']['shares']['Space']], [0.5, 0.33, 0.124, 0.044, 0.002])
        self.assertEqual([round(s, 9) for s in rods['Space Rod']['shares']['Space']], [0.4, 0.25, 0.2, 0.145, 0.005])
        for name, mean, chance, quality, price in (('Ultimate Depths Rod', 43.5, 0.07, 1.05, 50 * 10 ** 12),
                                                   ('Abyssal Supermagnet', 25, 0.05, 1.5, 250 * 10 ** 12),
                                                   ('Dark Rod', 46, 0.07, 1.1, 10 ** 15)):
            rod = rods[name]
            self.assertEqual((rod['mean_catch'], rod['treasure_chance'], rod['treasure_quality'], rod['price'],
                              rod['unlock_level']), (mean, chance, quality, price, 2500), name)
        self.assertEqual([round(s, 9) for s in rods['Dark Rod']['shares']['Alien']], [0.15, 0.25, 0.46, 0.11, 0.03])
        self.assertNotIn('Abyss', rods['Dark Rod']['shares'], 'Abyss has no fish in fish.json: not modelled')
        self.assertEqual(sorted(name for name, rod in rods.items() if 'River' in rod['shares']),
                         ['Alloy Rod', 'Fiberglass Rod', 'Heavy Rod', 'Improved Rod', 'Plastic Rod', 'Steel Rod',
                          'Supporter Rod'])

    def test_other_tables_follow_the_miraheze_wiki(self):
        data = load_game_data()
        self.assertEqual(data['baits']['Worms']['bonus_fish'], 3)  # Bait.wikitext
        self.assertEqual(data['baits']['Magnet']['treasure_chance'], 0.4)
        self.assertEqual(data['chests']['legendary']['diamond'], 0.2)  # progression-model.md (M9)
        self.assertNotIn('Abyss', data['biomes'])

    def test_shares_are_matched_to_tiers_by_fish_name(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'game_model.json'
            good = json.loads(Path('src/reference/game_model.json').read_text(encoding='utf-8'))
            good['rods']['Lava Rod']['shares'] = {'Volcanic': {'Hot cod': 4, 'Salmon': 15, 'Fiery puffer': 19,
                                                               'Cod': 25, 'Tropical fish': 37}}
            path.write_text(json.dumps(good), encoding='utf-8')
            self.assertEqual(load_game_data(path)['rods']['Lava Rod']['shares']['Volcanic'],
                             [0.15, 0.25, 0.37, 0.19, 0.04])
            good['rods']['Lava Rod']['shares'] = {'Volcanic': {'Hot cod': 4, 'Salmon': 15, 'Banana': 19,
                                                               'Cod': 25, 'Tropical fish': 37}}
            path.write_text(json.dumps(good), encoding='utf-8')
            with self.assertRaisesRegex(GameDataError, 'Lava Rod in Volcanic.*Banana'):
                load_game_data(path)
            good['rods']['Lava Rod']['shares'] = {'Volcanic': [15, 25, 37, 19, 4]}
            path.write_text(json.dumps(good), encoding='utf-8')
            with self.assertRaisesRegex(GameDataError, 'Lava Rod in Volcanic'):
                load_game_data(path)

    def test_a_broken_file_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'game_model.json'
            path.write_text('{"biomes": {}}', encoding='utf-8')
            with self.assertRaises(GameDataError):
                load_game_data(path)
            path.write_text('not json', encoding='utf-8')
            with self.assertRaises(GameDataError):
                load_game_data(path)
            good = json.loads(Path('src/reference/game_model.json').read_text(encoding='utf-8'))
            del good['rods']['Lava Rod']['mean_catch']
            path.write_text(json.dumps(good), encoding='utf-8')
            with self.assertRaisesRegex(GameDataError, 'Lava Rod'):
                load_game_data(path)


from src.game_model import Account, NotModelled, Setup, Value, buffs, max_charm_level, round_half_up, worst

DATA = load_game_data()


def example_data():
    """The buff calculator's example rod (calculator K30:K32 and J22:J26), added to the real tables."""
    data = load_game_data()
    data['rods']['Example Rod'] = {'mean_catch': 9.5, 'treasure_chance': 0.05, 'treasure_quality': 1.0,
                                   'unlock_level': 1, 'price': 0, 'biomes': ['River'],
                                   'shares': {'River': [0.84, 0.135, 0.025, 0, 0]}}
    return data


# The calculator's example inputs (cells B6:B34, F6:F27, J5:J9, N5:N27, O30:O32).
EXAMPLE_ACCOUNT = Account(level=1, prestige=0,
                          upgrades={'Better Fish': 10, 'Salesman': 10, 'More Chests': 2, 'Artifact Specialist': 2},
                          clan={'fish catch': 2, 'xp': 3, 'sell price': 2, 'treasure quality': 1.5}, boats=4,
                          official_server=True)
EXAMPLE_SETUP = Setup(rod='Example Rod', biome='River', bait='Fish', fish_boost=True, global_booster=True)


class BuffTests(unittest.TestCase):
    def test_calculator_example_buffs(self):
        result = buffs(EXAMPLE_ACCOUNT, EXAMPLE_SETUP, example_data())
        expected = {'sell_price': 1.8, 'fish_catch': 2.304225, 'fish_quality': 3.0, 'treasure_chance': 1.1,
                    'treasure_quality': 1.5, 'xp_multiplier': 0.805, 'fishing_cooldown': 2.5,
                    'treasure_quantity': 1.2, 'charm_quantity': 1.0}  # calculator B38:B50
        for name, value in expected.items():
            self.assertAlmostEqual(result[name].value, value, places=9, msg=name)
            self.assertEqual(result[name].source, 'formula')

    def test_this_accounts_sell_price(self):
        # P4, level 49, Salesman 7, International Ties and Business Education: /buffs showed 2.91x.
        account = Account(level=49, prestige=4, upgrades={'Salesman': 7},
                          prestige_upgrades={'International Ties': 1, 'Business Education': 1})
        result = buffs(account, Setup(rod='Fiberglass Rod', biome='River'), DATA)
        self.assertAlmostEqual(result['sell_price'].value, 2.9106, places=9)

    def test_dolphin_level_51_and_pet_effectiveness(self):
        account = Account(level=40, prestige=0, pets={'Dolphin': 51})
        result = buffs(account, Setup(rod='Fiberglass Rod', biome='River', pet='Dolphin'), DATA)
        self.assertAlmostEqual(result['treasure_chance'].value, 1.361875, places=9)  # +36.1875%
        self.assertAlmostEqual(result['xp_multiplier'].value, 1.361875, places=9)

    def test_bait_rules_and_cooldown_clamp(self):
        account = Account(level=40, prestige=0, upgrades={'Bait Lover': 2, 'Bait Helper': 1}, boats=10)
        magnet = buffs(account, Setup(rod='Fiberglass Rod', biome='River', bait='Artifact Magnet'), DATA)
        strength = 1.3 * 1.1
        self.assertAlmostEqual(magnet['fish_catch'].value, (1 - 0.3 / strength) * 0.95, places=9)
        self.assertAlmostEqual(magnet['treasure_quality'].value, 1 + 0.5 * strength, places=9)
        self.assertEqual(magnet['fishing_cooldown'].value, 2.0)  # 3.5 - 2.5 clamps at 2
        sky = buffs(Account(level=300, prestige=0), Setup(rod='Space Rod', biome='Alien'), DATA)
        self.assertEqual(sky['fishing_cooldown'].value, 5.0)  # 3.5 + 4 clamps at 5

    def test_highly_experienced_follows_the_miraheze_wiki(self):
        # Upgrade.wikitext: +15% XP per level (the calculator and progression-model.md had +20%).
        account = Account(level=100, prestige=0, upgrades={'Highly Experienced': 4, 'Experienced': 5})
        result = buffs(account, Setup(rod='Lava Rod', biome='Volcanic'), DATA)
        self.assertAlmostEqual(result['xp_multiplier'].value, 1.5 * 1.6, places=12)

    def test_marketing_compounds_to_50(self):
        for level, expected in ((10, 1.05 ** 10), (52, 1.05 ** 50 * 1.1)):
            account = Account(level=1, prestige=0, charms={'Marketing': level})
            self.assertAlmostEqual(buffs(account, Setup(rod='Plastic Rod', biome='River'), DATA)['sell_price'].value,
                                   expected, places=9)

    def test_an_assumed_input_makes_its_buffs_estimates(self):
        account = Account(level=60, prestige=0, assumed={'Fish Ovens': 'the special shop is not read yet'})
        result = buffs(account, Setup(rod='Lava Rod', biome='Volcanic'), DATA)
        self.assertEqual(result['sell_price'].source, 'estimate')
        self.assertEqual(result['sell_price'].why, ('Fish Ovens: the special shop is not read yet',))
        self.assertEqual(result['fish_catch'].source, 'formula')

    def test_unknown_names_are_not_modelled(self):
        with self.assertRaisesRegex(NotModelled, 'unknown rod Banana Rod'):
            buffs(Account(level=1, prestige=0), Setup(rod='Banana Rod', biome='River'), DATA)
        with self.assertRaisesRegex(NotModelled, 'unknown bait Gum'):
            buffs(Account(level=1, prestige=0), Setup(rod='Plastic Rod', biome='River', bait='Gum'), DATA)

    def test_a_rod_not_listed_for_the_biome_is_an_estimate(self):
        # The wiki's lists may be incomplete; the model never goes dark over one.
        result = trip(Account(level=60, prestige=0), Setup(rod='Plastic Rod', biome='Volcanic'), DATA)
        note = 'Plastic Rod is not listed for Volcanic'
        for name in PER_TRIP:
            self.assertIn(result[name].source, ('estimate', 'not modelled'), name)
            self.assertIn(note, result[name].why, name)
        self.assertGreater(result['fish'].value, 0)
        listed = trip(Account(level=60, prestige=0), Setup(rod='Lava Rod', biome='Volcanic'), DATA)
        self.assertEqual(listed['fish'].source, 'formula')
        self.assertNotIn(note, listed['fish'].why)

    def test_buff_corrections_multiply_each_buff(self):
        account = Account(level=49, prestige=0)
        setup = Setup(rod='Fiberglass Rod', biome='River')
        plain = buffs(account, setup, DATA)
        corrected = buffs(account, setup, DATA, corrections={'fish_catch': 1.05, 'sell_price': 1.2,
                                                             'fishing_cooldown': 0.9})
        self.assertAlmostEqual(corrected['fish_catch'].value, plain['fish_catch'].value * 1.05, places=12)
        self.assertAlmostEqual(corrected['sell_price'].value, plain['sell_price'].value * 1.2, places=12)
        self.assertAlmostEqual(corrected['fishing_cooldown'].value, plain['fishing_cooldown'].value * 0.9, places=12)
        self.assertEqual(corrected['xp_multiplier'].value, plain['xp_multiplier'].value, 'no correction given: 1')
        self.assertEqual(buffs(account, setup, DATA, corrections=None)['fish_catch'], plain['fish_catch'])

    def test_helpers(self):
        self.assertEqual((round_half_up(2.5), round_half_up(7.5), round_half_up(1.49)), (3, 8, 1))
        self.assertEqual((max_charm_level(4), max_charm_level(100)), (14, 112))
        self.assertEqual(worst(Value(1.0), Value(2.0, 'estimate', ('a',)), Value(3.0, 'measured')),
                         ('estimate', ('a',)))


from src.game_model import base_shares, fish_shares, trip


class TripTests(unittest.TestCase):
    def test_calculator_example_trip(self):
        result = trip(EXAMPLE_ACCOUNT, EXAMPLE_SETUP, example_data())
        self.assertAlmostEqual(result['fish'].value, 44.03196656, places=7)  # calc F38 (G38 = 40.33520625)
        self.assertEqual([round(row['share'], 9) for row in result['shares']], [0.52, 0.405, 0.075, 0.0, 0.0])
        self.assertAlmostEqual(result['money_fish'].value, 196.9549864, places=6)  # calc E48
        self.assertAlmostEqual(result['xp_fish'].value, 60.43497491, places=6)  # calc F48
        self.assertEqual(result['mix_source'], 'formula')

    def test_shares_fill_from_the_best_tier(self):
        self.assertEqual(fish_shares([0.5, 0.3, 0.2, 0, 0], 1.0), [0.5, 0.3, 0.2, 0, 0])
        self.assertEqual([round(s, 9) for s in fish_shares([0.5, 0.3, 0.2, 0, 0], 3.0)], [0.0, 0.4, 0.6, 0, 0])
        # Below quality 1 the bottom tier takes the rest: a catch is always all fish.
        self.assertEqual([round(s, 9) for s in fish_shares([0.5, 0.3, 0.2, 0, 0], 0.5)], [0.75, 0.15, 0.1, 0, 0])
        self.assertAlmostEqual(sum(fish_shares([0.5, 0.3, 0.2, 0, 0], 0.7)), 1.0, places=12)

    def test_corrections_flow_into_the_trip(self):
        account, setup = Account(level=49, prestige=0), Setup(rod='Fiberglass Rod', biome='River')
        plain = trip(account, setup, DATA)
        corrected = trip(account, setup, DATA, corrections={'fish_catch': 1.05, 'sell_price': 1.2})
        mean = DATA['rods']['Fiberglass Rod']['mean_catch']
        self.assertAlmostEqual(corrected['fish'].value - plain['fish'].value,
                               plain['buffs']['fish_catch'].value * 0.05 * mean, places=9)
        self.assertAlmostEqual(corrected['money_fish'].value, plain['money_fish'].value * 1.05 * 1.2, places=9)

    def test_assumed_fish_inputs_make_fish_an_estimate(self):
        special = 'the special shop is not read yet'
        account = Account(level=60, prestige=0, assumed={'Duplicator': special, 'Duplicator 2.0': special})
        result = trip(account, Setup(rod='Lava Rod', biome='Volcanic'), DATA)
        self.assertEqual(result['fish'].source, 'estimate')
        self.assertIn(f'Duplicator: {special}', result['fish'].why)
        self.assertEqual(result['gold'].source, 'formula')
        boats = Account(level=60, prestige=0, assumed={'boats': 'the boat shop is not read yet (/shop boats)'})
        result = trip(boats, Setup(rod='Lava Rod', biome='Volcanic'), DATA)
        self.assertEqual(result['fish'].why, ('boats: the boat shop is not read yet (/shop boats)',))
        self.assertEqual(result['buffs']['fishing_cooldown'].source, 'estimate')
        frenzy = Account(level=60, prestige=0, assumed={'Fishing Frenzy': 'not read yet'})
        self.assertEqual(trip(frenzy, Setup(rod='Lava Rod', biome='Volcanic'), DATA)['fish'].source, 'formula',
                         'Fishing Frenzy does nothing without a fish boost')
        self.assertEqual(trip(frenzy, Setup(rod='Lava Rod', biome='Volcanic', fish_boost=True), DATA)['fish'].source,
                         'estimate')

    def test_an_assumed_input_without_effect_is_not_an_estimate(self):
        unread = 'not read yet'
        account = Account(level=60, prestige=0, assumed={name: unread for name in (
            'Bait Lover', 'Bait Helper', 'Boost Booster', 'Fishing Frenzy')})
        plain = trip(account, Setup(rod='Lava Rod', biome='Volcanic'), DATA)
        for name in ('fish', 'money_fish', 'xp_fish', 'chest_chance'):
            self.assertEqual(plain[name].source, 'formula', name)
        for name in ('fish_catch', 'fish_quality', 'treasure_chance', 'treasure_quality', 'xp_multiplier'):
            self.assertEqual(plain['buffs'][name].source, 'formula', name)
        # Worms: only bonus fish and XP, so Bait Lover and Bait Helper reach fish caught and XP, not fish catch.
        worms = trip(account, Setup(rod='Lava Rod', biome='Volcanic', bait='Worms'), DATA)
        self.assertEqual(worms['fish'].why, (f'Bait Lover: {unread}', f'Bait Helper: {unread}'))
        self.assertEqual(worms['buffs']['xp_multiplier'].source, 'estimate')
        self.assertEqual((worms['buffs']['fish_catch'].source, worms['buffs']['treasure_chance'].source),
                         ('formula', 'formula'))
        boosted = trip(account, Setup(rod='Lava Rod', biome='Volcanic', fish_boost=True, treasure_boost=True), DATA)
        self.assertEqual(boosted['buffs']['fish_catch'].why, (f'Boost Booster: {unread}', f'Fishing Frenzy: {unread}'))
        self.assertEqual(boosted['buffs']['treasure_chance'].why, (f'Boost Booster: {unread}',
                                                                   f'Fishing Frenzy: {unread}'))
        # Support Bait's pet effectiveness carries Bait Lover into every buff the pet gives.
        pet = Account(level=60, prestige=0, pets={'Dolphin': 10}, assumed={'Bait Lover': unread})
        support = buffs(pet, Setup(rod='Lava Rod', biome='Volcanic', bait='Support Bait', pet='Dolphin'), DATA)
        self.assertEqual(support['treasure_chance'].source, 'estimate')
        self.assertEqual(support['treasure_quality'].source, 'formula')

    def test_estimated_treasure_quantity_makes_exotic_fish_estimates(self):
        account = Account(level=60, prestige=0, assumed={'Quantity': '/charms is not read yet'})
        result = trip(account, Setup(rod='Lava Rod', biome='Volcanic'), DATA)
        for name in ('gold', 'emerald', 'lava', 'diamond'):
            self.assertEqual(result[name].source, 'estimate', name)
            self.assertIn('Quantity: /charms is not read yet', result[name].why)
        self.assertEqual(result['chest_chance'].source, 'formula')

    def test_missing_mix_falls_back_and_is_labelled(self):
        self.assertEqual(base_shares(Setup(rod='Fiberglass Rod', biome='River'), DATA)[1], 'formula')
        # Lava Rod (mean 9) has no River table: Alloy, Fiberglass and Supporter (8.5) tie; the first name wins.
        river = base_shares(Setup(rod='Lava Rod', biome='River'), DATA)
        self.assertEqual((river[0], river[1]), (DATA['rods']['Alloy Rod']['shares']['River'], 'estimate'))
        self.assertIn('from Alloy Rod', river[2][0])
        volcanic = base_shares(Setup(rod='Oceanium Rod', biome='Volcanic'), DATA)  # mean 12.5: Magma (11.5) nearest
        self.assertEqual((volcanic[0], volcanic[1]), (DATA['rods']['Magma Rod']['shares']['Volcanic'], 'estimate'))
        self.assertIn('from Magma Rod', volcanic[2][0])
        bare = load_game_data()
        for rod in bare['rods'].values():
            rod['shares'].pop('River', None)
        example = base_shares(Setup(rod='Fiberglass Rod', biome='River'), bare)
        self.assertEqual((example[0], example[1]), (bare['example_shares'], 'estimate'))
        self.assertIn('calculator example', example[2][0])
        learned = {'Fiberglass Rod|River': {'shares': [0.7, 0.2, 0.1, 0, 0], 'trips': 640}}
        measured = base_shares(Setup(rod='Fiberglass Rod', biome='River'), DATA, learned)
        self.assertEqual(measured, ([0.7, 0.2, 0.1, 0, 0], 'measured', ('fish mix measured over 640 trips',)))

    def test_estimated_mix_makes_money_an_estimate(self):
        result = trip(Account(level=49, prestige=4), Setup(rod='Lava Rod', biome='River'), DATA)
        self.assertEqual(result['money_fish'].source, 'estimate')
        self.assertEqual(result['fish'].source, 'formula')

    def test_fishing_frenzy_strengthens_the_fish_boost(self):
        # Temporary_Buff.wikitext: fish boost 1.4 + 0.02 BB + 0.02 FF.
        account = Account(level=300, prestige=0, upgrades={'Boost Booster': 2, 'Fishing Frenzy': 3})
        result = buffs(account, Setup(rod='Lava Rod', biome='Volcanic', fish_boost=True), DATA)
        self.assertAlmostEqual(result['fish_catch'].value, (1.4 + 0.04 + 0.06) * 0.95, places=12)
        plain = buffs(account, Setup(rod='Lava Rod', biome='Volcanic'), DATA)
        self.assertAlmostEqual(plain['fish_catch'].value, 0.95, places=12)

    def test_bait_bonus_fish_follows_the_wiki(self):
        # Bait.wikitext: bonus fish x (1 + 0.15 BL)(1 + 0.1 BH); Rod.wikitext: "CNT += BA (rounded)", half away from
        # zero. Worms (+3) with Bait Lover 1, Bait Helper 1: 3 x 1.15 x 1.1 = 3.795, rounded 4 (additive: 3.75).
        def caught(lover, helper, bait='Worms'):
            account = Account(level=300, prestige=0, upgrades={'Bait Lover': lover, 'Bait Helper': helper})
            return trip(account, Setup(rod='Steel Rod', biome='River', bait=bait), DATA)['fish'].value
        mean = 0.95 * 6.5
        self.assertAlmostEqual(caught(1, 1), mean + 4, places=9)
        self.assertAlmostEqual(caught(0, 0), mean + 3, places=9)
        # Support Bait (+5) with Bait Helper 1: 5.5 rounds half away from zero to 6.
        support = Account(level=300, prestige=0, upgrades={'Bait Helper': 1})
        result = trip(support, Setup(rod='Steel Rod', biome='River', bait='Support Bait'), DATA)
        fish_catch = result['buffs']['fish_catch'].value
        self.assertAlmostEqual(result['fish'].value, fish_catch * 6.5 + 6, places=9)

    def test_supporter_rod_scales_with_biome_and_duplication(self):
        account = Account(level=300, prestige=0, upgrades={'Duplicator': 5})
        river = trip(account, Setup(rod='Supporter Rod', biome='River'), DATA)['fish'].value
        sky = trip(account, Setup(rod='Supporter Rod', biome='Sky'), DATA)['fish'].value
        mean = 0.95 * 8.5
        self.assertAlmostEqual(river, mean * 1 * 1 * 1.1, places=9)
        self.assertAlmostEqual(sky, mean * 2.0 * 0.12 * 1.1, places=9)


from dataclasses import replace

from src.game_model import PER_TRIP, chests, per_hour


class ChestTests(unittest.TestCase):
    def test_calculator_example_chests(self):
        result = trip(EXAMPLE_ACCOUNT, EXAMPLE_SETUP, example_data())
        tiers = {'common': 0.009625, 'uncommon': 0.02475, 'rare': 0.014025, 'epic': 0.0045375,
                 'legendary': 0.00165, 'artifact': 0.0004125}  # calc J39:J44
        for tier, chance in tiers.items():
            self.assertAlmostEqual(result['chest_tiers'][tier], chance, places=12, msg=tier)
        self.assertAlmostEqual(result['chest_chance'].value, 0.055, places=12)  # calc B42
        self.assertAlmostEqual(result['gold'].value, 0.0460925025, places=12)  # calc N40
        self.assertAlmostEqual(result['emerald'].value, 0.0169651625, places=12)  # calc N41
        self.assertEqual((result['lava'].value, result['diamond'].value), (0, 0))  # River
        self.assertEqual(result['charms'].value, 0)  # level 1: charms need level 20 (calc J12)
        self.assertEqual(result['super_crate'].value, 0)
        self.assertEqual(result['money_chests'].source, 'not modelled')

    def test_charms_from_level_20(self):
        account = replace(EXAMPLE_ACCOUNT, level=25)
        result = trip(account, EXAMPLE_SETUP, example_data())
        # calc N44 with B47 = 1, B48 = 1.5, all eight kinds open: 0.02 rare + 0.25 epic + 0.4 legendary
        # + 1.5 x 0.75 artifact.
        self.assertAlmostEqual(result['charms'].value, 0.0025389375, places=12)
        maxed = replace(account, charms={kind: 10 for kind in ('Marketing', 'Endurance', 'Haste', 'Quantity')})
        self.assertAlmostEqual(trip(maxed, EXAMPLE_SETUP, example_data())['charms'].value, 0.0025389375 / 2, places=12)

    def test_super_crates_and_lava_and_diamond_by_biome(self):
        account = Account(level=120, prestige=4, upgrades={'Super Crates': 2})
        ocean = trip(account, Setup(rod='Golden Rod', biome='Ocean'), DATA)
        quality = 1.0  # no treasure quality buffs, Golden Rod base 1.0
        self.assertAlmostEqual(ocean['super_crate'].value, 2 * 0.00025 * quality, places=12)  # calc J45
        self.assertGreater(ocean['lava'].value, 0)
        self.assertGreater(ocean['diamond'].value, 0)
        river = trip(account, Setup(rod='Golden Rod', biome='River'), DATA)
        self.assertEqual(river['lava'].value > 0, True, 'super crates give lava in any biome')

    def test_super_crates_2_golden_values(self):
        # Golden Rod in Ocean, no buffs: treasure chance 0.13, treasure quality 1, quantity 1, charm quantity 1.
        # J45 super crate chance = 2 x 0.00025 x 1 = 0.0005; each tier share x 0.13 x (1 - 0.0005) = x 0.129935.
        # N40 gold: sum of (a + 1) x gold x share = 0.375(.45) + 0.625(.3) + 1.178(.17) + 0.8875(.055) + 0.645(.02)
        #   + 0.275(.005) = 0.6195975; x 0.129935 = 0.0805074012; plus the super crate term
        #   M52 + L48 x J45 = 13 x 0.0005 x 0.4 + 0.4 x 0.0005 = 0.0028.
        # N44 charms: 0.02 rare + 0.25 epic + 0.4 legendary = 0.00326786525; artifact B48 1.5 x 0.000649675 x 0.75
        #   = 0.00073088438; super crates B49 5 x C30 0.57 x 0.0005 = 0.001425 (all eight kinds open).
        account = Account(level=120, prestige=4, upgrades={'Super Crates': 2})
        result = trip(account, Setup(rod='Golden Rod', biome='Ocean'), DATA)
        self.assertAlmostEqual(result['super_crate'].value, 0.0005, places=12)
        self.assertAlmostEqual(result['chest_tiers']['artifact'], 0.000649675, places=12)
        self.assertAlmostEqual(result['gold'].value, 0.0805074012 + 0.0028, places=10)
        self.assertAlmostEqual(result['charms'].value, 0.00326786525 + 0.000730884375 + 0.001425, places=12)

    def test_chest_money_and_xp_use_the_fitted_scale(self):
        result = trip(EXAMPLE_ACCOUNT, EXAMPLE_SETUP, example_data(), chest_scales={'money': 1000.0, 'xp': None})
        tiers = result['chest_tiers']
        mean = sum(tiers[t] * DATA['chests'][t]['multiplier'] for t in tiers) / sum(tiers.values())
        self.assertAlmostEqual(result['money_chests'].value, 0.0178915 * mean * 1000.0, places=9)  # calc L56
        self.assertEqual(result['money_chests'].source, 'estimate')
        self.assertEqual(result['xp_chests'].source, 'not modelled')

    def test_per_hour(self):
        result = trip(EXAMPLE_ACCOUNT, EXAMPLE_SETUP, example_data())
        hourly = per_hour(result, 3.0)
        self.assertEqual(set(hourly), set(PER_TRIP))
        self.assertAlmostEqual(hourly['money_fish'].value, 196.9549864 * 1200, places=3)
        self.assertIsNone(hourly['money_chests'].value)
        self.assertEqual(hourly['fish'].source, 'formula')

    def test_per_hour_from_estimated_seconds_is_an_estimate(self):
        result = trip(EXAMPLE_ACCOUNT, EXAMPLE_SETUP, example_data())
        reason = 'seconds per trip: cooldown + 0.5 s until this run has a rate'
        hourly = per_hour(result, Value(3.0, 'estimate', (reason,)))
        self.assertAlmostEqual(hourly['money_fish'].value, 196.9549864 * 1200, places=3)
        self.assertEqual((hourly['fish'].source, hourly['fish'].why), ('estimate', (reason,)))
        self.assertEqual(hourly['money_chests'].source, 'not modelled')
