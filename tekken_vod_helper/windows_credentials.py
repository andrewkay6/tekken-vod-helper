import ctypes
import json
import os
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Optional


TARGET_STARTGG_TOKEN = "TekkenVodHelper:start.gg-token"
TARGET_YOUTUBE_CLIENT_ID = "TekkenVodHelper:youtube-client-id"
TARGET_YOUTUBE_CLIENT_SECRET = "TekkenVodHelper:youtube-client-secret"
TARGET_YOUTUBE_REFRESH_TOKEN = "TekkenVodHelper:youtube-refresh-token"


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

#: Off Windows (the devbox), secrets live in one JSON file keyed by the same target names, readable
#: only by its owner. $TVH_CREDENTIALS_FILE overrides the location.
CREDENTIALS_FILE_ENV = "TVH_CREDENTIALS_FILE"


def credentials_file() -> Path:
    override = os.environ.get(CREDENTIALS_FILE_ENV)
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "tvh" / "credentials.json"


def _read_file_store() -> dict:
    path = credentials_file()
    if not path.exists():
        return {}
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise CredentialError("{} is readable by other users; run chmod 600 on it".format(path))
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        raise CredentialError("{} must hold a JSON object".format(path))
    return data


def _write_file_store(data: dict) -> None:
    path = credentials_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(prefix=".credentials-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_startgg_token() -> str:
    return read_credential(TARGET_STARTGG_TOKEN) or ""


def write_startgg_token(token: str) -> None:
    write_credential(TARGET_STARTGG_TOKEN, token)


def delete_startgg_token() -> None:
    delete_credential(TARGET_STARTGG_TOKEN)


def read_youtube_client_id() -> str:
    return read_credential(TARGET_YOUTUBE_CLIENT_ID) or ""


def read_youtube_client_secret() -> str:
    return read_credential(TARGET_YOUTUBE_CLIENT_SECRET) or ""


def read_youtube_refresh_token() -> str:
    return read_credential(TARGET_YOUTUBE_REFRESH_TOKEN) or ""


def write_youtube_refresh_token(token: str) -> None:
    write_credential(TARGET_YOUTUBE_REFRESH_TOKEN, token)


def delete_youtube_refresh_token() -> None:
    delete_credential(TARGET_YOUTUBE_REFRESH_TOKEN)


def write_youtube_client_credentials(client_id: str, client_secret: str) -> None:
    write_credential(TARGET_YOUTUBE_CLIENT_ID, client_id)
    write_credential(TARGET_YOUTUBE_CLIENT_SECRET, client_secret)


def read_credential(target: str) -> Optional[str]:
    if not sys.platform.startswith("win"):
        value = _read_file_store().get(target)
        return None if value is None else str(value)
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
        data = _read_file_store()
        data[target] = secret
        _write_file_store(data)
        return
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
        data = _read_file_store()
        if data.pop(target, None) is not None:
            _write_file_store(data)
        return
    advapi32 = ctypes.WinDLL("Advapi32.dll")
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    if not advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        error = ctypes.GetLastError()
        if error != ERROR_NOT_FOUND:
            raise CredentialError("Could not delete Windows credential {}: WinError {}".format(target, error))
