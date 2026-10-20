from __future__ import annotations

import base64
import os


class SecretStorageError(RuntimeError):
    pass


def protect_secret(value: str) -> str:
    """Protect a credential for the current Windows user with DPAPI."""
    normalized = value.strip()
    if not normalized:
        raise SecretStorageError("Credential cannot be empty")
    if os.name != "nt":
        raise SecretStorageError(
            "Direct credential storage requires Windows DPAPI; use an environment variable"
        )
    protected = _crypt_protect(normalized.encode("utf-8"))
    return "dpapi:" + base64.b64encode(protected).decode("ascii")


def reveal_secret(value: str | None) -> str:
    if not value:
        return ""
    if not value.startswith("dpapi:"):
        raise SecretStorageError("Unsupported protected credential format")
    if os.name != "nt":
        raise SecretStorageError("Windows DPAPI is unavailable")
    try:
        protected = base64.b64decode(value.removeprefix("dpapi:"), validate=True)
        return _crypt_unprotect(protected).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise SecretStorageError("Stored credential is invalid") from exc


def _crypt_protect(payload: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = (
        ctypes.POINTER(DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    )
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.LocalFree.restype = wintypes.HLOCAL

    buffer = ctypes.create_string_buffer(payload)
    source = DataBlob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    destination = DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "Video2Doc credential",
        None,
        None,
        None,
        0x01,
        ctypes.byref(destination),
    ):
        raise SecretStorageError(str(ctypes.WinError(ctypes.get_last_error())))
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(destination.pbData)


def _crypt_unprotect(payload: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = (
        ctypes.POINTER(DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    )
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.LocalFree.restype = wintypes.HLOCAL

    buffer = ctypes.create_string_buffer(payload)
    source = DataBlob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    destination = DataBlob()
    description = wintypes.LPWSTR()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        ctypes.byref(description),
        None,
        None,
        None,
        0x01,
        ctypes.byref(destination),
    ):
        raise SecretStorageError(str(ctypes.WinError(ctypes.get_last_error())))
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        if description:
            kernel32.LocalFree(description)
        kernel32.LocalFree(destination.pbData)
