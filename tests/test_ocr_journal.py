import asyncio
import gzip
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


class OcrJournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def reads(self, journal):
        journal.close()
        with gzip.open(self.root/'run-1'/'ocr_reads.jsonl.gz','rt',encoding='utf-8') as stream:
            return [json.loads(line) for line in stream]

    def test_every_read_is_compressed_with_capture_and_crop_metadata(self):
        from src.ocr_journal import OcrJournal, ocr_scope, record_ocr_read
        journal = OcrJournal(self.root,lambda: {'run_id':'run-1','action_id':'action-1'})
        with ocr_scope(journal,window=99,light=True):
            for i in range(100):
                record_ocr_read('windows',['uncomrnon chest',str(i)],region=(1,2,3,4),scale=2)
        records = self.reads(journal)
        self.assertEqual(len(records),100)
        self.assertEqual(records[0]['lines'],['uncomrnon chest','0'])
        self.assertEqual(records[0]['region'],[1,2,3,4])
        self.assertEqual(records[0]['action_id'],'action-1')
        self.assertEqual(len({r['capture_id'] for r in records}),1)
        self.assertTrue(records[0]['light'])
        plain = sum(len(json.dumps(record).encode()) for record in records)
        self.assertLess((self.root/'run-1'/'ocr_reads.jsonl.gz').stat().st_size,plain/2)
        self.assertEqual(list(self.root.rglob('*.png')),[])

    def test_scope_covers_async_reads_and_resets_after_capture(self):
        from src.ocr_journal import OcrJournal, ocr_scope, record_ocr_read, trace_windows_read
        journal = OcrJournal(self.root,lambda:'run-1')
        @trace_windows_read
        async def read(path,scale,region=None):
            return [{'text':'raw OCR'}]
        with ocr_scope(journal): asyncio.run(read('unused',3,(1,2,3,4)))
        record_ocr_read('windows',['outside live automation'])
        records = self.reads(journal)
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['lines'],['raw OCR'])
        self.assertEqual(records[0]['scale'],3)

    def test_tesseract_logs_success_and_failure_without_changing_results(self):
        from src.ocr_journal import OcrJournal,ocr_scope
        from src.reply_ocr import _tesseract
        journal = OcrJournal(self.root,lambda:'run-1')
        with ocr_scope(journal):
            with patch('src.reply_ocr.subprocess.run',return_value=subprocess.CompletedProcess([],0,' raw read \n','')):
                self.assertEqual(_tesseract('unused','unused',7),['raw read'])
            with patch('src.reply_ocr.subprocess.run',side_effect=subprocess.TimeoutExpired('unused',5)):
                with self.assertRaises(subprocess.TimeoutExpired): _tesseract('unused','unused',6)
        records = self.reads(journal)
        self.assertEqual(len(records),2)
        self.assertEqual(records[0]['lines'],[' raw read '])
        self.assertIn('error',records[1])

    def test_closed_or_no_run_does_not_write_and_resume_opens_new_run(self):
        from src.ocr_journal import OcrJournal,ocr_scope,record_ocr_read
        state = [None]
        journal = OcrJournal(self.root,lambda:state[0])
        with ocr_scope(journal): record_ocr_read('windows',['inspection'])
        self.assertEqual(list(self.root.iterdir()),[])
        state[0]='run-1'
        journal.close()
        with ocr_scope(journal): record_ocr_read('windows',['after stop'])
        self.assertEqual(list(self.root.iterdir()),[])
        journal.resume()
        with ocr_scope(journal): record_ocr_read('windows',['new run'])
        self.assertEqual(self.reads(journal)[0]['lines'],['new run'])

    def test_disk_errors_never_escape_ocr_or_retain_queued_records(self):
        from src.ocr_journal import OcrJournal,ocr_scope,record_ocr_read
        self.root.joinpath('run-1').write_text('cannot be directory')
        journal = OcrJournal(self.root,lambda:'run-1')
        with ocr_scope(journal):
            for _ in range(100): record_ocr_read('windows',['read'])
        self.assertTrue(journal.error)
        journal.close()

    def test_stop_during_capture_keeps_completed_reads_but_rejects_new_captures(self):
        from src.ocr_journal import OcrJournal,ocr_scope,record_ocr_read
        run = ['run-1']
        journal = OcrJournal(self.root,lambda:run[0])
        with ocr_scope(journal):
            journal.close()  # Stop arrives while a Windows OCR pass is underway.
            run[0] = 'later-run'
            record_ocr_read('windows',['finished after Stop'])
            with ocr_scope(journal):
                record_ocr_read('windows',['new capture after Stop'])
        records = self.reads(journal)
        self.assertEqual([r['lines'] for r in records],[['finished after Stop']])
        self.assertEqual(records[0]['run_id'],'run-1')
        self.assertFalse((self.root/'later-run').exists())

    def test_ocr_logs_rotate_and_enforce_run_and_account_byte_limits(self):
        import random
        from src.ocr_journal import OcrJournal,ocr_scope,record_ocr_read
        from src import storage_limits as limits
        run = ['run-1']
        journal = OcrJournal(self.root,lambda:run[0])
        self.addCleanup(journal.close)
        randomizer = random.Random(17)
        with patch.object(limits,'OCR_SEGMENT_MAX_BYTES',700), \
             patch.object(limits,'OCR_RUN_MAX_BYTES',2500), \
             patch.object(limits,'OCR_ACCOUNT_MAX_BYTES',4000):
            for run_id in ('run-1','run-2','run-3'):
                run[0] = run_id
                for _ in range(12):
                    with ocr_scope(journal):
                        record_ocr_read('windows',[randomizer.randbytes(300).hex()])
                journal.close()
                files = list((self.root/run_id).glob('ocr_reads*.gz'))
                self.assertLessEqual(sum(p.stat().st_size for p in files),2500)
                self.assertLessEqual(sum(p.stat().st_size for p in self.root.glob('*/ocr_reads*.gz')),4000)
                for path in files:
                    with gzip.open(path,'rt',encoding='utf-8') as stream:
                        self.assertTrue(all(json.loads(line)['type']=='ocr_read' for line in stream))
                journal.resume()
            self.assertTrue(list(self.root.rglob('ocr_reads.*.jsonl.gz')))

    def test_linked_journal_ancestor_is_never_pruned_or_written(self):
        from src.ocr_journal import OcrJournal,ocr_scope,record_ocr_read
        from src import storage_limits as limits
        folder = self.root/'run-1'
        folder.mkdir()
        existing = folder/'ocr_reads.jsonl.gz'
        existing.write_bytes(b'keep external data')
        journal = OcrJournal(self.root,lambda:'run-1')
        with patch.object(Path,'is_junction',lambda path:path == self.root), \
             patch.object(limits,'OCR_ACCOUNT_MAX_BYTES',0):
            journal.close()
            self.assertEqual(existing.read_bytes(),b'keep external data')
            self.assertTrue(journal.error)
            journal.resume()
            with ocr_scope(journal): record_ocr_read('windows',['must not append through junction'])
        self.assertEqual(existing.read_bytes(),b'keep external data')
        self.assertTrue(journal.error)
