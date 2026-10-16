from datetime import timezone
import json
from pathlib import Path
import tempfile
import unittest

from src.progress import empty_index, index_trips


def trip(at, xp=100, money=0, finds=None, level_ups=(), name=None):
    return {'session_id': name or at, 'captured_at': at,
            'tally': {'trips': 1, 'fishing_xp': xp, 'chest_money': money, 'finds': finds or {},
                      'chest_contents': {}, 'level_ups': list(level_ups)}}


class IndexTests(unittest.TestCase):
    def test_trips_add_up_by_day_with_fishing_time(self):
        records = [trip('2026-10-14T10:00:00Z', 100, finds={'rare chest': 1, 'Haste Charm': 1}),
                   trip('2026-10-14T10:00:05Z', 200, 50, level_ups=[129]),
                   trip('2026-10-14T11:00:00Z', 50),               # a 55-minute gap: not fishing time
                   trip('2026-10-15T09:00:00Z', 10, finds={'pet  Puffer': 1})]
        index = index_trips(empty_index(), records, tz=timezone.utc)
        day = index['days']['2026-10-14']
        self.assertEqual((day['trips'], day['xp'], day['chest_money'], day['chests'], day['charms'], day['level'],
                          day['seconds']), (3, 350, 50, 1, 1, 129, 5))
        self.assertEqual(index['days']['2026-10-15']['pets'], 1)
        self.assertEqual(len(index['files']), 4)
        self.assertEqual(day['chest_kinds'], {'rare chest': 1})

    def test_chests_by_kind_add_onto_an_older_index_without_them(self):
        older = {'version': 1, 'files': ['old'], 'last_at': '2026-10-14T09:00:00+00:00',
                 'days': {'2026-10-14': {'trips': 1, 'xp': 0, 'chest_money': 0, 'chests': 2, 'charms': 0,
                                         'special_fish': 0, 'pets': 0, 'level': None, 'seconds': 0}}}
        index = index_trips(older, [trip('2026-10-14T10:00:00Z', finds={'rare chest': 2, 'epic chest': 1})],
                            tz=timezone.utc)
        self.assertEqual(index['days']['2026-10-14']['chest_kinds'], {'rare chest': 2, 'epic chest': 1})
        self.assertEqual(index['days']['2026-10-14']['chests'], 5)
        self.assertNotIn('chest_kinds', older['days']['2026-10-14'])         # the index passed in is not changed

    def test_only_new_files_are_added(self):
        once = index_trips(empty_index(), [trip('2026-10-14T10:00:00Z')], tz=timezone.utc)
        twice = index_trips(once, [trip('2026-10-14T10:00:00Z'), trip('2026-10-14T10:00:04Z')], tz=timezone.utc)
        self.assertEqual(twice['days']['2026-10-14']['trips'], 2)
        self.assertEqual(twice['days']['2026-10-14']['seconds'], 4)   # the gap from the last indexed trip


