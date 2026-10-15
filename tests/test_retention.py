import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from src import retention


class RetentionTests(unittest.TestCase):
    def test_scoped_rolling_groups_preserve_unowned_and_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('01.png', '01.json', '02.png', '02.json', '03.png', 'notes.txt', 'backup.json'):
                (root / name).write_bytes(b'1234')
            retention.prune_files(root, re.compile(r'\d{2}\.(?:png|json)'), 2, 8, group_stem=True)
            self.assertFalse((root/'01.png').exists())
            self.assertFalse((root/'01.json').exists())
            self.assertFalse((root/'02.png').exists())
            self.assertTrue((root/'03.png').exists())
            self.assertEqual((root/'notes.txt').read_bytes(),b'1234')
            self.assertTrue((root/'backup.json').exists())

    def test_protected_records_can_exceed_rolling_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for index in range(4): (root/f'{index}.json').write_text(str(index))
            retention.prune_files(root, re.compile(r'\d\.json'), 1, 1,
                                  protected=lambda p: p.stem in ('0','1'))
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['0.json','1.json','3.json'])

    def test_line_trim_keeps_complete_newest_lines_and_uses_atomic_replace(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'history.jsonl'
            path.write_bytes(b'old\nsecond\nnew\n')
            import os
            original=os.replace
            seen=[]
            def replace(source,dest):
                seen.append(path.read_bytes())
                original(source,dest)
            with patch('src.retention.os.replace', side_effect=replace):
                retention.trim_lines(path, 11)
            self.assertEqual(path.read_bytes(), b'second\nnew\n')
            self.assertEqual(seen,[b'old\nsecond\nnew\n'])
            self.assertEqual(list(Path(temp).iterdir()),[path])

    def test_under_limit_file_is_not_read(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'history.jsonl';path.write_bytes(b'new\n')
            with patch.object(Path,'open',side_effect=AssertionError('unnecessary read')):
                retention.trim_lines(path,100)

    def test_snapshot_limit_rejects_without_erasing_existing_records(self):
        from src.player_snapshots import save_snapshot
        from src import storage_limits as limits
        with tempfile.TemporaryDirectory() as temp, patch.object(limits,'SNAPSHOT_MAX_FILES',1):
            state={'profile':{'account_name':'Player','balance':10,'level':1}}
            first=save_snapshot(state,Path(temp),mode='manual')
            second=save_snapshot(state,Path(temp),mode='manual')
            self.assertEqual(first['status'],'saved')
            self.assertEqual(second['status'],'rejected')
            self.assertIn('storage limit',second['reason'].lower())
            self.assertEqual(len(list(Path(temp).glob('*/*.json'))),1)

    def test_review_keeps_blocking_and_only_prunes_owned_nonblocking(self):
        from src.review_queue import add_item, list_items
        from src import storage_limits as limits
        with tempfile.TemporaryDirectory() as temp, patch.object(limits,'REVIEW_MAX_FILES',1):
            root=Path(temp); (root/'backup.json').write_text('{}')
            blocked=add_item(root,'Player','profile','blocked',['uncertain'])
            old=add_item(root,'Player','profile','old',[],blocks_actions=False)
            new=add_item(root,'Player','profile','new',[],blocks_actions=False)
            ids={item['id'] for item in list_items(root)[0]}
            self.assertEqual(ids,{blocked['id'],new['id']})
            self.assertFalse((root/(old['id']+'.json')).exists())
            self.assertTrue((root/'backup.json').exists())

    def test_profile_history_and_console_are_bounded_on_write(self):
        from src.progress_store import append_history
        from src.run_output import RunOutput
        from src import storage_limits as limits
        with tempfile.TemporaryDirectory() as temp, patch.object(limits,'PROFILE_HISTORY_MAX_BYTES',80), patch.object(limits,'RUN_LOG_MAX_BYTES',80):
            root=Path(temp);history=root/'profile_history.jsonl'
            for i in range(10):append_history(history,{'at':str(i),'kind':'profile'})
            self.assertLessEqual(history.stat().st_size,80)
            self.assertEqual(json.loads(history.read_text().splitlines()[-1])['at'],'9')
            with RunOutput(root,console=io.StringIO()) as output:
                for i in range(20):output.say('message '+str(i))
                log=output.path
            self.assertLessEqual(log.stat().st_size,80)
            self.assertIn('message 19',log.read_text())

    def test_capture_pairs_obey_byte_bound_and_preserve_unowned_files(self):
        from src.live_capture import prune_kept
        from src import storage_limits as limits
        with tempfile.TemporaryDirectory() as temp, patch.object(limits,'CAPTURE_MAX_BYTES',8):
            root=Path(temp)
            for i in range(3):
                stamp=f'20261014-120000-{i:06}'
                for extension in ('png','json'): (root/f'{stamp}.{extension}').write_bytes(b'1234')
            (root/'personal.png').write_bytes(b'keep')
            prune_kept(root,limit=200)
            self.assertEqual(len(list(root.glob('2026*'))),2)
            self.assertTrue((root/'20261014-120000-000002.png').exists())
            self.assertEqual((root/'personal.png').read_bytes(),b'keep')

    def test_diagnostics_retention_is_scoped_and_best_effort(self):
        from types import SimpleNamespace
        from src.automation_live import save_diagnostics
        from src.player_snapshots import account_key
        from src import storage_limits as limits
        controller=SimpleNamespace(config=SimpleNamespace(account='Player'),state=SimpleNamespace(run_id=None))
        transport=SimpleNamespace(baseline=None,last_frame=None,last_lines=['read'],blocked=False,last_issue=None)
        with tempfile.TemporaryDirectory() as temp, patch.object(limits,'DIAGNOSTIC_MAX_FILES',2):
            root=Path(temp)
            folder=root/account_key('Player')/'transport_diagnostics';folder.mkdir(parents=True)
            (folder/'backup.json').write_text('keep')
            for i in range(4):
                controller.state.run_id=f'20261014T12000{i}.000000Z-abcd1234'
                self.assertEqual(save_diagnostics(controller,transport,root),'')
            self.assertEqual(len(list(folder.glob('2026*'))),2)
            self.assertEqual((folder/'backup.json').read_text(),'keep')
            with patch('src.retention.owned_files',side_effect=OSError('unreadable')):
                self.assertEqual(save_diagnostics(controller,transport,root),'')

    def test_snapshot_unreadable_capacity_is_actionable_rejection(self):
        from src.player_snapshots import save_snapshot
        with tempfile.TemporaryDirectory() as temp, patch('src.retention.owned_files',side_effect=PermissionError('denied')):
            result=save_snapshot({'profile':{'account_name':'P','balance':1,'level':1}},Path(temp))
            self.assertEqual(result['status'],'rejected')
            self.assertIn('denied',result['reason'])
            self.assertFalse(list(Path(temp).glob('*/*.json')))

    def test_trimming_failure_preserves_original_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'history.jsonl';path.write_bytes(b'one\ntwo\nthree\n')
            with patch('src.retention.os.replace',side_effect=OSError('blocked')):
                with self.assertRaises(OSError):retention.trim_lines(path,6)
            self.assertEqual(path.read_bytes(),b'one\ntwo\nthree\n')
            self.assertEqual(list(Path(temp).iterdir()),[path])

    def test_linked_files_cannot_escape_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'owned';folder.mkdir()
            outside=root/'outside.json';outside.write_bytes(b'keep')
            link=folder/'01.json'
            try:link.symlink_to(outside)
            except OSError:self.skipTest('Creating symlinks needs Windows developer mode')
            retention.prune_files(folder,re.compile(r'01\.json'),0,0)
            self.assertEqual(outside.read_bytes(),b'keep')
            self.assertTrue(link.is_symlink())
            with self.assertRaises(OSError):retention.trim_lines(link,0)

    def test_html_cache_is_bounded_across_known_folders_and_preserves_current(self):
        from src.data_scraping import web_scraper as scraper
        from src import storage_limits as limits
        import os
        import requests
        with tempfile.TemporaryDirectory() as temp, patch.object(limits,'HTML_CACHE_MAX_FILES',2):
            previous=Path.cwd()
            try:
                os.chdir(temp)
                cache=Path('src/data/html');(cache/'pages').mkdir(parents=True)
                (cache/'commands.html').write_text('<p>commands</p>')
                (cache/'pages'/'older.html').write_text('<p>old</p>')
                os.utime(cache/'commands.html',(1,1));os.utime(cache/'pages'/'older.html',(2,2))
                reference=Path('src/data/reference.json');reference.write_text('keep')
                backup=Path('src/data/backups');backup.mkdir();(backup/'old.html').write_text('keep')
                result=requests.Response();result.status_code=200;result._content=b'<p>new</p>'
                with patch.object(scraper.requests,'get',return_value=result):
                    self.assertEqual(scraper.process_urls(['https://example.test/wiki/Fish'],1)[0].p.text,'new')
                self.assertFalse((cache/'commands.html').exists())
                self.assertTrue((cache/'pages'/'fish.html').exists())
                self.assertTrue((cache/'pages'/'older.html').exists())
                self.assertEqual(reference.read_text(),'keep')
                self.assertEqual((backup/'old.html').read_text(),'keep')
            finally:os.chdir(previous)

    def test_html_oversized_download_is_parsed_without_caching(self):
        from src.data_scraping import web_scraper as scraper
        from src import storage_limits as limits
        import os
        import requests
        with tempfile.TemporaryDirectory() as temp, patch.object(limits,'HTML_CACHE_MAX_BYTES',8):
            previous=Path.cwd()
            try:
                os.chdir(temp)
                result=requests.Response();result.status_code=200;result._content=b'<p>oversized</p>'
                with patch.object(scraper.requests,'get',return_value=result):
                    self.assertEqual(scraper.process_urls(['https://example.test/wiki/Fish'],1)[0].p.text,'oversized')
                self.assertFalse(Path('src/data/html/pages/fish.html').exists())
            finally:os.chdir(previous)

    def test_oversized_newest_artifact_does_not_evict_older_files_that_fit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'03.json').write_bytes(b'x'*20)
            (root/'02.json').write_bytes(b'1234')
            (root/'01.json').write_bytes(b'5678')
            retention.prune_files(root,re.compile(r'\d{2}\.json'),2,8)
            self.assertEqual(sorted(p.name for p in root.iterdir()),['01.json','02.json'])
