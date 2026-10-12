from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest

from src.automation_controller import AutomationController
from src.automation_journal import AutomationJournal
from src.automation_models import AutomationConfig, ContextStatus
from src.automation_transport import SimulationTransport
from src.fishing_sessions import load_sessions
from src.review_queue import list_items
from tests.plan_fixtures import CHARMS, account_state, synced

# /charms is left out of most tests so their command sequences stay short; its own tests add it.
CONFIG = AutomationConfig(account='sim', username='sim0001', context_id='sim:dm',
                          capabilities=('refresh_profile', 'refresh_buffs', 'fish'))
CHARMS_CONFIG = ('refresh_profile', 'refresh_buffs', 'refresh_charms', 'fish')
# Every read: what a live run does.
from src.account_reads import ALL_READS  # noqa: E402
READS_CONFIG = ('refresh_profile', 'refresh_buffs', 'refresh_charms', *ALL_READS, 'fish')
ACCOUNT_ORDER = ['refresh_upgrades', 'refresh_upgrades_page2', 'refresh_shop_rods', 'refresh_shop_rods_page2',
                 'refresh_rod', 'refresh_bait', 'refresh_biome', 'refresh_boats', 'refresh_pets',
                 'refresh_prestige_shop', 'refresh_league', 'refresh_league_page2', 'refresh_boosts']
STEP = 0.1


class Crash(BaseException):
    """Stands in for the process dying: nothing in the controller catches it."""


