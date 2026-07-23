import ctypes
import sys
from ctypes import wintypes
from typing import Optional


TARGET_STARTGG_TOKEN = "TekkenVodHelper:start.gg-token"


class CredentialError(Exception):
    pass


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


def read_startgg_token() -> str:
    return read_credential(TARGET_STARTGG_TOKEN) or ""


def write_startgg_token(token: str) -> None:
    write_credential(TARGET_STARTGG_TOKEN, token)


def delete_startgg_token() -> None:
    delete_credential(TARGET_STARTGG_TOKEN)


def read_credential(target: str) -> Optional[str]:
    if not sys.platform.startswith("win"):
        return None
    advapi32 = ctypes.WinDLL("Advapi32.dll")
    advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]

    credential = ctypes.POINTER(CREDENTIALW)()
    if not advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(credential)):
        error = ctypes.GetLastError()
        if error == ERROR_NOT_FOUND:
            return None
        raise CredentialError("Could not read Windows credential {}: WinError {}".format(target, error))
    try:
        size = credential.contents.CredentialBlobSize
        if size <= 0:
            return ""
        blob = ctypes.string_at(credential.contents.CredentialBlob, size)
        return blob.decode("utf-16-le").rstrip("\x00")
    finally:
        advapi32.CredFree(credential)


def write_credential(target: str, secret: str) -> None:
    if not sys.platform.startswith("win"):
        raise CredentialError("Windows Credential Manager is only available on Windows.")
    advapi32 = ctypes.WinDLL("Advapi32.dll")
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL

    blob = secret.encode("utf-16-le")
    blob_buffer = ctypes.create_string_buffer(blob)
    credential = CREDENTIALW()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(blob_buffer, ctypes.POINTER(ctypes.c_byte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = "start.gg"

    if not advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise CredentialError("Could not write Windows credential {}: WinError {}".format(target, ctypes.GetLastError()))


def delete_credential(target: str) -> None:
    if not sys.platform.startswith("win"):
        return
    advapi32 = ctypes.WinDLL("Advapi32.dll")
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    if not advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        error = ctypes.GetLastError()
        if error != ERROR_NOT_FOUND:
            raise CredentialError("Could not delete Windows credential {}: WinError {}".format(target, error))