class StoreTests(unittest.TestCase):
    def test_build_index_reads_only_new_trip_files_and_skips_bad_ones(self):
        from src.progress_store import build_index
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            trips = folder / 'fishing' / 'acct'
            trips.mkdir(parents=True)
            (trips / 'a.json').write_text(json.dumps(trip('2026-10-14T10:00:00Z', name='a')), encoding='utf-8')
            (trips / 'bad.json').write_text('{', encoding='utf-8')
            index, bad = build_index(folder, tz=timezone.utc)
            self.assertEqual((index['days']['2026-10-14']['trips'], bad), (1, 1))
            (trips / 'b.json').write_text(json.dumps(trip('2026-10-14T10:00:03Z', name='b')), encoding='utf-8')
            index, _ = build_index(folder, tz=timezone.utc)
            self.assertEqual(index['days']['2026-10-14']['trips'], 2)
            self.assertTrue((folder / 'progress_index.json').exists())

    def test_build_index_survives_odd_trip_files(self):
        from src.progress_store import build_index
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            trips = folder / 'fishing' / 'acct'
            trips.mkdir(parents=True)
            good = trip('2026-10-14T10:00:00Z', name='good')
            naive = trip('2026-10-14T10:00:02', name='naive')          # no zone: read as UTC, still indexed
            wrong_type = trip('2026-10-14T10:00:03Z', name='wrongtype')
            wrong_type['tally']['fishing_xp'] = 'lots'
            bad_levels = trip('2026-10-14T10:00:04Z', name='badlevels')
            bad_levels['tally']['level_ups'] = ['x', 3]
            for record in (good, naive, wrong_type, bad_levels):
                (trips / (record['session_id'] + '.json')).write_text(json.dumps(record), encoding='utf-8')
            (trips / 'list.json').write_text('[1]', encoding='utf-8')
            index, bad = build_index(folder, tz=timezone.utc)
            self.assertEqual((index['days']['2026-10-14']['trips'], bad), (2, 3))

    def test_damaged_saved_index_is_rebuilt_from_the_trips(self):
        from src.progress_store import build_index
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            trips = folder / 'fishing' / 'acct'
            trips.mkdir(parents=True)
            (trips / 'a.json').write_text(json.dumps(trip('2026-10-14T10:00:00Z', name='a')), encoding='utf-8')
            for damaged in ('{"version": 1}', '{"version": 1, "files": {}, "days": [], "last_at": 5}'):
                (folder / 'progress_index.json').write_text(damaged, encoding='utf-8')
                index, bad = build_index(folder, tz=timezone.utc)
                self.assertEqual((index['days']['2026-10-14']['trips'], bad), (1, 0))

    def test_history_lines_append_and_bad_lines_are_skipped(self):
        from src.progress_store import append_history, read_history
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'profile_history.jsonl'
            append_history(path, {'at': '2026-10-14T10:00:00+00:00', 'kind': 'profile', 'level': 128})
            with path.open('a', encoding='utf-8') as file:
                file.write('not json\n')
            append_history(path, {'at': '2026-10-14T11:00:00+00:00', 'kind': 'charms', 'found': 40})
            entries, bad = read_history(path)
            self.assertEqual(([e['kind'] for e in entries], bad), (['profile', 'charms'], 1))
            self.assertEqual(read_history(Path(root) / 'none.jsonl'), ([], 0))


def event(kind, at, run='r1', action=None, **fields):
    return {'type': kind, 'at': at, 'run_id': run, 'action_id': action, **fields}