class FaultyJournal(AutomationJournal):
    """Raises `error` instead of writing the `nth` event of type `on`."""

    def __init__(self, root, on=None, nth=1, error=Crash):
        super().__init__(root)
        self.on, self.nth, self.error, self.seen = on, nth, error, 0

    def append(self, event):
        if event['type'] == self.on:
            self.seen += 1
            if self.seen == self.nth:
                raise self.error(f'fault writing {self.on}')
        super().append(event)


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def utc(self):
        return datetime(2026, 10, 6, 12, tzinfo=timezone.utc) + timedelta(seconds=self.now)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.journal_root, self.fishing, self.review = root / 'journal', root / 'fishing', root / 'review'
        self.clock = Clock()
        self.sim = SimulationTransport(self.clock.monotonic, self.clock.utc, cooldown=3.5, latency=0.4)
        self.checks = []
        original = self.sim.send

        def checked_send(intent):
            # At every hand-over: the intent is already journaled, and no other command is unresolved.
            events = AutomationJournal(self.journal_root).recover()['actions']
            pending = self.controller.state.pending  # reserved for this very command, and nothing else
            self.checks.append((events.get(intent.action_id, {}).get('status'),
                                None if pending == intent else pending or 'not reserved', self.clock.now))
            return original(intent)

        self.sim.send = checked_send

    def tearDown(self):
        self.temporary.cleanup()

    def make(self, journal=None, config=CONFIG, state_path=None, plan_spawn=lambda work: None):
        # Plans are skipped unless a test asks: a plan's background thread would slow the simulated run.
        self.controller = AutomationController(self.sim, config, journal or AutomationJournal(self.journal_root),
                                               self.fishing, self.review, monotonic=self.clock.monotonic,
                                               utc=self.clock.utc, state_path=state_path, plan_spawn=plan_spawn)
        return self.controller

    def run_for(self, seconds, controller=None):
        controller = controller or self.controller
        end = self.clock.now + seconds
        while self.clock.now < end and controller.status()['running']:
            controller.tick()
            self.clock.now = round(self.clock.now + STEP, 6)

    def saved_trips(self):
        return sum(session['tally']['trips'] for session in load_sessions('sim', self.fishing)[0])

    def fish_sends(self):
        return [time for time, intent in self.sim.sent if intent.kind == 'fish']

    # --- the normal cycle ---

    def test_bootstraps_then_fishes_with_intent_journaled_before_each_send(self):
        self.make(config=replace(CONFIG, max_trips=3)).start()
        self.run_for(30)
        self.assertEqual([intent.kind for _, intent in self.sim.sent],
                         ['refresh_profile', 'refresh_buffs', 'fish', 'fish', 'fish'])
        self.assertTrue(all(status == 'dispatching' for status, _, _ in self.checks), self.checks)
        self.assertTrue(all(pending is None for _, pending, _ in self.checks), 'never two unresolved commands')
        self.assertEqual(self.saved_trips(), 3)
        status = self.controller.status()
        self.assertEqual((status['running'], status['trips']), (False, 3))
        self.assertIn('trip limit', status['reason'])

    def test_fishing_waits_for_the_verified_cooldown_alone_without_jitter(self):
        self.make(config=replace(CONFIG, max_trips=10, jitter_bands=())).start()
        self.run_for(60)
        sends = self.fish_sends()
        gaps = [later - earlier for earlier, later in zip(sends, sends[1:])]
        self.assertTrue(all(gap >= 3.5 - 1e-9 for gap in gaps), gaps)
        self.assertLess(max(gaps), 3.5 + 0.2, 'and not much longer')

    def test_cooldown_reply_is_not_a_trip_and_resets_the_wait(self):
        self.make(config=replace(CONFIG, max_trips=4, cooldown_margin=-2.0)).start()  # deliberately too early
        self.run_for(60)
        self.assertEqual(self.saved_trips(), 4)
        self.assertEqual(self.sim.totals['trips'], 4)
        results = [info.get('result') for info in AutomationJournal(self.journal_root).recover()['actions'].values()]
        self.assertIn('cooldown', results)

    def test_another_players_reply_and_duplicates_are_not_counted(self):
        self.sim.script = {2: 'other_player_first', 3: 'duplicate'}
        self.make(config=replace(CONFIG, max_trips=4)).start()
        self.run_for(40)
        self.assertEqual(self.saved_trips(), 4)
        self.assertEqual(self.sim.totals['trips'], 4)

    def kinds(self):
        return [intent.kind for _, intent in self.sim.sent]

    def test_unreadable_profile_is_set_aside_and_retried_every_10_trips(self):
        # The same misread twice gives up at once; fishing goes on and /profile is tried again 10 trips later.
        self.sim.script = {0: 'unreadable_fixed', 1: 'unreadable_fixed'}
        self.make(config=replace(CONFIG, max_trips=12)).start()
        self.run_for(120)
        self.assertEqual(self.kinds(), ['refresh_profile', 'refresh_profile', 'refresh_buffs'] + ['fish'] * 10
                         + ['refresh_profile', 'fish', 'fish'])
        self.assertEqual(self.controller.state.profile['balance'], 1000, 'the retry read it')
        self.assertTrue(all(not item['blocks_actions'] for item in list_items(self.review)[0]))

    def test_unreadable_buffs_fishes_on_an_assumed_cooldown_until_read(self):
        self.sim.script = {1: 'unreadable_fixed', 2: 'unreadable_fixed', 13: 'unreadable_fixed'}
        self.make(config=replace(CONFIG, max_trips=21)).start()
        self.run_for(240)
        kinds = self.kinds()
        self.assertEqual(kinds[:3], ['refresh_profile', 'refresh_buffs', 'refresh_buffs'])
        self.assertEqual(kinds[3:13], ['fish'] * 10)
        self.assertEqual(kinds[13], 'refresh_buffs', 'tried again after 10 trips; this read fails too')
        self.assertEqual(kinds[24], 'refresh_buffs', 'and again 10 trips later, which reads')
        self.assertEqual(self.controller.state.cooldown_seconds, 3.5)
        self.assertNotIn(self.controller.status()['phase'], ('NEEDS_REVIEW', 'FAILED'))

    def test_startup_retries_only_failed_command_then_fishes(self):
        self.sim.script = {1: 'unreadable', 2: 'unreadable'}
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(30)
        self.assertEqual([intent.kind for _, intent in self.sim.sent],
                         ['refresh_profile', 'refresh_buffs', 'refresh_buffs', 'refresh_buffs', 'fish'])
        self.assertEqual(self.saved_trips(), 1)
        self.assertFalse(self.controller.status()['unresolved'])
        self.assertTrue(all(not item['blocks_actions'] for item in list_items(self.review)[0]))
        buffs_times = [at for at, intent in self.sim.sent if intent.kind == 'refresh_buffs']
        self.assertTrue(all(b - a >= 2.4 - STEP for a, b in zip(buffs_times, buffs_times[1:])))

    def test_new_launch_fetches_fresh_data_and_unbounded_run_stops_on_request(self):
        self.make().start()
        self.run_for(12)
        self.controller.stop()
        previous = len(self.sim.sent)
        self.make().start()
        self.run_for(12)
        self.assertEqual([intent.kind for _, intent in self.sim.sent[previous:]][:2],
                         ['refresh_profile', 'refresh_buffs'])
        self.assertTrue(self.controller.running)
        self.controller.stop()
        sends = len(self.sim.sent)
        self.run_for(20)
        self.assertEqual(len(self.sim.sent), sends)

    def test_missing_startup_reply_is_asked_again_after_the_timeout(self):
        self.sim.script = {0: 'missing'}
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(40)
        kinds = [intent.kind for _, intent in self.sim.sent]
        self.assertEqual(kinds[:3], ['refresh_profile', 'refresh_profile', 'refresh_buffs'])
        self.assertGreaterEqual(self.sim.sent[1][0] - self.sim.sent[0][0], 20)
        self.assertEqual(self.saved_trips(), 1)

    def test_discord_sending_wait_is_bounded_and_never_resends(self):
        self.sim.script = {0: 'missing'}
        self.sim.sending_seen = True
        self.make().start()
        self.run_for(30)
        self.assertEqual(self.controller.status()['phase'], 'WAITING_DELIVERY')
        self.assertEqual(len(self.sim.sent), 1)
        self.run_for(35)
        self.assertGreaterEqual(self.sim.sent[1][0] - self.sim.sent[0][0], 60, 'waits the full delivery bound')

    def test_timeout_preserves_transport_failure_reason(self):
        self.sim.script = {0: 'missing'}
        self.make().start()
        self.run_for(1)
        self.sim.context = ContextStatus(False, 'sim:dm', 'Reply placement uncertain: overlap lost')
        self.run_for(40)
        self.assertIn('overlap lost', self.controller.status()['reason'])
        self.assertEqual(len(self.sim.sent), 1)

    def test_profile_retry_recovers_before_buffs_and_stop_cancels_retry(self):
        self.sim.script = {0: 'unreadable'}
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(20)
        self.assertEqual([intent.kind for _, intent in self.sim.sent],
                         ['refresh_profile', 'refresh_profile', 'refresh_buffs', 'fish'])
        self.assertEqual(self.saved_trips(), 1)

    def test_stop_during_startup_retry_sends_nothing_more(self):
        self.sim.script = {0: 'unreadable'}
        self.make().start()
        self.run_for(1)
        self.assertEqual(self.controller.status()['phase'], 'RETRYING_STARTUP')
        self.assertEqual(len(self.sim.sent), 1)
        self.controller.stop()
        self.run_for(20)
        self.assertEqual(len(self.sim.sent), 1)
        self.assertFalse(AutomationJournal(self.journal_root).recover()['unresolved'])

    def test_startup_ignores_optional_ocr_and_fishes_with_required_values(self):
        original = self.sim._play
        def play(intent, now):
            if intent.kind == 'refresh_profile':
                return ('Inventory of sim\nBalance: $50.\nP4 Level 30, xp to next leveL\nFish Invent«y\na777 Salmon', 'profile')
            if intent.kind == 'refresh_buffs':
                return ("sim0001's current multipliers (including bait):\nFish quality: ??\nFishing cooldown: 3.5s.", 'buffs')
            return original(intent, now)
        self.sim._play = play
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(20)
        self.assertEqual([intent.kind for _, intent in self.sim.sent], ['refresh_profile', 'refresh_buffs', 'fish'])
        self.assertEqual(self.saved_trips(), 1)

    def test_wrong_context_pauses_without_sending(self):
        self.sim.context = ContextStatus(False, 'sim:dm', 'Discord is minimized')
        self.make().start()
        self.run_for(10)
        self.assertEqual(self.sim.sent, [])
        self.assertEqual(self.controller.status()['phase'], 'PAUSED')
        self.sim.context = ContextStatus(True, 'other:channel')
        self.run_for(10)
        self.assertEqual(self.sim.sent, [], 'a different channel is not the configured one')

    # --- failures: never a blind resend ---

    def test_pause_holds_sending_and_resume_carries_on(self):
        self.make(config=replace(CONFIG, max_trips=4)).start()
        self.run_for(10)
        self.controller.pause()
        sent = len(self.sim.sent)
        self.run_for(30)
        status = self.controller.status()
        self.assertTrue(status['paused'])
        self.assertEqual(status['phase'], 'PAUSED_BY_YOU')
        self.assertLessEqual(len(self.sim.sent), sent, 'nothing more is sent while paused')
        self.assertEqual(self.controller.status()['pending'], None, 'a reply already on its way is still read')
        self.controller.resume()
        self.run_for(30)
        self.assertEqual(self.saved_trips(), 4)
        self.assertFalse(self.controller.status()['paused'])

    def test_resume_asks_the_transport_to_click_the_message_box_again(self):
        self.make().start()
        self.run_for(5)
        refocused = []
        self.sim.refocus = lambda: refocused.append(True)
        self.controller.pause()
        self.controller.resume()
        self.assertEqual(refocused, [True])

    # --- live stats and periodic resync ---

    def test_profile_and_buffs_are_resynced_every_n_trips(self):
        self.make(config=replace(CONFIG, max_trips=5, resync_trips=3)).start()
        self.run_for(60)
        self.assertEqual([intent.kind for _, intent in self.sim.sent],
                         ['refresh_profile', 'refresh_buffs', 'fish', 'fish', 'fish',
                          'refresh_profile', 'refresh_buffs', 'fish', 'fish'])
        self.assertEqual(self.controller.status()['dashboard']['trips_since_sync'], 2)

    def test_unreadable_resync_never_stops_fishing(self):
        self.sim.script = {5: 'unreadable_fixed', 6: 'unreadable_fixed'}
        self.make(config=replace(CONFIG, max_trips=5, resync_trips=3)).start()
        self.run_for(60)
        self.assertEqual(self.saved_trips(), 5)
        self.assertTrue(all(not item['blocks_actions'] for item in list_items(self.review)[0]))

    def test_a_read_missing_one_field_is_read_again_until_complete(self):
        # Buffs read without the sell price: tried again every 2 trips (here) until it reads; then every 100.
        self.sim.script = {1: 'partial', 4: 'partial'}
        self.make(config=replace(CONFIG, max_trips=6, resync_trips=100, retry_unread_trips=2)).start()
        self.run_for(90)
        self.assertEqual(self.kinds(), ['refresh_profile', 'refresh_buffs', 'fish', 'fish', 'refresh_buffs',
                                        'fish', 'fish', 'refresh_buffs', 'fish', 'fish'])
        dashboard = self.controller.status()['dashboard']
        self.assertEqual(dashboard['unread'], {})
        self.assertEqual(dashboard['buffs']['sell_price']['value'], 1.0)

    def test_charms_are_read_at_startup_and_with_each_resync(self):
        self.make(config=replace(CONFIG, max_trips=4, resync_trips=3, capabilities=CHARMS_CONFIG)).start()
        self.run_for(60)
        self.assertEqual(self.kinds(), ['refresh_profile', 'refresh_buffs', 'refresh_charms', 'fish', 'fish', 'fish',
                                        'refresh_profile', 'refresh_buffs', 'refresh_charms', 'fish'])
        charms = self.controller.status()['dashboard']['charms']
        self.assertEqual((charms['total'], charms['goal'], charms['types']), (11, 840, 6))

    def test_a_charms_reply_missing_a_line_is_read_again(self):
        self.sim.script = {2: 'partial'}
        self.make(config=replace(CONFIG, max_trips=3, resync_trips=100, retry_unread_trips=2,
                                 capabilities=CHARMS_CONFIG)).start()
        self.run_for(60)
        self.assertEqual(self.kinds(), ['refresh_profile', 'refresh_buffs', 'refresh_charms', 'fish', 'fish',
                                        'refresh_charms', 'fish'])
        self.assertEqual(self.controller.status()['dashboard']['unread'], {})

    # --- reading the account ---

    def test_the_account_is_read_at_startup_and_each_resync_and_saved(self):
        from src.account_state import load_state
        path = self.journal_root.parent / 'account_state.json'
        self.make(config=replace(CONFIG, max_trips=4, resync_trips=3, capabilities=READS_CONFIG),
                  state_path=path).start()
        self.run_for(150)
        startup = ['refresh_profile', 'refresh_buffs', 'refresh_charms'] + ACCOUNT_ORDER
        self.assertEqual(self.kinds(), startup + ['fish'] * 3 + startup + ['fish'])
        sections = load_state(path)['sections']
        self.assertEqual(len(sections), 10)
        self.assertEqual({kind: section['unread'] for kind, section in sections.items() if section['unread']}, {})
        self.assertEqual(sections['shop_upgrades']['values']['items']['Better Chests']['price'], 100000)
        self.assertEqual(self.controller.status()['account']['sections']['pet']['values']['current'], 'Dolphin')

    def test_status_carries_the_model_view_and_saves_what_it_measures(self):
        path = self.journal_root.parent / 'account_state.json'
        self.make(config=replace(CONFIG, max_trips=3, capabilities=READS_CONFIG), state_path=path).start()
        self.run_for(150)
        model = self.controller.status()['model']
        self.assertIsNone(model['error'], model['error'])
        self.assertEqual(model['setup']['rod'], 'Steel Rod')
        self.assertEqual([row['name'] for row in model['buffs']][0], 'sell_price')
        fish = {row['name']: row for row in model['trip']}['fish']
        self.assertEqual(fish['status'], 'measuring 3/50 trips')
        self.assertTrue((self.journal_root.parent / 'model_check.json').exists())
        # A model failure never breaks status.
        self.controller.model_data = None
        self.assertIn('not loaded', self.controller.status()['model']['error'])

    def test_a_model_failure_on_a_trip_never_stops_fishing_and_clears_once_a_trip_is_used(self):
        path = self.journal_root.parent / 'account_state.json'
        self.make(config=replace(CONFIG, max_trips=6, capabilities=READS_CONFIG), state_path=path).start()
        observe, seen = self.controller.model.observe, []

        def broken(*args):
            raise RuntimeError('boom')
        self.controller.model.observe = broken
        while self.controller.status()['trips'] < 3 and self.controller.status()['running']:
            self.run_for(STEP)
        self.assertEqual(self.controller.model_problem, 'The model could not use a trip: boom')
        self.assertIn('The model could not use a trip: boom', self.controller.status()['model']['estimates'])

        def watching(tally, setup, predicted, *rest):
            seen.append(predicted)
            return observe(tally, setup, predicted, *rest)
        self.controller.model.observe = watching
        self.run_for(150)
        self.assertEqual(self.controller.status()['trips'], 6, 'fishing went on')
        self.assertIsNone(self.controller.model_problem)
        self.assertNotIn('The model could not use a trip: boom', self.controller.status()['model']['estimates'])
        # The /buffs comparison's corrections reach the prediction: the simulation shows fish catch 1.00x, 3.5 s.
        self.assertAlmostEqual(seen[-1]['buffs']['fish_catch'].value, 1.0, places=9)
        self.assertAlmostEqual(seen[-1]['buffs']['fishing_cooldown'].value, 3.5, places=9)

    def test_a_corrupt_model_file_never_stops_the_run(self):
        path = self.journal_root.parent / 'account_state.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        (path.parent / 'model_check.json').write_text(json.dumps({'window': {'key': 'k', 'trips': 7},
                                                                  'mix': 'oops'}), encoding='utf-8')
        self.make(config=replace(CONFIG, max_trips=3, capabilities=READS_CONFIG), state_path=path).start()
        self.run_for(150)
        self.assertEqual(self.controller.status()['trips'], 3)
        model = self.controller.status()['model']
        self.assertIsNone(model['error'], model['error'])
        self.assertTrue(any('model_check.json' in item for item in model['estimates']), model['estimates'])
        self.assertEqual({row['name']: row for row in model['trip']}['fish']['status'], 'measuring 3/50 trips')

    def test_each_trip_is_predicted_with_its_own_boost_and_the_buffs_sell_price(self):
        path = self.journal_root.parent / 'account_state.json'
        self.make(config=replace(CONFIG, max_trips=2, capabilities=READS_CONFIG), state_path=path).start()
        self.run_for(150)
        seen = []
        observe = self.controller.model.observe

        def watching(tally, setup, predicted, data, sell, shown=None):
            seen.append((setup.global_booster, predicted['fish'].value, sell, shown))
            return observe(tally, setup, predicted, data, sell, shown)
        self.controller.model.observe = watching
        synced = self.controller._synced

        def percent_sell_price():  # "/buffs" showing "Sell price: +91%"
            found = synced()
            found['buffs'] = dict(found['buffs'], sell_price={'value': 91.0, 'unit': 'percent'})
            return found
        self.controller._synced = percent_sell_price

        def logged(boosted, fish='Cod'):
            return {'trips': 1, 'catches': {fish: 3}, 'fishing_xp': 30, 'chest_xp': 0,
                    'boosted_trips': int(boosted),
                    'trip_details': [{'catches': {fish: 3}, 'xp': 30, 'chest_xp': 0, 'boosted': boosted,
                                      'events': []}]}
        self.controller._model_observe(logged(True))
        self.assertIn('global booster', self.controller.status()['model']['setup']['boosts'])
        self.controller._model_observe(logged(False))
        self.assertNotIn('global booster', self.controller.status()['model']['setup']['boosts'])
        (boosted, boosted_fish, sell, shown), (plain, plain_fish, _, _) = seen
        self.assertEqual((boosted, plain), (True, False), 'from the trip itself, not the /boosts read')
        self.assertAlmostEqual(boosted_fish, plain_fish * 1.5, places=9)
        self.assertAlmostEqual(sell, 1.91, places=9, msg='+91% is 1.91x')
        self.assertAlmostEqual(shown['sell_price'], 1.91, places=9)
        self.assertAlmostEqual(shown['fishing_cooldown'], 3.5, places=9)
        # Only a trip actually used clears a problem.
        self.controller.model_problem = 'The model could not use a trip: boom'
        self.controller._model_observe(logged(False, fish='Banana'))
        self.assertEqual(self.controller.model_problem, 'The model could not use a trip: boom')
        self.controller._model_observe(logged(False))
        self.assertIsNone(self.controller.model_problem)

    def test_page_two_that_cannot_be_clicked_never_stops_fishing(self):
        # Send 3 (counting from 0) is /shop upgrades page 2, after profile, buffs and page 1.
        self.sim.script = {3: 'no_button'}
        reads = ('refresh_profile', 'refresh_buffs', 'refresh_upgrades', 'refresh_upgrades_page2', 'fish')
        self.make(config=replace(CONFIG, max_trips=4, resync_trips=100, retry_unread_trips=2,
                                 capabilities=reads)).start()
        self.run_for(80)
        self.assertEqual(self.kinds(), ['refresh_profile', 'refresh_buffs', 'refresh_upgrades',
                                        'refresh_upgrades_page2', 'fish', 'fish', 'refresh_upgrades',
                                        'refresh_upgrades_page2', 'fish', 'fish'])
        self.assertEqual(self.saved_trips(), 4)
        self.assertEqual(self.controller.status()['dashboard']['unread'], {})

    def test_a_garbled_league_line_is_read_again(self):
        self.sim.script = {3: 'partial'}   # /shop league page 2
        reads = ('refresh_profile', 'refresh_buffs', 'refresh_league', 'refresh_league_page2', 'fish')
        self.make(config=replace(CONFIG, max_trips=3, resync_trips=100, retry_unread_trips=2,
                                 capabilities=reads)).start()
        self.run_for(80)
        self.assertEqual(self.kinds(), ['refresh_profile', 'refresh_buffs', 'refresh_league', 'refresh_league_page2',
                                        'fish', 'fish', 'refresh_league', 'refresh_league_page2', 'fish'])
        self.assertEqual(self.controller.status()['account']['sections']['shop_league']['unread'], [])

    def test_complete_reads_wait_for_the_regular_resync(self):
        self.make(config=replace(CONFIG, max_trips=25, resync_trips=100)).start()
        self.run_for(200)
        self.assertEqual(self.kinds(), ['refresh_profile', 'refresh_buffs'] + ['fish'] * 25)

    def test_profile_with_unread_xp_is_read_again_after_a_few_trips(self):
        self.sim.script = {0: 'xp_unread'}
        self.make(config=replace(CONFIG, max_trips=4, resync_trips=100, retry_unread_trips=2)).start()
        self.run_for(60)
        self.assertEqual([intent.kind for _, intent in self.sim.sent],
                         ['refresh_profile', 'refresh_buffs', 'fish', 'fish', 'refresh_profile', 'fish', 'fish'])
        profile = self.controller.status()['dashboard']['profile']
        self.assertEqual(profile['level'], 10)
        self.assertIsNotNone(profile['xp_current'], 'the second read showed the XP progress')
        self.assertEqual(self.saved_trips(), 4)

    def test_captcha_pauses_and_alerts_until_resumed(self):
        self.sim.script = {3: 'captcha'}
        alerts = []
        self.make(config=replace(CONFIG, max_trips=5))
        self.controller.alert = alerts.append
        self.controller.start()
        self.run_for(30)
        status = self.controller.status()
        self.assertTrue(status['paused'])
        self.assertEqual(status['phase'], 'CAPTCHA')
        self.assertIn('/captcha', status['reason'])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(len(self.sim.sent), 4, 'nothing more is sent after the captcha')
        self.assertEqual(self.saved_trips(), 1, 'the captcha reply is not a trip')
        self.controller.resume()
        self.run_for(30)
        self.assertEqual(self.saved_trips(), 5)

    # --- Discord losing focus ---

    def test_an_interrupted_command_is_retried_once_discord_is_back(self):
        self.sim.script = {2: 'interrupted'}       # the first /fish
        self.make(config=replace(CONFIG, max_trips=3)).start()
        self.run_for(10)
        status = self.controller.status()
        self.assertEqual(status['phase'], 'WAITING_FOCUS')
        self.assertIn('interrupted', status['reason'])
        self.assertEqual(self.kinds(), ['refresh_profile', 'refresh_buffs', 'fish'], 'nothing more while unfocused')
        self.sim.focus()
        self.run_for(30)
        self.assertEqual(self.kinds(), ['refresh_profile', 'refresh_buffs'] + ['fish'] * 4)
        self.assertEqual(self.saved_trips(), 3)
        self.assertEqual(self.controller.status()['unresolved'], [])
        self.assertEqual(list_items(self.review)[0], [], 'an interruption is not an unknown outcome')

    def test_time_without_focus_does_not_run_out_the_reply_wait(self):
        self.make(config=replace(CONFIG, max_trips=2)).start()
        while not any(intent.kind == 'fish' for _, intent in self.sim.sent):
            self.run_for(STEP)
        self.sim.unfocus()                          # the reply is on its way; Discord is in the background
        self.run_for(45)                            # longer than the 20 s reply timeout
        status = self.controller.status()
        self.assertEqual(status['phase'], 'WAITING_FOCUS')
        self.assertIn('fish was sent', status['reason'])
        self.sim.focus()
        self.run_for(30)
        self.assertEqual(self.saved_trips(), 2)
        self.assertEqual(list_items(self.review)[0], [], 'its reply was read, not given up on')

    def test_a_captcha_pause_ends_once_a_newer_reply_appears(self):
        self.sim.script = {3: 'captcha'}
        self.make(config=replace(CONFIG, max_trips=4))
        self.controller.alert = lambda message: None
        self.controller.start()
        self.run_for(20)
        self.assertEqual(self.controller.status()['phase'], 'CAPTCHA', 'still paused without an answer')
        self.sim.solve_captcha()
        self.run_for(30)
        self.assertFalse(self.controller.status()['paused'])
        self.assertEqual(self.saved_trips(), 4)
        events = [event['type'] for event in AutomationJournal(self.journal_root).events()[0]]
        self.assertIn('captcha_solved', events)

    def test_each_fish_waits_a_random_extra_in_steps(self):
        self.make(config=replace(CONFIG, max_trips=12)).start()
        self.run_for(120)
        fish = self.fish_sends()
        gaps = [later - earlier - 3.5 - CONFIG.cooldown_margin for earlier, later in zip(fish, fish[1:])]
        self.assertTrue(all(-STEP <= gap <= 0.75 + 2 * STEP for gap in gaps), gaps)
        self.assertGreater(len({round(gap, 1) for gap in gaps}), 1, 'the extra wait varies')

    def test_extra_wait_bands_and_their_shares(self):
        import random
        self.make()
        self.controller.rng = random.Random(7)
        for _ in range(20000):
            self.controller._pick_jitter()
        extras = self.controller.jitters
        def on_grid(extra, step):
            return abs(extra / step - round(extra / step)) < 1e-9
        low = [e for e in extras if e <= 0.25 and on_grid(e, 0.025)]
        middle = [e for e in extras if 0.25 < e <= 0.5 and on_grid(e, 0.05)]
        high = [e for e in extras if 0.5 < e <= 0.75 and on_grid(e, 0.05)]
        self.assertEqual(len(low) + len(middle) + len(high), len(extras), 'every extra is in a band, on its steps')
        # 75% / 17.5% / 7.5%; a band's shared endpoint (0.25, 0.5) counts toward the lower band here.
        self.assertAlmostEqual(len(low) / len(extras), 0.75 + 0.175 / 6, delta=0.015)
        self.assertAlmostEqual(len(high) / len(extras), 0.075 * 5 / 6, delta=0.01)
        self.assertEqual({round(e, 2) for e in high}, {0.55, 0.6, 0.65, 0.7, 0.75})

    def test_dashboard_carries_synced_stats_forward(self):
        self.make(config=replace(CONFIG, max_trips=2)).start()
        self.run_for(30)
        dashboard = self.controller.status()['dashboard']
        self.assertEqual(dashboard['gained']['xp'], self.sim.totals['xp'])
        self.assertEqual(dashboard['gained']['trips'], 2)
        self.assertEqual(dashboard['profile']['account_name'], 'sim')
        self.assertEqual(dashboard['buffs']['fishing_cooldown']['value'], 3.5)
        self.assertEqual(sum(dashboard['changes']['fish'].values()), sum(self.sim.totals['catches'].values()))
        self.assertIn('xp', dashboard['per_hour'])
        self.assertEqual(set(dashboard['finds']), {'run', 'all'})
        self.assertEqual(dashboard['finds']['run']['chests'], {})

    # --- unknown outcomes: noted, never stop the run ---

    def assert_moves_on(self, outcome):
        self.sim.script = {2: outcome}
        self.make().start()
        self.run_for(60)
        status = self.controller.status()
        self.assertNotIn(status['phase'], ('NEEDS_REVIEW', 'FAILED'))
        sends = self.fish_sends()
        self.assertGreater(len(sends), 2, 'fishing continues')
        self.assertGreaterEqual(sends[1] - sends[0], 3.5, 'the next fish waits at least a cooldown')
        self.assertTrue(all(not item['blocks_actions'] for item in list_items(self.review)[0]))
        return status

    def test_missing_reply_is_noted_and_fishing_continues(self):
        self.assert_moves_on('missing')
        self.assertTrue(list_items(self.review)[0])

    def test_unreadable_result_is_noted_and_fishing_continues(self):
        self.assert_moves_on('unreadable')
        self.assertIn('fishing', [item['kind'] for item in list_items(self.review)[0]])

    def test_reply_without_correlation_is_noted_and_fishing_continues(self):
        self.assert_moves_on('uncorrelated')

    def test_send_failure_is_noted_and_fishing_continues(self):
        self.assert_moves_on('send_error')

    def test_readable_trip_with_a_garbled_extra_line_still_counts(self):
        # Observed 2026-10-09: one garbled level-up line made a whole trip (catch, XP, level-up) uncounted.
        self.sim.script = {3: 'noisy'}
        self.make(config=replace(CONFIG, max_trips=3)).start()
        self.run_for(30)
        self.assertEqual(self.saved_trips(), 3)
        self.assertEqual(self.controller.status()['dashboard']['gained']['xp'], self.sim.totals['xp'])
        [note] = list_items(self.review)[0]
        self.assertFalse(note['blocks_actions'])
        self.assertIn('the rest was counted', ' '.join(note['reasons']))

    def test_three_unknown_outcomes_in_a_row_stop_the_run(self):
        self.sim.script = {2: 'missing', 3: 'missing', 4: 'missing'}
        self.make().start()
        self.run_for(200)
        status = self.controller.status()
        self.assertEqual(status['phase'], 'FAILED')
        self.assertIn('3 commands in a row', status['reason'])
        self.assertEqual(len(self.fish_sends()), 3)

    def test_profile_is_read_again_after_three_uncounted_trips(self):
        # Live: 10 of 34 trips had no confirmed reply; their XP was never counted and the level shown fell a level
        # behind until the next /profile, 100 trips later.
        self.sim.script = {3: 'missing', 5: 'missing', 7: 'missing'}
        self.make(config=replace(CONFIG, max_trips=8)).start()
        self.run_for(200)
        kinds = [intent.kind for _, intent in self.sim.sent]
        self.assertEqual(kinds[:9], ['refresh_profile', 'refresh_buffs'] + ['fish'] * 6 + ['refresh_profile'])
        self.assertEqual(self.controller.status()['dashboard']['uncounted_since_sync'], 0)

    def test_noted_outcome_is_not_carried_into_the_next_run(self):
        self.sim.script = {2: 'missing'}
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(60)
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(20)
        self.assertFalse([item for item in list_items(self.review)[0] if 'earlier run' in ' '.join(item['reasons'])])

    def test_crash_before_send_is_forgotten_safely(self):
        self.make(journal=FaultyJournal(self.journal_root, on='dispatching', nth=3)).start()
        with self.assertRaises(Crash):
            self.run_for(10)
        self.assertEqual(len(self.fish_sends()), 0, 'the fish command was journaled but never handed over')
        restarted = self.make(config=replace(CONFIG, max_trips=2))
        restarted.start()
        self.assertEqual(restarted.status()['unresolved'], [])
        self.run_for(20)
        self.assertEqual((self.saved_trips(), self.sim.totals['trips']), (2, 2))

    def test_crash_after_send_before_receipt_reconciles_from_the_visible_reply(self):
        self.make(journal=FaultyJournal(self.journal_root, on='dispatched', nth=3)).start()
        with self.assertRaises(Crash):
            self.run_for(10)
        self.assertEqual(len(self.fish_sends()), 1)
        restarted = self.make(config=replace(CONFIG, max_trips=3))
        restarted.start()
        self.assertNotEqual(restarted.status()['phase'], 'NEEDS_REVIEW', 'an earlier run never blocks this one')
        self.run_for(30)
        self.assertEqual(self.sim.totals['trips'], 4, 'the reconciled trip plus three new ones')
        self.assertEqual(self.saved_trips(), 4, 'the crashed trip is counted once, from its reply')

    def test_crash_after_send_with_no_reply_is_noted_and_does_not_block(self):
        self.sim.script = {2: 'missing'}
        self.make(journal=FaultyJournal(self.journal_root, on='dispatched', nth=3)).start()
        with self.assertRaises(Crash):
            self.run_for(10)
        restarted = self.make(config=replace(CONFIG, max_trips=1))
        restarted.start()
        self.run_for(30)
        self.assertEqual(self.saved_trips(), 1)
        notes = [item for item in list_items(self.review)[0] if 'earlier run' in ' '.join(item['reasons'])]
        self.assertEqual(len(notes), 1)
        self.assertFalse(notes[0]['blocks_actions'])

    def test_crash_after_receipt_before_acknowledgment(self):
        self.make(journal=FaultyJournal(self.journal_root, on='acknowledged', nth=3)).start()
        with self.assertRaises(Crash):
            self.run_for(10)
        restarted = self.make(config=replace(CONFIG, max_trips=1))
        restarted.start()
        self.run_for(20)
        self.assertEqual((self.sim.totals['trips'], self.saved_trips()), (2, 2))

    def test_crash_before_projection_is_projected_once_on_restart(self):
        self.make(journal=FaultyJournal(self.journal_root, on='projected', nth=1)).start()
        with self.assertRaises(Crash):
            self.run_for(10)
        self.assertEqual(self.saved_trips(), 1, 'saved, but the projection was not journaled')
        restarted = self.make(config=replace(CONFIG, max_trips=1))
        restarted.start()
        self.run_for(20)
        self.assertEqual((self.sim.totals['trips'], self.saved_trips()), (2, 2), 'not saved twice')

    def test_journal_write_failure_before_send_sends_nothing(self):
        self.make(journal=FaultyJournal(self.journal_root, on='intent', nth=1, error=OSError)).start()
        self.run_for(10)
        self.assertEqual(self.sim.sent, [])
        self.assertEqual(self.controller.status()['phase'], 'FAILED')

    def test_journal_write_failure_after_send_is_uncertain(self):
        self.make(journal=FaultyJournal(self.journal_root, on='dispatched', nth=3, error=OSError)).start()
        self.run_for(30)
        self.assertEqual(len(self.fish_sends()), 1)
        self.assertIn(self.controller.status()['phase'], ('FAILED', 'NEEDS_REVIEW'))

    def test_refusal_before_input_pauses_then_resumes_in_the_same_run(self):
        # Observed 2026-10-08: Discord left the foreground just before a /fish, which ended a 36-trip run.
        from src.automation_models import NotDispatched
        original, refused = self.sim.send, []
        def refuse_while_away(intent):
            if self.clock.now < 10:
                refused.append(intent.kind)
                raise NotDispatched('Discord must remain in the foreground.')
            return original(intent)
        self.sim.send = refuse_while_away
        self.make(config=replace(CONFIG, max_trips=1)).start()
        self.run_for(5)
        status = self.controller.status()
        self.assertEqual(status['phase'], 'WAITING_FOCUS')
        self.assertIn('active window', status['reason'])
        self.assertLessEqual(len(refused), 5, 'retried calmly, not every tick')
        self.run_for(20)
        self.assertEqual(self.saved_trips(), 1, 'the same run carries on once Discord is back')
        self.assertFalse(list_items(self.review)[0], 'nothing was sent, so there is nothing to review')

    def test_stop_during_a_blocked_send_prevents_later_dispatch(self):
        entered, release = threading.Event(), threading.Event()
        inner = self.sim.send

        def blocking(intent):
            if intent.kind == 'fish':
                entered.set()
                release.wait(5)
            return inner(intent)

        self.sim.send = blocking
        controller = self.make()
        controller.start()
        worker = threading.Thread(target=lambda: self.run_for(30, controller))
        worker.start()
        self.assertTrue(entered.wait(5))
        controller.stop()
        release.set()
        worker.join(5)
        self.assertEqual(len(self.fish_sends()), 1, 'the command in flight was recorded; nothing after it')
        recovered = AutomationJournal(self.journal_root).recover()
        fish = [info for info in recovered['actions'].values() if info.get('intent', {}).get('kind') == 'fish']
        self.assertEqual(len(fish), 1)
        self.assertNotEqual(fish[0]['status'], 'intended', 'its hand-over is in the journal')

    def test_concurrent_ticks_send_one_command(self):
        entered, release = threading.Event(), threading.Event()
        inner = self.sim.send

        def blocking(intent):
            entered.set()
            release.wait(5)
            return inner(intent)

        self.sim.send = blocking
        controller = self.make()
        controller.start()
        first = threading.Thread(target=controller.tick)
        first.start()
        self.assertTrue(entered.wait(5))
        second = threading.Thread(target=controller.tick)  # e.g. the page's thread and the worker together
        second.start()
        second.join(5)
        release.set()
        first.join(5)
        self.assertEqual([intent.kind for _, intent in self.sim.sent], ['refresh_profile'])
        self.assertEqual(controller.status()['pending'], self.sim.sent[0][1].action_id)

    def test_damaged_journal_is_noted_and_does_not_block(self):
        self.make(journal=FaultyJournal(self.journal_root, on='dispatched', nth=3)).start()
        with self.assertRaises(Crash):
            self.run_for(10)
        dispatching = [path for path in self.journal_root.rglob('*.json')
                       if '"dispatching"' in path.read_text(encoding='utf-8')][-1]
        dispatching.write_text('{"schema_version": 1, "type": "dispa', encoding='utf-8')  # torn write
        sends = len(self.sim.sent)
        restarted = self.make()
        restarted.start()
        self.run_for(30)
        status = restarted.status()
        self.assertGreater(len(self.sim.sent), sends)
        self.assertEqual(status['unresolved'], [])
        self.assertTrue(any('could not be read' in ' '.join(item['reasons']) for item in list_items(self.review)[0]))

    # --- acceptance ---

    def test_one_hundred_simulated_trips(self):
        self.make(config=replace(CONFIG, max_trips=100)).start()
        self.run_for(1000)
        recovered = AutomationJournal(self.journal_root).recover()
        trips = [info for info in recovered['actions'].values() if info.get('result') == 'trip']
        self.assertEqual(len(trips), 100, '100 acknowledgments')
        sessions = load_sessions('sim', self.fishing)[0]
        catches = {}
        for session in sessions:
            for fish, count in session['tally']['catches'].items():
                catches[fish] = catches.get(fish, 0) + count
        self.assertEqual(self.saved_trips(), 100)
        self.assertEqual(catches, self.sim.totals['catches'], 'the logbook tally is exact')
        self.assertEqual(len(self.fish_sends()), 100, 'no extra sends')
        self.assertTrue(all(pending is None for _, pending, _ in self.checks), 'no overlapping commands')
        gaps = [b - a for a, b in zip(self.fish_sends(), self.fish_sends()[1:])]
        self.assertGreaterEqual(min(gaps), 3.5 - 1e-9, 'cooldown spacing')

    def test_one_hundred_trips_across_a_crash_and_restart(self):
        self.make(journal=FaultyJournal(self.journal_root, on='dispatched', nth=52),
                  config=replace(CONFIG, max_trips=100)).start()
        with self.assertRaises(Crash):
            self.run_for(1000)
        done = self.saved_trips()
        restarted = self.make(config=replace(CONFIG, max_trips=100 - done - 1))  # one trip awaits reconciliation
        restarted.start()
        self.run_for(1000)
        self.assertEqual(self.saved_trips(), 100)
        self.assertEqual(self.sim.totals['trips'], 100)
        self.assertEqual(len(self.fish_sends()), 100)

    def test_status_has_a_plan_section(self):
        started = []
        self.make(config=replace(CONFIG, capabilities=READS_CONFIG), plan_spawn=started.append).start()
        self.run_for(30)
        plan = self.controller.status()['plan']
        self.assertEqual(set(plan), {'plan', 'made_at', 'updating', 'error'})
        self.assertEqual(len(started), 1)            # one plan at a time: the rest wait for it
        self.assertEqual((plan['plan'], plan['made_at'], plan['updating']), (None, None, True))
        started[0]()                                 # the thread runs: a plan, not an error
        plan = self.controller.status()['plan']
        self.assertIsNone(plan['error'])
        self.assertIsNotNone(plan['plan'])
        self.assertIsNotNone(plan['made_at'])

    def test_a_plan_is_worked_out_from_the_runs_reads(self):
        path = Path(self.temporary.name) / 'account_state.json'
        self.make(config=replace(CONFIG, max_trips=3, capabilities=READS_CONFIG), state_path=path,
                  plan_spawn=lambda work: work()).start()
        self.run_for(60)
        self.clock.now += 30                         # past the planner's spacing: a request left waiting starts
        view = self.controller.status()['plan']
        self.assertFalse(view['updating'])
        self.assertIsNone(view['error'])
        self.assertEqual(set(view['plan']), {'error', 'next', 'steps', 'prestige', 'assumptions', 'cut_short'})
        self.assertIsNotNone(view['made_at'])

    def test_a_plan_that_cannot_be_requested_never_stops_the_run(self):
        self.make(config=replace(CONFIG, max_trips=3, capabilities=READS_CONFIG))
        self.controller._plan_inputs = lambda dashboard=None: 1 / 0
        self.controller.start()
        self.run_for(60)
        self.assertEqual(self.sim.totals['trips'], 3)
        self.assertIn('could not be requested', self.controller.status()['plan']['error'])

    def test_plan_runner_keeps_the_newest_request_and_never_raises(self):
        from src.automation_controller import PlanRunner
        started = []
        runner = PlanRunner(lambda inputs: {'steps': [inputs]}, spawn=lambda work: started.append(work))
        runner.request(1)
        runner.request(2)
        self.assertEqual(len(started), 1)
        self.assertTrue(runner.view()['updating'])
        started[0]()
        self.assertEqual(runner.view()['plan'], {'steps': [2]})
        self.assertFalse(runner.view()['updating'])
        failing = PlanRunner(lambda inputs: 1 / 0, spawn=lambda work: work())
        failing.request(1)
        self.assertIn('division', failing.view()['error'])

    def test_a_later_planner_error_keeps_the_last_good_plan(self):
        from src.automation_controller import PlanRunner
        now = [0.0]
        results = [{'error': None, 'steps': ['good']}, ZeroDivisionError('boom'), {'error': 'not planned: x'}]

        def compute(inputs):
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        runner = PlanRunner(compute, spawn=lambda work: work(), clock=lambda: now[0])
        runner.request(1)
        made = runner.view()['made_at']
        for message in ('The planner failed: boom', 'not planned: x'):
            now[0] += 30
            runner.request(2)
            view = runner.view()
            self.assertEqual(view['plan'], {'error': None, 'steps': ['good']})
            self.assertEqual(view['error'], message)
            self.assertEqual(view['made_at'], made)

    def test_a_failure_replaces_a_plan_that_could_not_be_made(self):
        # Live: the run's first plan (before /profile) said the level was not read; the next one failed on a misread
        # bait, and the tab kept showing the stale level message.
        from src.automation_controller import PlanRunner
        now = [0.0]
        results = [{'error': 'not planned: The level is not read yet (/profile).'}, ZeroDivisionError('boom')]

        def compute(inputs):
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        runner = PlanRunner(compute, spawn=lambda work: work(), clock=lambda: now[0])
        runner.request(1)
        now[0] += 30
        runner.request(2)
        self.assertIsNone(runner.view()['plan'])
        self.assertEqual(runner.view()['error'], 'The planner failed: boom')

    def test_plan_requests_are_spaced_30_seconds_apart(self):
        from src.automation_controller import PlanRunner
        now, computed = [0.0], []
        runner = PlanRunner(lambda inputs: computed.append(inputs) or {'error': None}, spawn=lambda work: work(),
                            clock=lambda: now[0])
        runner.request('first')
        self.assertEqual(computed, ['first'])        # the first at once
        for moment, inputs in ((5, 'a'), (12, 'b'), (29, 'c')):
            now[0] = moment
            runner.request(inputs)
            runner.poll()
        self.assertEqual(computed, ['first'])        # inside the window: waiting, never blocking the caller
        self.assertTrue(runner.view()['updating'])
        now[0] = 30
        runner.poll()
        self.assertEqual(computed, ['first', 'c'])   # folded into one plan with the newest inputs
        runner.poll()
        now[0] = 45
        runner.request('d')
        self.assertEqual(computed, ['first', 'c'])
        now[0] = 60
        runner.poll()
        self.assertEqual(computed, ['first', 'c', 'd'])
        self.assertFalse(runner.view()['updating'])

    def test_the_run_starts_a_waiting_plan_when_the_window_ends(self):
        computed = []
        self.make(config=replace(CONFIG, capabilities=READS_CONFIG), plan_spawn=lambda work: work())
        self.controller._compute_plan = lambda inputs: computed.append(self.clock.now) or {'error': None}
        self.controller.planner.compute = self.controller._compute_plan
        self.controller.start()
        self.run_for(70)
        self.assertGreaterEqual(len(computed), 2)
        self.assertTrue(all(b - a >= 30 for a, b in zip(computed, computed[1:])), computed)

    def test_trip_spacing_from_the_logbook(self):
        from src.automation_controller import trip_spacing
        stamp = lambda seconds: {'captured_at': f'2026-10-07T13:{seconds // 60:02d}:{seconds % 60:02d}.000000Z',
                                 'tally': {'trips': 1}}
        moments = [i * 7 for i in range(30)] + [30 * 7 + 600 + i * 9 for i in range(3)]   # a break, then 9 s gaps
        sessions = [stamp(m) for m in moments]
        self.assertEqual(trip_spacing(sessions), 7)
        self.assertIsNone(trip_spacing(sessions[:10]))   # too few trips to tell

    def test_a_plan_before_any_rate_uses_the_logbook_spacing(self):
        self.make(config=replace(CONFIG, capabilities=READS_CONFIG), plan_spawn=lambda work: work())
        self.controller.start()
        self.run_for(20)
        inputs = self.controller._plan_inputs(self.controller._dashboard())
        inputs['trips_per_hour'] = None
        inputs['logbook_spacing'] = 9.0
        inputs['account'], inputs['synced'], inputs['charms'] = account_state(), synced(), CHARMS
        plan = self.controller._compute_plan(inputs)
        self.assertTrue(any("logbook's recent trip spacing (median 9.0 s)" in note for note in plan['assumptions']),
                        plan['assumptions'])

    def test_plan_inputs_carry_the_charms_found_since_the_read(self):
        self.make(config=replace(CONFIG, capabilities=READS_CONFIG))
        self.controller.charm_tallies = [{'finds': {'Haste Charm': 2}, 'chest_contents': {}, 'charm_levels': {}}]
        self.assertEqual(self.controller._plan_inputs()['charms_since'], {'Haste Charm': 2})


if __name__ == '__main__':
    unittest.main()
