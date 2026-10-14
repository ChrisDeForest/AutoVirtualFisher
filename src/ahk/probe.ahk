#Requires AutoHotkey v2.0
#SingleInstance Off
#NoTrayIcon
text := A_AhkVersion "`t" (A_PtrSize * 8)
size := StrPut(text, "UTF-8") - 1
bytes := Buffer(size + 1)
StrPut(text, bytes, "UTF-8")
chars := 0
DllCall("Crypt32\CryptBinaryToStringW", "Ptr", bytes, "UInt", size, "UInt", 0x40000001, "Ptr", 0, "UInt*", &chars)
b64 := Buffer(chars * 2)
DllCall("Crypt32\CryptBinaryToStringW", "Ptr", bytes, "UInt", size, "UInt", 0x40000001, "Ptr", b64, "UInt*", &chars)
out := FileOpen("*", "w", "UTF-8-RAW")
out.Write("1`t0`tok`truntime`tnot_started`t" StrGet(b64) "`n")
out.Read(0)
ExitApp