class HistoryTests(unittest.TestCase):
    def test_runs_newest_first_with_their_trips(self):
        from src.progress import run_history
        tally = {'trips': 1, 'fishing_xp': 500, 'finds': {'rare chest': 1, 'Haste Charm': 1}, 'chest_contents': {}}
        events = [event('run_started', '2026-10-14T10:00:00Z'),
                  event('acknowledged', '2026-10-14T10:00:10Z', action='r1-00001-fish', result='trip', tally=tally),
                  event('acknowledged', '2026-10-14T10:00:20Z', action='r1-00002-refresh_profile', result='refreshed'),
                  event('run_stopped', '2026-10-14T10:30:00Z', reason='Stopped by you.'),
                  event('run_started', '2026-10-14T11:00:00Z', run='r2'),
                  event('acknowledged', '2026-10-14T11:00:10Z', run='r2', action='r2-00001-fish', result='trip',
                        tally=tally)]
        runs = run_history(events)
        self.assertEqual([r['run_id'] for r in runs], ['r2', 'r1'])
        self.assertEqual((runs[1]['trips'], runs[1]['xp'], runs[1]['chests'], runs[1]['charms'],
                          runs[1]['seconds'], runs[1]['stopped']), (1, 500, 1, 1, 1800, 'Stopped by you.'))
        self.assertEqual(runs[0]['stopped'], 'Ended without a stop record.')
        self.assertEqual(run_history(events, running_id='r2')[0]['stopped'], 'Still running.')

    def test_a_run_without_a_stop_record_still_writing_is_running(self):
        # A run started from the command line: no running id, but its journal was written moments ago.
        from datetime import datetime
        from src.progress import run_history
        events = [event('run_started', '2026-10-14T10:00:00Z'),
                  event('run_stopped', '2026-10-14T10:30:00Z', reason='Stopped.'),
                  event('run_started', '2026-10-14T11:00:00Z', run='r2'),
                  event('dispatched', '2026-10-14T11:20:00Z', run='r2', action='r2-00009-fish'),
                  event('run_started', '2026-10-14T09:00:00Z', run='r0')]      # an older run, never stopped
        soon = datetime(2026, 10, 14, 11, 21, tzinfo=timezone.utc)
        self.assertEqual([r['stopped'] for r in run_history(events, now=soon)],
                         ['Still running.', 'Stopped.', 'Ended without a stop record.'])
        later = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)                  # nothing written for 40 min
        self.assertEqual(run_history(events, now=later)[0]['stopped'], 'Ended without a stop record.')

    def test_actions_with_their_results_notes_and_costs(self):
        from src.progress import action_history
        buy = {'act': 'buy', 'name': 'Salesman', 'level': 8, 'shop': 'upgrades', 'price': 25000}
        events = [event('intent', '2026-10-14T10:00:00Z', action='a1', intent={
                      'kind': 'act', 'preconditions': ['Buy Salesman 8 (Salesman 8)'], 'arguments': [buy]}),
                  event('acknowledged', '2026-10-14T10:00:03Z', action='a1', result='done',
                        note='bought Salesman level 8'),
                  event('intent', '2026-10-14T10:05:00Z', action='a2', intent={
                      'kind': 'act', 'preconditions': ['Switch to Ocean (Switch: biome Ocean)']}),
                  event('resolved', '2026-10-14T10:05:20Z', action='a2', outcome='unknown',
                        reason='No confirmed reply within 20 s.'),
                  event('intent', '2026-10-14T10:06:00Z', action='a3', intent={
                      'kind': 'act', 'preconditions': ['Buy Super Crates 3 (Super Crates 3)'],
                      'arguments': [{'act': 'buy', 'name': 'Super Crates', 'shop': 'league', 'price': 150}]}),
                  event('acknowledged', '2026-10-14T10:06:03Z', action='a3', result='failed'),
                  event('intent', '2026-10-14T10:07:00Z', action='f1', intent={'kind': 'fish', 'preconditions': []})]
        actions = action_history(events)
        self.assertEqual([(a['label'], a['result'], a['note'], a['cost']) for a in actions], [
            ('Buy Super Crates 3 (Super Crates 3)', 'failed', '', ''),               # failed: nothing was paid
            ('Switch to Ocean (Switch: biome Ocean)', 'unknown', 'No confirmed reply within 20 s.', ''),
            ('Buy Salesman 8 (Salesman 8)', 'done', 'bought Salesman level 8', '$25,000 (planned)')])

    def test_the_cost_is_what_the_game_charged_when_its_reply_says(self):
        from src.progress import action_history

        def bought(name, arguments, result, note):
            return [event('intent', '2026-10-14T10:00:00Z', action=name, intent={
                        'kind': 'act', 'preconditions': [name], 'arguments': [arguments]}),
                    event('acknowledged', '2026-10-14T10:00:03Z', action=name, result=result, note=note)]
        events = (bought('bait', {'act': 'buy', 'name': 'Wise Bait', 'amount': 500, 'shop': 'bait', 'price': 9625},
                         'done', 'bought 500 wise bait for $17,500')
                  + bought('rod', {'act': 'buy', 'name': 'Infinity Rod', 'shop': 'rods', 'price': 1e9},
                           'failed', "can't afford it")
                  + bought('owned', {'act': 'buy', 'name': 'Dinghy', 'shop': 'boats', 'price': 5000},
                           'done', 'already owned Dinghy')
                  + bought('ovens', {'act': 'buy', 'name': 'Fish Ovens', 'level': 3, 'shop': 'special', 'price': 60,
                                     'currency': 'Lava Fish'}, 'done', 'bought Fish Ovens level 3')
                  + bought('older', {'act': 'buy', 'name': 'Fish Ovens', 'level': 2, 'shop': 'special', 'price': 40},
                           'done', 'bought Fish Ovens level 2'))
        self.assertEqual({a['label']: a['cost'] for a in action_history(events)},
                         {'bait': '$17,500', 'rod': '', 'owned': '', 'ovens': '60 Lava Fish (planned)',
                          'older': '40 exotic fish (planned)'})

    def test_odd_events_are_skipped_not_raised(self):
        from src.progress import action_history, run_history
        events = [event('intent', '2026-10-14T10:00:00Z', action='a1'),
                  event('intent', '2026-10-14T10:00:01Z', action='a2', intent='nope'),
                  event('acknowledged', '2026-10-14T10:00:02Z', action='r1-1-fish', result='trip', tally=None),
                  event('acknowledged', '2026-10-14T10:00:03Z', action='r1-2-fish', result='trip', tally='x'),
                  event('intent', '2026-10-14T10:00:04Z', action='a3', intent={'kind': 'act', 'arguments': ['x']}),
                  {'type': 'acknowledged', 'run_id': 'r1'},
                  {'type': 'intent', 'at': 'garbage', 'run_id': 'r1', 'action_id': 'a4',
                   'intent': {'kind': 'act'}},
                  {'run_id': 'r1'}]
        runs = run_history(events)
        self.assertEqual((runs[0]['trips'], runs[0]['xp']), (0, 0))
        self.assertEqual([a['label'] for a in action_history(events)], ['an action'])  # a3 only: a4's time is unreadable


