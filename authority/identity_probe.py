"""Isolated OS-identity probe.

Answers one question, with no other concerns:
    given a named-pipe connection handle -> (client_pid, principal) or ("", "").

Kept separate from the server so it can be tested on its own. Every path that
cannot produce a verified identity returns empty, and callers fail closed.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes as wt

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
adv = ctypes.WinDLL("advapi32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenUser = 1

k32.GetNamedPipeClientProcessId.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
k32.GetNamedPipeClientProcessId.restype = wt.BOOL
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.CloseHandle.restype = wt.BOOL
k32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)
]
k32.QueryFullProcessImageNameW.restype = wt.BOOL

adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
adv.OpenProcessToken.restype = wt.BOOL
adv.GetTokenInformation.argtypes = [
    wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)
]
adv.GetTokenInformation.restype = wt.BOOL
adv.LookupAccountSidW.argtypes = [
    wt.LPCWSTR, wt.LPVOID, wt.LPWSTR, ctypes.POINTER(wt.DWORD),
    wt.LPWSTR, ctypes.POINTER(wt.DWORD),
]
adv.LookupAccountSidW.restype = wt.BOOL


def client_pid(handle: int) -> int:
    pid = wt.DWORD(0)
    if k32.GetNamedPipeClientProcessId(handle, ctypes.byref(pid)):
        return int(pid.value)
    return 0


def process_image(pid: int) -> str:
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wt.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k32.CloseHandle(h)


def token_sid(pid: int) -> str:
    """Windows SID string (e.g. S-1-5-21-...-1001) owning `pid`.

    SID bytes are parsed directly rather than resolved to an account name.
    LookupAccountSidW and ConvertSidToStringSidW both fail on this host
    (ERROR_INVALID_PARAMETER / 1337), and a SID is the stronger authority
    primitive anyway: it is immutable and cannot be renamed or spoofed by a
    caller, whereas a display name is neither.
    """
    if not pid:
        return ""
    ph = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not ph:
        return ""
    tok = wt.HANDLE()
    try:
        if not adv.OpenProcessToken(ph, TOKEN_QUERY, ctypes.byref(tok)):
            return ""
        need = wt.DWORD(0)
        adv.GetTokenInformation(tok, TokenUser, None, 0, ctypes.byref(need))
        if not need.value:
            return ""
        buf = ctypes.create_string_buffer(need.value)
        if not adv.GetTokenInformation(tok, TokenUser, buf, need, ctypes.byref(need)):
            return ""
        raw = bytes(buf)
        # TOKEN_USER { SID_AND_ATTRIBUTES User; }
        # SID_AND_ATTRIBUTES { PSID Sid(8,64-bit); DWORD Attributes(4); pad(4); SID inline }
        offset = 8 + 4 + (4 if ctypes.sizeof(ctypes.c_void_p) == 8 else 0)
        sid = raw[offset:]
        if len(sid) < 8:
            return ""
        revision = sid[0]
        count = sid[1]
        if revision not in (1, 2) or count > 15:
            return ""
        authority = int.from_bytes(sid[2:8], "big")
        parts = [str(revision), str(authority)]
        for i in range(count):
            parts.append(str(int.from_bytes(sid[8 + i * 4: 12 + i * 4], "little")))
        return "S-" + "-".join(parts)
    finally:
        if tok:
            k32.CloseHandle(tok)
        k32.CloseHandle(ph)


def resolve(handle: int) -> tuple[int, str]:
    """The only entry point the server should use."""
    pid = client_pid(int(handle))
    if not pid:
        return 0, ""
    return pid, token_sid(pid)


if __name__ == "__main__":
    me = os.getpid()
    print("self_pid=" + str(me))
    print("self_image=" + process_image(me))
    print("self_sid=" + token_sid(me))
    print("sid_wellformed=" + str(token_sid(me).startswith("S-1-5-21-")))
    print("whoami=" + os.popen("whoami").read().strip())