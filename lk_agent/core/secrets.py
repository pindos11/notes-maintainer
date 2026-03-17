from __future__ import annotations

import ctypes
import json
import os
import stat
import sys
from ctypes import POINTER, Structure, WinDLL, byref, cast
from ctypes.wintypes import DWORD, FILETIME, LPWSTR
from pathlib import Path

from lk_agent.core.models import AppConfig, TelegramConfig


DEFAULT_TELEGRAM_TARGET = "telegram-bot"
ENV_PROVIDER = "env"
WINCRED_PROVIDER = "wincred"
FILE_PROVIDER = "file"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
ENV_TELEGRAM_TOKEN = "LK_AGENT_TELEGRAM_TOKEN"


class SecretStoreError(RuntimeError):
    """Raised when secret storage is unavailable or fails."""


class CREDENTIAL_ATTRIBUTEW(Structure):
    _fields_ = [
        ("Keyword", LPWSTR),
        ("Flags", DWORD),
        ("ValueSize", DWORD),
        ("Value", ctypes.c_void_p),
    ]


class CREDENTIALW(Structure):
    _fields_ = [
        ("Flags", DWORD),
        ("Type", DWORD),
        ("TargetName", LPWSTR),
        ("Comment", LPWSTR),
        ("LastWritten", FILETIME),
        ("CredentialBlobSize", DWORD),
        ("CredentialBlob", ctypes.c_void_p),
        ("Persist", DWORD),
        ("AttributeCount", DWORD),
        ("Attributes", POINTER(CREDENTIAL_ATTRIBUTEW)),
        ("TargetAlias", LPWSTR),
        ("UserName", LPWSTR),
    ]


if sys.platform == "win32":
    advapi32 = WinDLL("Advapi32.dll")
    kernel32 = WinDLL("Kernel32.dll")
    CredWriteW = advapi32.CredWriteW
    CredWriteW.argtypes = [POINTER(CREDENTIALW), DWORD]
    CredWriteW.restype = ctypes.c_int
    CredReadW = advapi32.CredReadW
    CredReadW.argtypes = [LPWSTR, DWORD, DWORD, POINTER(POINTER(CREDENTIALW))]
    CredReadW.restype = ctypes.c_int
    CredDeleteW = advapi32.CredDeleteW
    CredDeleteW.argtypes = [LPWSTR, DWORD, DWORD]
    CredDeleteW.restype = ctypes.c_int
    CredFree = advapi32.CredFree
    CredFree.argtypes = [ctypes.c_void_p]
    CredFree.restype = None
    GetLastError = kernel32.GetLastError
    GetLastError.argtypes = []
    GetLastError.restype = DWORD
else:
    advapi32 = None


def default_telegram_target() -> str:
    return DEFAULT_TELEGRAM_TARGET


def default_provider() -> str:
    if sys.platform == "win32":
        return WINCRED_PROVIDER
    return FILE_PROVIDER


def supported_providers() -> list[str]:
    providers = [ENV_PROVIDER, FILE_PROVIDER]
    if sys.platform == "win32":
        providers.append(WINCRED_PROVIDER)
    return providers


def provider_supported(provider: str) -> bool:
    return provider in supported_providers()


def secret_file_path(config: AppConfig, root: Path | None = None) -> Path:
    base = root or Path.cwd()
    return config.resolved_data_dir(base) / "secrets.json"


def store_telegram_token(config: AppConfig, token: str, provider: str | None = None, target: str | None = None, root: Path | None = None) -> tuple[str, str]:
    chosen_provider = provider or config.telegram.bot_token_provider or default_provider()
    chosen_target = target or config.telegram.bot_token_ref or default_telegram_target()
    store_secret(config, chosen_provider, chosen_target, token, root=root)
    return chosen_provider, chosen_target


def clear_telegram_token(config: AppConfig, root: Path | None = None) -> bool:
    provider = config.telegram.bot_token_provider
    target = config.telegram.bot_token_ref
    if not provider or not target:
        return False
    return delete_secret(config, provider, target, root=root)