class ViewTests(unittest.TestCase):
    def test_chart_series_by_range(self):
        from datetime import datetime
        from src.progress import chart_series
        index = index_trips(empty_index(), [trip('2026-10-01T10:00:00Z', 3600, level_ups=[100]),
                                            trip('2026-10-01T10:05:00Z', 3600),
                                            trip('2026-10-14T10:00:00Z', 100, level_ups=[128])],
                            tz=timezone.utc)
        history = [{'at': '2026-10-14T10:00:00+00:00', 'kind': 'profile', 'level': 128, 'balance': 1000,
                    'fish_value': 500},
                   {'at': '2026-10-14T11:00:00+00:00', 'kind': 'charms', 'found': 40, 'goal': 840}]
        now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
        every = chart_series(index, history, None, now)
        self.assertEqual([v for _, v in every['level']], [100, 128])   # Oct 14: the profile read, not trip level-ups
        self.assertEqual(every['xp_per_hour'], [('2026-10-01T12:00:00+00:00', 86400.0)])   # 7,200 XP in 300 s
        self.assertEqual(every['net_worth'], [('2026-10-14T10:00:00+00:00', 1500)])
        self.assertEqual(every['charms'], [('2026-10-14T11:00:00+00:00', 40)])
        week = chart_series(index, history, 7, now)
        self.assertEqual([v for _, v in week['trips']], [1])                  # Oct 1 is outside the week

    def test_level_never_mixes_trip_level_ups_into_profile_reads(self):
        # Trip level-ups by day only before the first /profile read: a day's highest level-up drawn at noon must
        # not sit beside that day's profile reads (224 at noon, then 166 read at 14:32).
        from datetime import datetime
        from src.progress import chart_series
        index = index_trips(empty_index(), [trip('2026-10-12T10:00:00Z', level_ups=[150]),
                                            trip('2026-10-13T10:00:00Z', level_ups=[224]),
                                            trip('2026-10-14T10:00:00Z', level_ups=[230])], tz=timezone.utc)
        history = [{'at': '2026-10-13T14:32:00+00:00', 'kind': 'profile', 'level': 166, 'balance': 1},
                   {'at': '2026-10-14T09:00:00+00:00', 'kind': 'profile', 'level': 170, 'balance': 1}]
        level = chart_series(index, history, None, datetime(2026, 10, 15, tzinfo=timezone.utc))['level']
        self.assertEqual(level, [('2026-10-12T12:00:00+00:00', 150), ('2026-10-13T14:32:00+00:00', 166),
                                 ('2026-10-14T09:00:00+00:00', 170)])

    def test_net_worth_carries_the_last_fish_value_forward(self):
        from datetime import datetime
        from src.progress import chart_series
        history = [{'at': '2026-10-14T09:00:00+00:00', 'kind': 'profile', 'balance': 50},          # none known yet
                   {'at': '2026-10-14T10:00:00+00:00', 'kind': 'profile', 'balance': 100, 'fish_value': 900},
                   {'at': '2026-10-14T11:00:00+00:00', 'kind': 'profile', 'balance': 200, 'fish_value': None},
                   {'at': '2026-10-14T12:00:00+00:00', 'kind': 'profile', 'balance': 300, 'fish_value': 1000}]
        worth = chart_series(empty_index(), history, None, datetime(2026, 10, 15, tzinfo=timezone.utc))['net_worth']
        self.assertEqual([v for _, v in worth], [1000, 1100, 1300])

    def test_countdown_from_a_plan(self):
        from src.progress import countdown
        plan = {'phase_now': 'charm_grind_1', 'prestige': {'reached': True, 'seconds': 90000, 'missing': []},
                'steps': [{'phase': 'charm_grind_1', 'phase_label': '1st charm grind', 'seconds': 0},
                          {'phase': 'xp_grind_2', 'phase_label': '2nd XP grind', 'seconds': 3600},
                          {'phase': 'xp_grind_2', 'phase_label': '2nd XP grind', 'seconds': 4000},
                          {'phase': 'xp_grind_3', 'phase_label': 'Final grind', 'seconds': 50000}]}
        result = countdown(plan, {'level': 128, 'prestige': 4, 'balance': 1e6, 'fish_value': 5e5},
                           {'found': 40, 'goal': 840})
        self.assertEqual((result['reached'], result['seconds']), (True, 90000))
        self.assertEqual([b['label'] for b in result['bars']], ['Level', 'Money', 'Charms'])
        self.assertEqual(result['bars'][0], {'label': 'Level', 'now': 128, 'goal': 500})   # P4 -> 100 + 100 * 4
        self.assertEqual(result['bars'][2], {'label': 'Charms', 'now': 40, 'goal': 840})
        self.assertEqual(result['milestones'], [{'label': '2nd XP grind', 'seconds': 3600},
                                                {'label': 'Final grind', 'seconds': 50000},
                                                {'label': 'Prestige', 'seconds': 90000}])
        self.assertEqual(countdown(None, {}, None)['missing'], ['No plan yet.'])

    def test_countdown_bars_without_a_plan(self):
        # The page opened with no run: the bars come from the last profile and charms read, the ETAs need a plan.
        from src.progress import countdown
        result = countdown(None, {'level': 128, 'prestige': 4, 'balance': 1e6, 'fish_value': 5e5},
                           {'found': 40, 'goal': 840})
        self.assertEqual((result['missing'], result['milestones']), (['No plan yet.'], []))
        self.assertEqual([(b['label'], b['now'], b['goal']) for b in result['bars']],
                         [('Level', 128, 500), ('Money', 1.5e6, result['bars'][1]['goal']), ('Charms', 40, 840)])

    def test_the_whole_view(self):
        from datetime import datetime
        from src.progress import progress_view
        view = progress_view(empty_index(), [], [], None, {}, None, datetime(2026, 10, 14, tzinfo=timezone.utc),
                             notes=['1 trip record could not be read.'])
        self.assertEqual(set(view), {'countdown', 'charts', 'runs', 'actions', 'notes'})
        self.assertEqual(set(view['charts']), {'7', '30', 'all'})
        self.assertEqual(view['notes'], ['1 trip record could not be read.'])

    def test_odd_inputs_are_skipped_not_raised(self):
        from datetime import datetime
        from src.progress import chart_series, countdown, progress_view
        now = datetime(2026, 10, 14, tzinfo=timezone.utc)
        history = [{'kind': 'profile'}, {'at': 'garbage', 'kind': 'profile', 'level': 5, 'balance': 1},
                   {'at': '2026-10-14T01:00:00Z', 'kind': 'charms'}, 'nope', {'kind': 'charms', 'found': 3},
                   {'at': '2026-10-14T01:00:00Z', 'kind': 'profile', 'level': 7, 'balance': 10}]
        series = chart_series({'days': {'2026-10-14': {}}}, history, 7, now)
        self.assertEqual(series['level'], [('2026-10-14T01:00:00Z', 7)])
        self.assertEqual(series['net_worth'], [])            # no fish value read yet: not a net worth
        self.assertEqual(series['charms'], [])
        result = countdown({'steps': [{'phase': 'x'}, 'nope'], 'prestige': None}, {'prestige': 99}, None)
        self.assertEqual((result['reached'], result['milestones'], result['bars']), (False, [], []))
        progress_view(empty_index(), history, [{'type': 'x'}], {'steps': None}, None, None, now)

    def test_view_from_disk_uses_the_last_profile_and_charms_and_notes_bad_files(self):
        from datetime import datetime
        from src.progress_store import HISTORY, append_history, view_from_disk
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / 'fishing').mkdir()
            (folder / 'fishing' / 'bad.json').write_text('{', encoding='utf-8')
            append_history(folder / HISTORY, {'at': '2026-10-14T01:00:00Z', 'kind': 'profile', 'level': 3,
                                              'balance': 10, 'fish_value': 5, 'prestige': 4})
            append_history(folder / HISTORY, {'at': '2026-10-14T02:00:00Z', 'kind': 'profile', 'level': 7,
                                              'balance': 20, 'fish_value': None, 'prestige': 4})
            append_history(folder / HISTORY, {'at': '2026-10-14T02:00:00Z', 'kind': 'charms', 'found': 4,
                                              'goal': 840})
            view = view_from_disk(folder, datetime(2026, 10, 14, 3, tzinfo=timezone.utc))
        self.assertEqual([v for _, v in view['charts']['all']['level']], [3, 7])
        self.assertEqual(view['runs'], [])
        self.assertEqual([(b['label'], b['now']) for b in view['countdown']['bars']],
                         [('Level', 7), ('Money', 25), ('Charms', 4)])
        self.assertEqual(view['notes'], ['1 trip records could not be read.'])


