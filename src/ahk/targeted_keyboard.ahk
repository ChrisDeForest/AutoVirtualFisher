; Target-thread keyboard state only. Never synthesize desktop-wide modifier events.
TargetKeyboard(renderer, operation, payload) {
    thread := DllCall("GetWindowThreadProcessId", "Ptr", renderer, "Ptr", 0, "UInt")
    current := DllCall("GetCurrentThreadId", "UInt")
    if !thread
        throw Error("Input renderer disappeared")
    attached := false
    saved := Buffer(256)
    restored := false
    try {
        if thread != current {
            if !DllCall("AttachThreadInput", "UInt", current, "UInt", thread, "Int", true)
                throw Error("Could not isolate Discord keyboard state")
            attached := true
        }
        if !DllCall("GetKeyboardState", "Ptr", saved)
            throw Error("Target keyboard state unavailable")
        restored := true
        neutral := Buffer(256)
        DllCall("RtlMoveMemory", "Ptr", neutral, "Ptr", saved, "UPtr", 256)
        ; Include generic and both sided modifiers, including AltGr's Ctrl/Alt pair.
        for vk in [0x10,0x11,0x12,0xA0,0xA1,0xA2,0xA3,0xA4,0xA5,0x5B,0x5C]
            NumPut("UChar", 0, neutral, vk)
        if !DllCall("SetKeyboardState", "Ptr", neutral)
            throw Error("Could not set target keyboard state")
        deadline := A_TickCount + 1200
        switch operation {
            case "reset":
                ; Emergency cleanup after a worker is terminated: leave this queue neutral.
            case "text":
                ; WM_CHAR is literal UTF-16, independent of Ctrl, Shift and keyboard layout.
                Loop StrLen(payload)
                    TargetMessage(renderer, 0x102, Ord(SubStr(payload, A_Index, 1)), 1, deadline)
            case "key":
                keys := Map("Enter", 0x0D, "Tab", 0x09, "Escape", 0x1B, "Backspace", 0x08)
                if !keys.Has(payload)
                    throw Error("Unsupported target key")
                TargetKey(renderer, keys[payload], deadline)
            case "select_all":
                ; Ctrl exists only in the attached target queue; no global key-down/up.
                NumPut("UChar", 0x80, neutral, 0x11)
                NumPut("UChar", 0x80, neutral, 0xA2)
                if !DllCall("SetKeyboardState", "Ptr", neutral)
                    throw Error("Could not select target text")
                TargetKey(renderer, 0x41, deadline, 1)
            default:
                throw Error("Unsupported target operation")
        }
    } finally {
        if restored && operation != "reset"
            DllCall("SetKeyboardState", "Ptr", saved)
        if attached
            DllCall("AttachThreadInput", "UInt", current, "UInt", thread, "Int", false)
    }
}

TargetKey(renderer, vk, deadline, character := -1) {
    scan := DllCall("MapVirtualKeyW", "UInt", vk, "UInt", 0, "UInt")
    bits := 1 | (scan << 16)
    TargetMessage(renderer, 0x100, vk, bits, deadline)
    try {
        ; Synchronous messages do not pass through TranslateMessage. Supply its character
        ; message explicitly, in the same order as normal Windows key delivery.
        TargetMessage(renderer, 0x102, character = -1 ? vk : character, bits, deadline)
    } finally {
        TargetMessage(renderer, 0x101, vk, bits | 0xC0000000, deadline)
    }
}

TargetMessage(renderer, message, key, bits, deadline) {
    remaining := deadline - A_TickCount
    if remaining <= 0
        throw Error("Target keyboard operation timed out")
    result := 0
    ; Complete each event before restoring queue state. Posted messages could run later
    ; with the wrong modifiers. Attached queues can ignore this API's timeout;
    ; the Python bridge's deadline terminates the worker if a target handler hangs.
    ; A separate guarded reset neutralizes the target queue after forced termination.
    ; Uncertain operations are never replayed.
    if !DllCall("SendMessageTimeoutW", "Ptr", renderer, "UInt", message, "UPtr", key,
                "Ptr", bits, "UInt", 0x22, "UInt", Min(remaining, 250), "UPtr*", &result, "Ptr")
        throw Error("Target keyboard message failed or timed out")
}
