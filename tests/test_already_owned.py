"""An already-owned reply satisfies just the correlated purchase and persists its ownership."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from src.actions import Action, check, outcome, reads_after
from src.account_state import apply_read, load_state
from src.automation_controller import AutomationController
from src.automation_journal import AutomationJournal
from src.automation_transport import SimulationTransport
from src.observation import Observation, VERIFIED, MISMATCH
from tests.test_automation_controller import Clock, CONFIG

REPLY = 'Virtual FisherAPP9:39 PM\n❌ You already own that boat.'


class AlreadyOwnedOutcomeTests(unittest.TestCase):
    def test_copied_reply_ocr_marks_and_discord_markup_are_satisfied(self):
        boat = Action('buy', 'Luxury Yacht', shop='boats')
        for text in (REPLY, 'x You already own that boat.',
                     'You already own that boat', '> **You already own that boat.**',
                     ':red_x: You already own that\nboat.', 'You **already own** that `boat`.'):
            with self.subTest(text=text):
                self.assertEqual(outcome(boat, text), ('done', 'already owned Luxury Yacht'))
        for category, shop, name in (('rod', 'rods', 'Lava Rod'), ('pet', 'pets', 'Puffer'),
                                     ('item', 'special', 'International Ties')):
            self.assertEqual(outcome(Action('buy', name, shop=shop), f'You already own that {category}.')[0], 'done')

    def test_ownership_does_not_satisfy_unrelated_actions_or_failures(self):
        boat = Action('buy', 'Luxury Yacht', shop='boats')
        for action, text in ((boat, 'You already own that rod.'),
                             (Action('rod', 'Lava Rod'), 'You already own that rod.'),
                             (boat, "You don't own that boat."),
                             (boat, 'You do not already own that boat.'),
                             (boat, "You can't afford that boat."),
                             (boat, 'You already have that upgrade maxed.')):
            with self.subTest(action=action, text=text):
                self.assertNotEqual(outcome(action, text)[0], 'done')


class AlreadyOwnedControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = Clock()
        self.sim = SimulationTransport(self.clock.monotonic, self.clock.utc, cooldown=3.5, latency=0.4)
        self.controller = AutomationController(self.sim, replace(CONFIG, capabilities=CONFIG.capabilities +
            ('act', 'refresh_boats'), resync_trips=100), AutomationJournal(self.root / 'journal'),
            self.root / 'fishing', self.root / 'review', monotonic=self.clock.monotonic, utc=self.clock.utc,
            state_path=self.root / 'account.json', plan_spawn=lambda work: None)
        self.controller.start()
        self.advance_until(lambda: any(intent.kind == 'fish' for _, intent in self.sim.sent))
        play = self.sim._play
        self.sim._play = lambda intent, now: (REPLY, 'action') if intent.kind == 'act' else play(intent, now)
        self.boat = Action('buy', 'Luxury Yacht', shop='boats')

    def advance_until(self, done):
        for _ in range(1200):
            if done():
                return
            self.controller.tick()
            self.clock.now = round(self.clock.now + 0.1, 6)
        self.fail('simulation did not reach the expected state')

    def test_already_owned_purchase_is_done_saved_refreshed_and_never_resent(self):
        c = self.controller
        c.queue_step('Luxury Yacht', [self.boat.to_dict()])
        self.advance_until(lambda: bool(c.action_log))
        self.assertEqual(c.action_log[0]['result'], 'done')
        self.assertEqual(c.action_log[0]['note'], 'already owned Luxury Yacht')
        self.assertTrue(c.account['sections']['shop_boats']['values']['items']['Luxury Yacht']['owned'])
        self.assertTrue(load_state(self.root / 'account.json')['sections']['shop_boats']['values']['items']['Luxury Yacht']['owned'])
        self.assertIn('refresh_boats', c.state.refresh_due)
        # An incomplete subsequent read must not erase the dispatch reply's evidence.
        c.account = apply_read(c.account, 'shop_boats', {'values': {'items': {
            'Rowboat': {'owned': True, 'price': None}}}, 'unread': ['missing rows']}, 'later')
        self.assertEqual(check(self.boat, c.account['sections'], {}), 'already owned Luxury Yacht')
        sent = sum(intent.kind == 'act' for _, intent in self.sim.sent)
        c.queue_step('Luxury Yacht', [self.boat.to_dict()])
        self.advance_until(lambda: not c.queue)
        self.assertEqual(sum(intent.kind == 'act' for _, intent in self.sim.sent), sent)
        self.assertIsNone(check(Action('buy', 'Fishing Boat', shop='boats'), c.account['sections'], {}))

    def test_unverified_uncorrelated_or_unsent_reply_never_establishes_ownership(self):
        c = self.controller
        observation = Observation('fake', 'fake', self.clock.utc().isoformat(), CONFIG.context_id,
                                  'action', 'sim', REPLY, VERIFIED, correlation='never-sent')
        c._observe(observation)
        self.assertNotIn('Luxury Yacht', c.account['sections']['shop_boats']['values']['items'])
        c.queue_step('Luxury Yacht', [self.boat.to_dict()])
        self.advance_until(lambda: c.state.pending is not None and c.state.pending.kind == 'act')
        pending = c.state.pending
        for wrong in (replace(observation, correlation=pending.action_id, identity_status=MISMATCH),
                      replace(observation, correlation=pending.action_id, context_id='wrong'),
                      replace(observation, correlation=pending.action_id, observed_at='2000-01-01')):
            c._observe(wrong)
            self.assertNotIn('Luxury Yacht', c.account['sections']['shop_boats']['values']['items'])
        c._observe(replace(observation, correlation=pending.action_id, observed_at=self.clock.utc().isoformat()))
        self.assertTrue(c.account['sections']['shop_boats']['values']['items']['Luxury Yacht']['owned'])
    def test_owned_rods_survive_incomplete_later_read(self):
        from src.account_state import mark_owned
        state = mark_owned({'sections': {}}, Action('buy', 'Lava Rod', shop='rods'))
        later = apply_read(state, 'rod', {'values': {'current': 'Steel Rod', 'owned': ['Steel Rod']},
                                        'unread': []}, 'later')
        self.assertEqual(set(later['sections']['rod']['values']['owned']), {'Lava Rod', 'Steel Rod'})
        self.assertEqual(check(Action('buy', 'Lava Rod', shop='rods'), later['sections'], {}),
                         'already owned Lava Rod')
        self.assertEqual(reads_after(Action('buy', 'Puffer', shop='pets')), ('refresh_profile', 'refresh_pets'))
    def test_generic_owned_item_skips_only_the_satisfied_item(self):
        from src.account_state import mark_owned
        special = Action('buy', 'Artifact Specialist', shop='special')
        state = mark_owned({'sections': {}}, special)
        self.assertEqual(check(special, state['sections'], {}), 'already owned Artifact Specialist')
        self.assertIsNone(check(Action('buy', 'Other Item', shop='special'), state['sections'], {}))
        upgrade = Action('buy', 'Salesman', shop='upgrades', level=8)
        state = mark_owned(state, upgrade)
        self.assertNotIn('level', state['sections']['shop_upgrades']['values']['items']['Salesman'])
        self.assertIsNone(check(upgrade, state['sections'], {}))
        self.assertIsNone(check(replace(upgrade, level=9), state['sections'], {}))

class AlreadyOwnedLevelEvidenceTests(unittest.TestCase):
    def test_generic_ownership_reply_does_not_prove_requested_upgrade_level(self):
        action = Action('buy', 'Salesman', shop='upgrades', level=8)
        self.assertEqual(outcome(action, 'You already own that item.')[0], 'unclear')
        from src.account_state import mark_owned
        state = {'sections': {'shop_upgrades': {'values': {'items': {'Salesman': {'level': 7}}}}}}
        remembered = mark_owned(state, action)
        self.assertEqual(remembered['sections']['shop_upgrades']['values']['items']['Salesman']['level'], 7)
        self.assertIsNone(check(action, remembered['sections'], {}))