class JournalCacheTests(unittest.TestCase):
    def test_only_a_run_folder_that_gained_a_file_is_read_again(self):
        from unittest import mock
        from src import progress_store
        from src.automation_journal import AutomationJournal
        from src.progress import journal_digest
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'journal'
            journal = AutomationJournal(root)
            journal.append({'run_id': 'run-a', 'type': 'run_started'})
            journal.append({'run_id': 'run-a', 'type': 'run_stopped'})
            journal.append({'run_id': 'run-b', 'type': 'run_started'})
            (root / 'run-b' / '000009-bad.json').write_text('{', encoding='utf-8')     # skipped, as events() does
            cache = {}
            first = progress_store.read_journal(root, cache)
            self.assertEqual(first, journal_digest(journal.events()[0]))
            journal.append({'run_id': 'run-b', 'type': 'run_stopped', 'reason': 'Done.'})
            read = []
            original = progress_store._read_event
            with mock.patch.object(progress_store, '_read_event',
                                   side_effect=lambda path: read.append(path.parent.name) or original(path)):
                second = progress_store.read_journal(root, cache)
            self.assertEqual(set(read), {'run-b'})
            self.assertEqual(second, journal_digest(journal.events()[0]))
            self.assertEqual(sorted(r['stopped'] for r in second['runs']), ['Done.', 'Stopped.'])


