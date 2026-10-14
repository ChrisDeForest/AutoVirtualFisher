import unittest
from dataclasses import replace

from src.actions import Action, AMOUNT_TABS, check, command_line, outcome, reads_after


class CommandTests(unittest.TestCase):
    def test_command_lines(self):
        self.assertEqual(command_line(Action('buy', 'Better Fish', level=20, shop='upgrades')), '/buy better fish')
        self.assertEqual(command_line(Action('rod', 'Lava Rod')), '/rod lava rod')
        self.assertEqual(command_line(Action('biome', 'Volcanic')), '/biome volcanic')
        self.assertEqual(command_line(Action('bait', 'Magic Bait')), '/bait magic bait')
        self.assertEqual(command_line(Action('bait', '')), '/bait none')
        self.assertEqual(command_line(Action('pet', 'Zebrafish')), '/pet zebrafish')
        self.assertEqual(command_line(Action('sell')), '/sell all')
        self.assertEqual(AMOUNT_TABS, 3)

    def test_round_trip_and_labels(self):
        action = Action('buy', 'Magic Bait', amount=500, shop='bait', price=1250.0)
        self.assertEqual(Action.from_dict(action.to_dict()), action)
        self.assertEqual(Action.from_dict({'act': 'rod', 'name': 'Lava Rod'}), Action('rod', 'Lava Rod'))
        self.assertEqual(action.label(), 'Buy 500 Magic Bait')
        self.assertEqual(Action('buy', 'Salesman', level=8, shop='upgrades').label(), 'Buy Salesman 8')
        self.assertEqual(Action('rod', 'Lava Rod').label(), 'Switch to Lava Rod')
        self.assertEqual(Action('bait', '').label(), 'Switch to no bait')
        self.assertEqual(Action('sell').label(), 'Sell all fish')


class OutcomeTests(unittest.TestCase):
    """The replies the user sent by hand on 2026-10-13, as OCR may read them (emoji lost or misread)."""

    def test_buys(self):
        better = Action('buy', 'Better Fish', level=20, shop='upgrades')
        self.assertEqual(outcome(better, 'v You just bought Better Fish level 20!'),
                         ('done', 'bought Better Fish level 20'))
        self.assertEqual(outcome(better, "x You can't afford that upgrade."), ('failed', "can't afford it"))
        self.assertEqual(outcome(better, 'You cant afford that upgrade.')[0], 'failed')
        self.assertEqual(outcome(better, 'You just bought Better Fish level 19!')[0], 'unclear')
        self.assertEqual(outcome(better, 'something else')[0], 'unclear')
        # Seen live 2026-10-14 (Artifact Specialist 6, bought by hand earlier).
        self.assertEqual(outcome(better, 'x You already have that upgrade maxed.'), ('failed', 'already maxed'))

    def test_switches(self):
        self.assertEqual(outcome(Action('rod', 'Lava Rod'), 'v You switched to the Lava Rod.'),
                         ('done', 'using Lava Rod'))
        self.assertEqual(outcome(Action('rod', 'Magma Rod'), "x You don't own that rod."),
                         ('failed', 'the rod is not owned'))
        self.assertEqual(outcome(Action('rod', 'Lava Rod'), 'You switched to the Alloy Rod.')[0], 'failed')
        self.assertEqual(outcome(Action('biome', 'River'), 'You are now fishing in the River biome.')[0], 'done')
        self.assertEqual(outcome(Action('biome', 'Volcano'), 'x Invalid biome name'), ('failed', 'invalid biome'))
        self.assertEqual(outcome(Action('bait', 'Fish'),
                                 'You are now using Fish (60 remaining)\nFish catch: +55.0%')[0], 'done')
        self.assertEqual(outcome(Action('bait', ''), 'You are no longer using any bait.')[0], 'done')
        pet = 'v You set your active pet to your level 61 Zebrafish.\nFish catch: +13.94%'
        self.assertEqual(outcome(Action('pet', 'Zebrafish'), pet), ('done', 'pet Zebrafish'))
        self.assertEqual(outcome(Action('pet', 'Puffer'), pet)[0], 'failed')
        listing = 'Pets are permanent, they are not lost when you prestige.\nCurrent pet: Puffer.'
        self.assertEqual(outcome(Action('pet', 'Zebrafish'), listing), ('failed', 'no pet was selected'))

    def test_sell(self):
        text = 'epicmouse9925\nv You sold your entire inventory for $56,636.\nYou now have $6,892,166!'
        self.assertEqual(outcome(Action('sell'), text), ('done', 'sold for $56,636'))


