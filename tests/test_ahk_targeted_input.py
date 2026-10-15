"""Real AHK tests use hidden disposable windows; never inject global keyboard input."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class InputIsolationContractTests(unittest.TestCase):
    def test_worker_has_no_automatic_modifier_sending_path(self):
        source = Path('src/ahk/discord_input.ahk').read_text(encoding='utf-8')
        self.assertNotIn('ControlSend ', source)
        self.assertNotIn('ControlSendText ', source)
        self.assertIn('TargetKeyboard renderer, op, payload', source)
        helper = Path('src/ahk/targeted_keyboard.ahk').read_text(encoding='utf-8')
        for forbidden in ('SendInput', 'keybd_event', 'SendEvent', 'SendPlay', 'BlockInput',
                          'SetForegroundWindow', 'SetCursorPos'):
            self.assertNotIn(forbidden, helper)


@unittest.skipUnless(os.environ.get('AVF_TEST_AHK_EXE'), 'Opt-in real AHK tests')
class TargetKeyboardTests(unittest.TestCase):
    def test_background_text_keys_and_clear_have_isolated_modifier_state(self):
        executable = os.environ['AVF_TEST_AHK_EXE']
        helper = Path('src/ahk/targeted_keyboard.ahk').resolve()
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            events, result = folder/'events.txt', folder/'result.txt'
            receiver = folder/'receiver.ahk'
            receiver.write_text('#Requires AutoHotkey v2.0\n#include "'+str(helper)+'"\n'+r'''
#NoTrayIcon
logPath := A_Args[1]
window := Gui()
textBox := window.AddEdit("w300 r4", "leftover")
callback := CallbackCreate(ObserveProc)
oldProc := DllCall("SetWindowLongPtrW", "Ptr", textBox.Hwnd, "Int", -4, "Ptr", callback, "Ptr")
SetTimer(CheckDone, 20)
out := FileOpen("*", "w", "UTF-8-RAW")
out.Write(textBox.Hwnd "`n")
out.Read(0)
Persistent()
ObserveProc(hwnd, msg, wParam, lParam) {
    global textBox, logPath, oldProc
    if hwnd = textBox.Hwnd && (msg = 0x100 || msg = 0x101 || msg = 0x102)
        FileAppend(msg "|" wParam "|" GetKeyState("Ctrl") "|" GetKeyState("Shift") "|" GetKeyState("Alt") "|" GetKeyState("LWin") "`n", logPath, "UTF-8-RAW")
    return DllCall("CallWindowProcW", "Ptr", oldProc, "Ptr", hwnd, "UInt", msg, "UPtr", wParam, "Ptr", lParam, "Ptr")
}
CheckDone() {
    global textBox, logPath, oldProc, callback
    if !FileExist(A_Args[2])
        return
    FileAppend("CROSS|" textBox.Value "`n", logPath, "UTF-8-RAW")
    ; No attachment here: these modifiers remain set until the helper explicitly clears them.
    for held in [0x10,0x11,0x12,0x5B,0x5C,0xA0,0xA1,0xA2,0xA3,0xA4,0xA5] {
        heldState := Buffer(256,0)
        NumPut("UChar", 0x80, heldState, held)
        DllCall("SetKeyboardState", "Ptr", heldState)
        TargetKeyboard textBox.Hwnd, "select_all", ""
        TargetKeyboard textBox.Hwnd, "key", "Backspace"
        TargetKeyboard textBox.Hwnd, "text", "/fish"
    }
    DllCall("SetKeyboardState", "Ptr", Buffer(256,0))
    FileAppend("TEXT|" textBox.Value "`n", logPath, "UTF-8-RAW")
    DllCall("SetWindowLongPtrW", "Ptr", textBox.Hwnd, "Int", -4, "Ptr", oldProc)
    CallbackFree(callback)
    ExitApp()
}
''', encoding='utf-8')
            sender = folder/'sender.ahk'
            sender.write_text('#Requires AutoHotkey v2.0\n#NoTrayIcon\n#include "'+str(helper)+'"\n'+r'''
renderer := Integer(A_Args[1])
foreground := DllCall("GetForegroundWindow", "Ptr")
physical := Buffer(8)
keys := [0xA0,0xA1,0xA2,0xA3,0xA4,0xA5,0x5B,0x5C]
for i, vk in keys
    NumPut("UChar", (DllCall("GetAsyncKeyState", "Int", vk, "Short") & 0x8000) != 0, physical, i-1)
; Simulate stale/held modifiers in the sender's local keyboard table only.
; Physical/global keyboard state is never altered by this test.
for held in [0x11,0x10,0x12,0x5B] {
    state := Buffer(256,0)
    NumPut("UChar", 0x80, state, held)
    DllCall("SetKeyboardState", "Ptr", state)
    TargetKeyboard renderer, "select_all", ""
    TargetKeyboard renderer, "key", "Backspace"
    TargetKeyboard renderer, "text", "/fish"
}
TargetKeyboard renderer, "text", " é 🐟 {Enter} ^!+#"
TargetKeyboard renderer, "key", "Enter"
TargetKeyboard renderer, "key", "Tab"
TargetKeyboard renderer, "key", "Escape"
changed := DllCall("GetForegroundWindow", "Ptr") != foreground
for i, vk in keys
    changed := changed || (((DllCall("GetAsyncKeyState", "Int", vk, "Short") & 0x8000) != 0) != NumGet(physical,i-1,"UChar"))
FileAppend(changed ? "changed" : "unchanged", A_Args[2], "UTF-8-RAW")
''', encoding='utf-8')
            options = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
            process = subprocess.Popen([executable, '/ErrorStdOut', str(receiver), str(events), str(result)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', **options)
            try:
                import queue
                import threading
                ready = queue.Queue()
                threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
                hwnd = ready.get(timeout=4).strip()
                self.assertTrue(hwnd.isdigit(), hwnd or process.stderr.read())
                run = subprocess.run([executable, '/ErrorStdOut', str(sender), hwnd, str(result)],
                    capture_output=True, text=True, encoding='utf-8', timeout=8, **options)
                self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
                process.wait(timeout=4)
                self.assertEqual(result.read_text(encoding='utf-8'), 'unchanged')
                lines = events.read_text(encoding='utf-8').splitlines()
                characters = [int(line.split('|')[1]) for line in lines if line.startswith('258|')]
                self.assertEqual(characters.count(ord('/')), 15)
                self.assertIn(0xD83D, characters)
                self.assertIn(0xDC1F, characters)
                self.assertTrue(any(line.startswith('CROSS|/fish é 🐟 {Enter} ^!+#') for line in lines),lines)
                self.assertTrue(any(line.startswith('TEXT|/fish') for line in lines), lines)
                for line in lines:
                    if line.startswith(('256|','257|','258|')):
                        _, key, ctrl, shift, alt, win = line.split('|')
                        self.assertEqual((shift,alt,win), ('0','0','0'), line)
                        if int(key) not in (0x41, 1):
                            self.assertEqual(ctrl,'0',line)
            finally:
                if process.poll() is None:
                    process.terminate()
                remaining_output = process.communicate(timeout=4)
                if any(remaining_output):
                    print('receiver output:', remaining_output)



    def test_hung_target_is_bounded_by_bridge_and_detaches_on_worker_exit(self):
        import queue
        import threading
        import time
        from unittest.mock import patch
        from src.ahk_bridge import AhkBridge, AhkBridgeError, AhkTarget
        from src.ahk_runtime import resolve_ahk
        runtime = resolve_ahk(Path(os.environ['AVF_TEST_AHK_EXE']))
        helper = Path('src/ahk/targeted_keyboard.ahk').resolve()
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            log = folder/'hung.txt'
            receiver = folder/'hung.ahk'
            receiver.write_text(r'''#Requires AutoHotkey v2.0
#NoTrayIcon
logPath := A_Args[1]
window := Gui()
textBox := window.AddEdit("w300", "leftover")
callback := CallbackCreate(ObserveProc)
oldProc := DllCall("SetWindowLongPtrW", "Ptr", textBox.Hwnd, "Int", -4, "Ptr", callback, "Ptr")
out := FileOpen("*", "w", "UTF-8-RAW")
out.Write(textBox.Hwnd "`n")
out.Read(0)
Persistent()
ObserveProc(hwnd, msg, wParam, lParam) {
    global logPath, oldProc, out
    if msg = 0x100 && wParam = 0x41 {
        FileAppend("before|" GetKeyState("Ctrl") "`n", logPath, "UTF-8-RAW")
        DllCall("Sleep", "UInt", 1000)
        FileAppend("after|" GetKeyState("Ctrl") "`n", logPath, "UTF-8-RAW")
        out.Write("finished`n")
        out.Read(0)
    }
    return DllCall("CallWindowProcW", "Ptr", oldProc, "Ptr", hwnd, "UInt", msg, "UPtr", wParam, "Ptr", lParam, "Ptr")
}
''', encoding='utf-8')
            worker = folder/'worker.ahk'
            source = Path('src/ahk/discord_input.ahk').read_text(encoding='utf-8')
            support = source[source.index('\nReply(id, status, code, state, message)'):]
            worker.write_text('#Requires AutoHotkey v2.0\n#NoTrayIcon\n#include "'+str(helper)+'"\n'+r'''
input := FileOpen("*", "r", "UTF-8-RAW")
output := FileOpen("*", "w", "UTF-8-RAW")
loop {
    line := ReadPipeLine(input.Handle)
    if IsInteger(line) && line = -1
        break
    fields := StrSplit(line, "`t")
    if fields[3] = "probe" {
        Reply(Integer(fields[2]), "ok", "probe", "not_started", A_AhkVersion "`t" (A_PtrSize*8))
        continue
    }
    ; Disposable test worker only: the production Discord guard is never bypassed.
    TargetKeyboard Integer(fields[5]), fields[3], ""
    Reply(Integer(fields[2]), "ok", fields[3], "posted", "")
}
ExitApp
'''+support, encoding='utf-8')
            options = {'creationflags': getattr(subprocess,'CREATE_NO_WINDOW',0)}
            process = subprocess.Popen([str(runtime.executable),'/ErrorStdOut',str(receiver),str(log)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', **options)
            bridge = AhkBridge(runtime)
            try:
                ready = queue.Queue()
                threading.Thread(target=lambda: ready.put(process.stdout.readline()),daemon=True).start()
                hwnd = ready.get(timeout=4).strip()
                self.assertTrue(hwnd.isdigit(),hwnd or process.stderr.read())
                bridge._command = lambda: [str(runtime.executable),'/ErrorStdOut',str(worker)]
                bridge.start()
                child = bridge._process
                started = time.monotonic()
                with patch('src.ahk_bridge.PRIMITIVE_TIMEOUT',.25):
                    with self.assertRaises(AhkBridgeError):
                        bridge.request('select_all',AhkTarget(int(hwnd),int(hwnd)),None,'',lambda:False)
                self.assertLess(time.monotonic()-started,1)
                self.assertIsNotNone(child.poll())
                self.assertIsNone(bridge._process)
                finished = queue.Queue()
                threading.Thread(target=lambda: finished.put(process.stdout.readline()),daemon=True).start()
                self.assertEqual(finished.get(timeout=4).strip(), 'finished')
                self.assertEqual(log.read_text(encoding='utf-8').splitlines(),['before|1','after|0'])
            finally:
                bridge.close()
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=4)