class DiskProgressTests(unittest.TestCase):
    """player_web: with no view from a run, the live account's view from its files, at most once a minute."""

    def handler(self, root, settings):
        from src.player_web import Handler
        import threading
        cls = type('DiskHandler', (Handler,), {'automation_live_root': root, 'disk_progress': None,
                                                'disk_progress_lock': threading.Lock(),
                                                'live_settings': lambda self: settings})
        return object.__new__(cls)

    def test_overlapping_polls_start_one_build(self):
        import threading
        from unittest import mock
        handler = self.handler(Path('.'), {'account': 'Me'})
        inside, release, calls = threading.Event(), threading.Event(), []

        def slow_build(folder):
            calls.append(folder)
            inside.set()
            release.wait(5)
            return {'runs': ['built']}

        status = {'running': False, 'progress': {'view': None, 'made_at': None, 'error': None}}
        results = []
        with mock.patch('src.player_web.view_from_disk', side_effect=slow_build):
            first = threading.Thread(target=lambda: results.append(handler.with_disk_progress(status)))
            first.start()
            self.assertTrue(inside.wait(5))
            meanwhile = handler.with_disk_progress(status)       # while the first build runs
            release.set()
            first.join(5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(meanwhile['progress'], {'view': None, 'made_at': None, 'error': None})
        self.assertEqual(results[0]['progress']['view'], {'runs': ['built']})

    def test_a_status_without_a_view_gets_the_disk_view_once_a_minute(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as temporary:
            handler = self.handler(Path(temporary), {'account': 'Me'})
            empty = {'running': False, 'progress': {'view': None, 'made_at': None, 'error': None}}
            with mock.patch('src.player_web.view_from_disk', return_value={'runs': []}) as built:
                first = handler.with_disk_progress(empty)
                handler.with_disk_progress(empty)
            self.assertEqual(first['progress'], {'view': {'runs': []}, 'made_at': None, 'error': None})
            self.assertEqual(built.call_count, 1)
            self.assertIsNone(empty['progress']['view'])            # the run's own status is not changed
            own = {'progress': {'view': {'mine': 1}}}
            self.assertIs(handler.with_disk_progress(own), own)      # a run's own view is kept
            running = {'running': True, 'progress': {'view': None, 'made_at': None, 'error': None}}
            self.assertIs(handler.with_disk_progress(running), running)   # its first view is on its way

    def test_no_run_at_all_still_gets_the_disk_view(self):
        # No run since the server started: the page still shows the live account's progress.
        from unittest import mock
        with tempfile.TemporaryDirectory() as temporary:
            handler = self.handler(Path(temporary), {'account': 'Me'})
            with mock.patch('src.player_web.view_from_disk', return_value={'runs': []}):
                status = handler.with_disk_progress(None)
        self.assertEqual(status, {'progress': {'view': {'runs': []}, 'made_at': None, 'error': None}})
        self.assertIsNone(self.handler(Path('.'), {}).with_disk_progress(None))   # no account named: nothing

    def test_the_full_page_never_waits_for_the_disk_view(self):
        # Building it can take seconds: the page shows the last one built (or Working out), the polls build it.
        from unittest import mock
        with tempfile.TemporaryDirectory() as temporary:
            handler = self.handler(Path(temporary), {'account': 'Me'})
            with mock.patch('src.player_web.view_from_disk', side_effect=AssertionError('built for the page')):
                page = handler.with_disk_progress(None, build=False)
            self.assertEqual(page, {'progress': {'view': None, 'made_at': None, 'error': None}})
            with mock.patch('src.player_web.view_from_disk', return_value={'runs': []}):
                handler.with_disk_progress(None)                              # a poll builds it
            with mock.patch('src.player_web.view_from_disk', side_effect=AssertionError('built for the page')):
                page = handler.with_disk_progress(None, build=False)
            self.assertEqual(page['progress']['view'], {'runs': []})

    def test_a_failure_is_the_tabs_error_not_a_broken_page(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as temporary:
            handler = self.handler(Path(temporary), {'account': 'Me'})
            with mock.patch('src.player_web.view_from_disk', side_effect=RuntimeError('boom')):
                progress = handler.with_disk_progress({'progress': None})['progress']
        self.assertIsNone(progress['view'])
        self.assertIn('boom', progress['error'])
        self.assertEqual(self.handler(Path('.'), {}).with_disk_progress({'x': 1}), {'x': 1})   # no account named