class ReadsAndChecksTests(unittest.TestCase):
    def test_reads_after(self):
        self.assertEqual(reads_after(Action('buy', 'Salesman', level=8, shop='upgrades')),
                         ('refresh_profile', 'refresh_upgrades'))
        self.assertEqual(reads_after(Action('buy', 'Magic Bait', amount=500, shop='bait')),
                         ('refresh_profile', 'refresh_bait'))
        self.assertEqual(reads_after(Action('rod', 'Lava Rod')), ('refresh_profile', 'refresh_buffs', 'refresh_rod'))
        self.assertEqual(reads_after(Action('sell')), ('refresh_profile',))

    def test_checks_before_running(self):
        sections = {'shop_upgrades': {'values': {'items': {'Salesman': {'level': 8}}}},
                    'rod': {'values': {'current': 'Lava Rod', 'owned': ['Lava Rod', 'Alloy Rod']}},
                    'biome': {'values': {'current': 'Volcanic', 'unlocked': ['River', 'Volcanic']}},
                    'bait': {'values': {'current': 'Fish', 'owned': {'Fish': 60}}},
                    'pet': {'values': {'current': 'Zebrafish', 'owned': {'Zebrafish': {'level': 61}}}}}
        profile = {'balance': 1000}
        self.assertEqual(check(Action('buy', 'Salesman', level=8, shop='upgrades', price=500), sections, profile),
                         'already level 8')
        self.assertIsNone(check(Action('buy', 'Salesman', level=9, shop='upgrades', price=500), sections, profile))
        self.assertEqual(check(Action('buy', 'Salesman', level=9, shop='upgrades', price=5000), sections, profile),
                         'not enough money ($1,000 of $5,000)')
        self.assertEqual(check(Action('rod', 'Lava Rod'), sections, profile), 'already using Lava Rod')
        self.assertIsNone(check(Action('rod', 'Alloy Rod'), sections, profile))
        self.assertEqual(check(Action('biome', 'Volcanic'), sections, profile), 'already using Volcanic')
        self.assertIsNone(check(Action('biome', 'River'), sections, profile))
        self.assertIsNone(check(Action('bait', ''), sections, profile))
        # Live reads miss lines ('/rod' saved current 'Lava', owned without Steel and Alloy Rod; '/biome' unlocked only
        # Ocean): ownership is left to the game's reply, and names compare loosely.
        live = {'rod': {'values': {'current': 'Lava', 'owned': ['Supporter Rod', 'Lava Rod']}},
                'biome': {'values': {'current': 'Volcanic', 'unlocked': ['Ocean']}}}
        self.assertEqual(check(Action('rod', 'Lava Rod'), live, profile), 'already using Lava Rod')
        self.assertIsNone(check(Action('rod', 'Alloy Rod'), live, profile))
        self.assertIsNone(check(Action('biome', 'River'), live, profile))
        self.assertIsNone(check(Action('bait', 'Magic Bait'), sections, profile))
        self.assertEqual(check(Action('rod', 'Steel Rod'), {}, {'rod': 'Steel Rod'}), 'already using Steel Rod')
        self.assertEqual(check(Action('pet', 'Zebrafish'), sections, profile), 'already using Zebrafish')
        self.assertIsNone(check(Action('sell'), sections, profile))

    def test_a_league_buy_needs_the_hooks(self):
        sections = {'shop_league': {'values': {'hooks': 11, 'items': {'Super Crates': {'level': 2}}}}}
        crates = Action('buy', 'Super Crates', level=3, shop='league', price=150)
        self.assertEqual(check(crates, sections, {}), 'not enough Hooks (11 of 150)')
        self.assertIsNone(check(replace(crates, price=10), sections, {}))
        self.assertIsNone(check(crates, {}, {}))                    # Hooks not read: left to the game's reply
