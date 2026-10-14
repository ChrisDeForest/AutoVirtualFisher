import base64
import importlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.m = importlib.import_module('src.ahk_runtime')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.exe = Path(self.tmp.name) / 'Program Files' / 'AutoHotkey64.exe'
        self.exe.parent.mkdir()
        self.exe.touch()

    def report(self, version='2.0.30', bits=64, protocol='1'):
        data = base64.b64encode(f'{version}\t{bits}'.encode()).decode()
        return subprocess.CompletedProcess([], 0, f'{protocol}\t0\tok\truntime\tnot_started\t{data}\n', '')

    def test_runtime_probe_accepts_compatible_stable_v2(self):
        for version in ('2.0.30', '2.0.31', '2.1.0'):
            with patch.object(self.m.subprocess, 'run', return_value=self.report(version)) as run:
                result = self.m.resolve_ahk(self.exe)
            self.assertEqual((result.executable, result.version, result.bitness), (self.exe.resolve(), version, 64))
            args, kwargs = run.call_args
            self.assertEqual(args[0][0], str(self.exe.resolve()))
            self.assertTrue(Path(args[0][2]).is_absolute())
            self.assertFalse(kwargs['shell'])
            self.assertEqual(kwargs['timeout'], 3)

    def test_runtime_probe_rejects_incompatible_interpreter(self):
        for version, bits, protocol in [('1.1.37',64,'1'), ('3.0.0',64,'1'), ('2.0.29',64,'1'), ('2.0.30',32,'1'), ('2.1.0-beta',64,'1'), ('2.0.30',64,'2')]:
            with self.subTest(version=version, bits=bits, protocol=protocol), patch.object(self.m.subprocess, 'run', return_value=self.report(version,bits,protocol)):
                with self.assertRaises(self.m.AhkSetupError): self.m.resolve_ahk(self.exe)

    def test_invalid_explicit_override_does_not_fall_back(self):
        with patch.object(self.m, '_candidates') as candidates:
            with self.assertRaises(self.m.AhkSetupError): self.m.resolve_ahk(self.exe.parent/'missing.exe')
            candidates.assert_not_called()

    def test_explicit_then_environment_then_machine_then_programfiles_then_path(self):
        with patch.dict(self.m.os.environ, {'AVF_AHK_EXE': str(self.exe)}), patch.object(self.m.subprocess,'run',return_value=self.report()):
            self.assertEqual(self.m.resolve_ahk().executable, self.exe.resolve())
        with patch.dict(self.m.os.environ, {}, clear=True), patch.object(self.m, '_machine_dirs', return_value=[Path('D:/AHK')]), patch.object(self.m.shutil,'which',return_value='E:/AutoHotkey64.exe'):
            self.assertEqual(list(self.m._candidates()), [Path('D:/AHK/v2/AutoHotkey64.exe'),Path('C:/Program Files/AutoHotkey/v2/AutoHotkey64.exe'),Path('E:/AutoHotkey64.exe')])

    def test_invalid_environment_override_is_authoritative(self):
        with patch.dict(self.m.os.environ, {'AVF_AHK_EXE': 'missing.exe'}), patch.object(self.m,'_candidates') as candidates:
            with self.assertRaises(self.m.AhkSetupError): self.m.resolve_ahk()
            candidates.assert_not_called()

    def test_runtime_probe_rejects_bad_output_and_uia(self):
        with patch.object(self.m.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'garbage','')):
            with self.assertRaises(self.m.AhkSetupError): self.m.resolve_ahk(self.exe)
        uia = self.exe.with_name('AutoHotkey64_UIA.exe'); uia.touch()
        with self.assertRaises(self.m.AhkSetupError): self.m.resolve_ahk(uia)

    def test_import_does_not_start_process_or_initialize_windows(self):
        import sys
        code = "from unittest.mock import patch; import subprocess; " \
               "r=patch.object(subprocess,'run'); p=patch.object(subprocess,'Popen'); " \
               "run=r.start(); popen=p.start(); import src.ahk_runtime; " \
               "run.assert_not_called(); popen.assert_not_called()"
        result = subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_readiness_cache_invalidates_when_executable_changes(self):
        self.m._READINESS.clear()
        with patch.object(self.m.subprocess,'run',return_value=self.report()) as run:
            self.m.ready_ahk(self.exe); self.m.ready_ahk(self.exe)
            self.assertEqual(run.call_count,1)
            self.exe.write_bytes(b'changed')
            self.m.ready_ahk(self.exe)
            self.assertEqual(run.call_count,2)
