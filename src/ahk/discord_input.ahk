#Requires AutoHotkey v2.0
#SingleInstance Off
#NoTrayIcon
#Include targeted_keyboard.ahk
SetKeyDelay -1, -1
SetControlDelay -1
input := FileOpen("*", "r", "UTF-8-RAW")
output := FileOpen("*", "w", "UTF-8-RAW")
lastID := 0
while true {
    line := ReadPipeLine(input.Handle)
    if IsInteger(line) && line = -1
        break
    line := RTrim(line, "`r`n")
    if line = ""
        continue
    id := 0
    state := "not_started"
    try {
        fields := StrSplit(line, "`t")
        if fields.Length != 8 || fields[1] != "1"
            throw Error("Invalid protocol framing")
        if !RegExMatch(fields[2], "^[1-9][0-9]*$")
            throw Error("Invalid request ID")
        id := Integer(fields[2])
        if id <= lastID
            throw Error("Stale request ID")
        lastID := id
        op := fields[3]
        if !InStr("|probe|target|text|key|select_all|reset|click|shutdown|", "|" op "|")
            throw Error("Unknown operation")
        Loop 4 {
            if !RegExMatch(fields[A_Index + 3], "^-?[0-9]+$")
                throw Error("Invalid numeric field")
        }
        payload := Decode(fields[8])
        if StrLen(payload) > 1024 || RegExMatch(payload, "[\x00-\x1f\x7f]")
            throw Error("Only bounded literal single-line text is allowed")
        if op = "probe" {
            Reply(id, "ok", "probe", "not_started", A_AhkVersion "`t" (A_PtrSize * 8))
            continue
        }
        if op = "shutdown" {
            Reply(id, "ok", "shutdown", "not_started", "")
            break
        }
        hwnd := Integer(fields[4])
        renderer := Integer(fields[5])
        x := Integer(fields[6])
        y := Integer(fields[7])
        Guard(hwnd, renderer)
        target := "ahk_id " hwnd
        if op = "key" && !InStr("|Enter|Tab|Escape|Backspace|", "|" payload "|")
            throw Error("Unsupported key")
        if op = "target" || op = "click" {
            rect := Buffer(16)
            if !DllCall("GetClientRect", "Ptr", hwnd, "Ptr", rect)
                throw Error("Client bounds unavailable")
            if x < 0 || y < 0 || x >= NumGet(rect, 8, "Int") || y >= NumGet(rect, 12, "Int")
                throw Error("Click lies outside client area")
        }
        state := "unknown"
        switch op {
            case "target", "click":
                ControlClick "x" x " y" y, target, , "Left", 1, "NA Pos"
            case "text", "key", "select_all", "reset":
                TargetKeyboard renderer, op, payload
        }
        Reply(id, "ok", op, "posted", "")
    } catch Error as err {
        Reply(id, "error", "worker", state, err.Message)
    }
}
ExitApp

Guard(hwnd, renderer) {
    if !DllCall("IsWindow", "Ptr", hwnd) || DllCall("IsIconic", "Ptr", hwnd)
        throw Error("Discord is unavailable or minimized")
    process := StrLower(WinGetProcessName("ahk_id " hwnd))
    if !InStr("|discord.exe|discordptb.exe|discordcanary.exe|", "|" process "|")
        throw Error("Window is not Discord")
    if !DllCall("IsChild", "Ptr", hwnd, "Ptr", renderer) || !DllCall("IsWindowVisible", "Ptr", renderer)
        throw Error("Discord renderer is unavailable")
    parentPID := 0, childPID := 0
    DllCall("GetWindowThreadProcessId", "Ptr", hwnd, "UInt*", &parentPID)
    DllCall("GetWindowThreadProcessId", "Ptr", renderer, "UInt*", &childPID)
    if parentPID != childPID || WinGetClass("ahk_id " renderer) != "Chrome_RenderWidgetHostHWND"
        throw Error("Renderer ownership changed")
}

Reply(id, status, code, state, message) {
    global output
    output.Write("1`t" id "`t" status "`t" code "`t" state "`t" Encode(message) "`n")
    output.Read(0)
}

Encode(text) {
    ; Empty messages are valid success acknowledgements and encode as empty base64.
    if text = ""
        return ""
    size := StrPut(text, "UTF-8") - 1
    bytes := Buffer(size + 1)
    StrPut(text, bytes, "UTF-8")
    chars := 0
    if !DllCall("Crypt32\CryptBinaryToStringW", "Ptr", bytes, "UInt", size, "UInt", 0x40000001, "Ptr", 0, "UInt*", &chars)
        throw Error("Could not encode response")
    result := Buffer(chars * 2)
    DllCall("Crypt32\CryptBinaryToStringW", "Ptr", bytes, "UInt", size, "UInt", 0x40000001, "Ptr", result, "UInt*", &chars)
    return StrGet(result)
}

Decode(text) {
    if text = ""
        return ""
    if !RegExMatch(text, "^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$")
        throw Error("Invalid base64 payload")
    size := 0
    if !DllCall("Crypt32\CryptStringToBinaryW", "Str", text, "UInt", 0, "UInt", 1, "Ptr", 0, "UInt*", &size, "Ptr", 0, "Ptr", 0)
        throw Error("Could not decode payload")
    bytes := Buffer(size + 1, 0)
    DllCall("Crypt32\CryptStringToBinaryW", "Str", text, "UInt", 0, "UInt", 1, "Ptr", bytes, "UInt*", &size, "Ptr", 0, "Ptr", 0)
    ; Reject NUL before StrGet can truncate the payload.
    Loop size {
        if NumGet(bytes, A_Index - 1, "UChar") = 0
            throw Error("NUL is not allowed in payload")
    }
    return StrGet(bytes, size, "UTF-8")
}

ReadPipeLine(handle) {
    line := ""
    byte := Buffer(1)
    loop {
        count := 0
        if !DllCall("ReadFile", "Ptr", handle, "Ptr", byte, "UInt", 1, "UInt*", &count, "Ptr", 0) || count = 0
            return -1
        char := NumGet(byte, 0, "UChar")
        if char = 10
            return line
        if StrLen(line) >= 8192
            throw Error("Request frame too large")
        line .= Chr(char)
    }
}