def resolve_telegram_token(config: TelegramConfig, app_config: AppConfig | None = None, root: Path | None = None) -> str | None:
    env_token = os.getenv(ENV_TELEGRAM_TOKEN)
    if env_token:
        return env_token
    if config.bot_token_provider and config.bot_token_ref:
        return read_secret(app_config, config.bot_token_provider, config.bot_token_ref, root=root)
    if config.bot_token:
        return config.bot_token
    return None


def store_secret(config: AppConfig, provider: str, target: str, secret: str, root: Path | None = None) -> None:
    ensure_provider(provider)
    if provider == WINCRED_PROVIDER:
        store_secret_wincred(target, secret)
        return
    if provider == FILE_PROVIDER:
        store_secret_file(secret_file_path(config, root), target, secret)
        return
    if provider == ENV_PROVIDER:
        raise SecretStoreError(f"provider '{ENV_PROVIDER}' is read-only; set {ENV_TELEGRAM_TOKEN} in the environment instead")
    raise SecretStoreError(f"unsupported secret provider: {provider}")


def read_secret(config: AppConfig | None, provider: str, target: str, root: Path | None = None) -> str | None:
    ensure_provider(provider)
    if provider == WINCRED_PROVIDER:
        return read_secret_wincred(target)
    if provider == FILE_PROVIDER:
        if config is None:
            raise SecretStoreError("file-based secret lookup requires app config")
        return read_secret_file(secret_file_path(config, root), target)
    if provider == ENV_PROVIDER:
        return os.getenv(ENV_TELEGRAM_TOKEN)
    raise SecretStoreError(f"unsupported secret provider: {provider}")


def delete_secret(config: AppConfig, provider: str, target: str, root: Path | None = None) -> bool:
    ensure_provider(provider)
    if provider == WINCRED_PROVIDER:
        return delete_secret_wincred(target)
    if provider == FILE_PROVIDER:
        return delete_secret_file(secret_file_path(config, root), target)
    if provider == ENV_PROVIDER:
        return False
    raise SecretStoreError(f"unsupported secret provider: {provider}")


def ensure_provider(provider: str) -> None:
    if not provider_supported(provider):
        raise SecretStoreError(f"secret provider '{provider}' is not supported on this system")


def store_secret_wincred(target: str, secret: str, username: str = "telegram-bot") -> None:
    secret_bytes = secret.encode("utf-16-le")
    blob = ctypes.create_string_buffer(secret_bytes)
    credential = CREDENTIALW()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.CredentialBlobSize = len(secret_bytes)
    credential.CredentialBlob = cast(blob, ctypes.c_void_p)
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = username
    if not CredWriteW(byref(credential), 0):
        raise SecretStoreError(f"CredWriteW failed with error {int(GetLastError())}")


def read_secret_wincred(target: str) -> str | None:
    credential_ptr = POINTER(CREDENTIALW)()
    if not CredReadW(target, CRED_TYPE_GENERIC, 0, byref(credential_ptr)):
        error = int(GetLastError())
        if error == ERROR_NOT_FOUND:
            return None
        raise SecretStoreError(f"CredReadW failed with error {error}")
    try:
        credential = credential_ptr.contents
        if not credential.CredentialBlob or credential.CredentialBlobSize == 0:
            return ""
        raw = ctypes.string_at(credential.CredentialBlob, int(credential.CredentialBlobSize))
        return raw.decode("utf-16-le")
    finally:
        CredFree(credential_ptr)


def delete_secret_wincred(target: str) -> bool:
    if CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        return True
    error = int(GetLastError())
    if error == ERROR_NOT_FOUND:
        return False
    raise SecretStoreError(f"CredDeleteW failed with error {error}")


def store_secret_file(path: Path, target: str, secret: str) -> None:
    data = load_secret_file(path)
    data[target] = secret
    write_secret_file(path, data)


def read_secret_file(path: Path, target: str) -> str | None:
    data = load_secret_file(path)
    return data.get(target)


def delete_secret_file(path: Path, target: str) -> bool:
    data = load_secret_file(path)
    if target not in data:
        return False
    del data[target]
    write_secret_file(path, data)
    return True


def load_secret_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_secret_file(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tighten_file_permissions(path)


def tighten_file_permissions(path: Path) -> None:
    try:
        if os.name == "posix":
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        raise SecretStoreError(f"failed to secure secret file permissions: {exc}") from exc
