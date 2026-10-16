import unittest

from src.account_replies import PARSERS
from src.actions import Action, outcome, reads_after
from src.paste_detect import _kind

FISH = Action('buy', 'Fish20m', shop='boosts')
TREASURE = Action('buy', 'Treasure20m', shop='boosts')
WORKER = Action('buy', 'Auto30m', shop='boosts')


class BoostReplies(unittest.TestCase):
    def test_success_lines_give_the_duration(self):
        self.assertEqual(outcome(WORKER, ':white_check_mark: You hired a worker for the next 30 minutes.\n'
                                         'The fish he catches will automatically be added to your inventory.'),
                         ('done', 'boost on for 30m'))
        self.assertEqual(outcome(TREASURE, ':white_check_mark: You will now find more treasure for the next 20 minutes.'),
                         ('done', 'boost on for 20m'))
        self.assertEqual(outcome(FISH, ':white_check_mark: You will now catch more fish for the next 20 minutes.'),
                         ('done', 'boost on for 20m'))

    def test_already_boosting_is_satisfied_with_time_left(self):
        self.assertEqual(outcome(FISH, 'You are already boosting that! Time left: 19m 38s'),
                         ('done', 'already boosting, 19m 38s left'))

    def test_wrong_boost_reply_is_unclear(self):
        self.assertEqual(outcome(FISH, ':white_check_mark: You hired a worker for the next 30 minutes.')[0], 'unclear')

    def test_cant_afford_fails(self):
        self.assertEqual(outcome(FISH, "You can't afford that!"), ('failed', "can't afford it"))

    def test_boost_buy_rereads_boosts(self):
        self.assertEqual(reads_after(FISH), ('refresh_profile', 'refresh_boosts'))

    def test_active_boosts_map_to_buys(self):
        parsed = PARSERS['boosts']('Active Boosts\nMore Fish: 19m 0s\nMore Treasure: 18m 56s\nWorker: 28m 29s\n'
                                   'Global Boost Duration: 1h 1m 35s\nCurrent Booster: Trinity')
        self.assertEqual(parsed['values']['boosts'], {'Fish20m': 1140, 'Treasure20m': 1136, 'Auto30m': 1709})
        self.assertEqual(parsed['values']['global_duration'], '1h 1m 35s')

    def test_active_boosts_read_through_ocr_glyphs(self):
        # An icon before the line, as OCR reads it, must not hide the boost (else it is bought again and again).
        parsed = PARSERS['boosts']("Active Boosts\nr' More Fish: 19m 0s\n\u00a9 more treasure : 18m 56s\n"
                                   '@Worker:1h 2m 3s\nGlobal Boost Duration: 1h 1m 35s')
        self.assertEqual(parsed['values']['boosts'], {'Fish20m': 1140, 'Treasure20m': 1136, 'Auto30m': 3723})
        self.assertEqual(parsed['values']['global_duration'], '1h 1m 35s')

    def test_no_active_boosts(self):
        parsed = PARSERS['boosts']('Active Boosts\nGlobal Boost Duration: 36m 2s\nCurrent Booster:')
        self.assertEqual(parsed['values']['boosts'], {})

    def test_shop_boosts_inventory(self):
        parsed = PARSERS['shop_boosts']('Temporary Boosts\nInventory: 2,314 :gold_fish:, and 1,080 :emerald_fish:.\n'
                                        'Worker (10m)\nHire a worker to fish for you for 10 minutes. /buy Auto10m')
        self.assertEqual(parsed['values'], {'gold': 2314, 'emerald': 1080})
        self.assertEqual(parsed['unread'], [])

    def test_shop_boosts_inventory_through_ocr_glyphs(self):
        parsed = PARSERS['shop_boosts']('Temporary Boosts\nInventory: 2,314 \u00a9, and 1,080 @.')
        self.assertEqual(parsed['values'], {'gold': 2314, 'emerald': 1080})

    def test_shop_boosts_without_inventory_is_unread(self):
        self.assertEqual(PARSERS['shop_boosts']('Temporary Boosts')['unread'], ['Emerald Fish'])

    def test_quests_with_completed_and_special(self):
        parsed = PARSERS['quests']('Quest List\nQuests have multiple tiers, so keep fishing to get maximum rewards!\n'
                                   'Daily Level-ups - COMPLETED\nLevel up 3 time(s)\n'
                                   'Daily Fishing - 2500/5000\nCatch 5000 fish.\n'
                                   'SPECIAL DAILY QUEST:\nPet Locator - 0/1\nFind a pet.\nQuests reset in 5h 29m')
        values = parsed['values']
        self.assertTrue(values['daily']['Daily Level-ups']['done'])
        self.assertEqual(values['daily']['Daily Fishing']['progress'], 2500)
        self.assertFalse(values['daily']['Daily Fishing']['done'])
        self.assertEqual(values['special'], {'Pet Locator': {'progress': 0, 'goal': 1, 'done': False,
                                                             'task': 'Find a pet'}})
        self.assertEqual(values['resets_in_minutes'], 329)

    def test_markers(self):
        self.assertEqual(_kind('Temporary Boosts'), 'shop_boosts')
        self.assertEqual(_kind('Quest List'), 'quests')
