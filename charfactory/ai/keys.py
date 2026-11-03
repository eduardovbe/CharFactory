"""Chaves de API dos provedores, guardadas no cofre do sistema (nunca no config.json): o
Gerenciador de Credenciais no Windows, as Chaves (Keychain) no macOS e o Secret Service
(secret-tool, do libsecret) no Linux. Sem cofre disponível, a chave vale só durante a sessão; sem
chave guardada, vale a variável de ambiente do provedor (ANTHROPIC_API_KEY, OPENAI_API_KEY,
GEMINI_API_KEY…)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ENV = {"anthropic": ("ANTHROPIC_API_KEY",), "openai": ("OPENAI_API_KEY",),
       "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "compatible": ("OPENAI_COMPATIBLE_API_KEY",)}
_memory: dict[str, str] = {}          # sem cofre: só durante a sessão
SERVICE = "CharFactory"
ACCOUNT = "api-key"


def _target(provider: str) -> str:
    return f"{SERVICE}/{provider}"


# ---------------------------------------------------------------- Windows

def _credapi():
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR)]
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    adv.CredFree.argtypes = [ctypes.c_void_p]
    return ctypes, CREDENTIAL, adv


CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE = 1, 2


def _win_read(provider: str) -> str:
    ctypes, CREDENTIAL, adv = _credapi()
    p = ctypes.POINTER(CREDENTIAL)()
    if not adv.CredReadW(_target(provider), CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
        return ""
    try:
        c = p.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
    finally:
        adv.CredFree(p)


def _win_write(provider: str, key: str) -> None:
    ctypes, CREDENTIAL, adv = _credapi()
    blob = key.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    c = CREDENTIAL(Type=CRED_TYPE_GENERIC, TargetName=_target(provider), CredentialBlobSize=len(blob),
                   CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)),
                   Persist=CRED_PERSIST_LOCAL_MACHINE, UserName=ACCOUNT)
    if not adv.CredWriteW(ctypes.byref(c), 0):
        raise OSError(ctypes.get_last_error(), "CredWriteW failed")


def _win_delete(provider: str) -> None:
    _, _, adv = _credapi()
    adv.CredDeleteW(_target(provider), CRED_TYPE_GENERIC, 0)


# ---------------------------------------------------------------- macOS e Linux (ferramenta do sistema)

def _run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=15)


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _mac_read(provider: str) -> str:
    r = _run(["security", "find-generic-password", "-s", _target(provider), "-a", ACCOUNT, "-w"])
    return r.stdout.rstrip("\n") if r.returncode == 0 else ""


def _mac_write(provider: str, key: str) -> None:
    # pelo modo interativo (stdin), para a chave não aparecer na lista de processos
    cmd = f"add-generic-password -U -s {_quote(_target(provider))} -a {ACCOUNT} -w {_quote(key)}\n"
    r = _run(["security", "-i"], cmd)
    if r.returncode or "error" in (r.stderr or "").lower():
        raise OSError(r.returncode or 1, (r.stderr or "security failed").strip())


def _mac_delete(provider: str) -> None:
    _run(["security", "delete-generic-password", "-s", _target(provider), "-a", ACCOUNT])


def _linux_attrs(provider: str) -> list[str]:
    return ["service", SERVICE, "provider", provider]


def _linux_read(provider: str) -> str:
    r = _run(["secret-tool", "lookup", *_linux_attrs(provider)])
    return r.stdout.rstrip("\n") if r.returncode == 0 else ""


def _linux_write(provider: str, key: str) -> None:
    r = _run(["secret-tool", "store", f"--label=Char Factory ({provider})", *_linux_attrs(provider)], key)
    if r.returncode:
        raise OSError(r.returncode, (r.stderr or "secret-tool failed").strip())


def _linux_delete(provider: str) -> None:
    _run(["secret-tool", "clear", *_linux_attrs(provider)])


def _backend():
    """(ler, gravar, apagar) do cofre deste sistema, ou None se não houver."""
    if sys.platform == "win32":
        return _win_read, _win_write, _win_delete
    if sys.platform == "darwin" and shutil.which("security"):
        return _mac_read, _mac_write, _mac_delete
    if shutil.which("secret-tool"):
        return _linux_read, _linux_write, _linux_delete
    return None


# ---------------------------------------------------------------- interface

def _read(provider: str) -> str:
    if provider in _memory:
        return _memory[provider]
    b = _backend()
    if not b:
        return ""
    try:
        return b[0](provider)
    except (OSError, subprocess.SubprocessError):
        return ""


def get(provider: str) -> str:
    try:
        key = _read(provider)
    except OSError:
        key = ""
    return key or next((os.environ[v] for v in ENV.get(provider, ()) if os.environ.get(v)), "")


def from_env(provider: str) -> bool:
    """A chave em uso vem de variável de ambiente (nada guardado)?"""
    try:
        return not _read(provider) and bool(get(provider))
    except OSError:
        return bool(get(provider))


def set(provider: str, key: str) -> None:  # noqa: A001
    key = key.strip()
    if not key:
        delete(provider)
        return
    b = _backend()
    if b is None:
        _memory[provider] = key
        return
    if sys.platform == "win32":
        b[1](provider, key)                # no Windows o erro sobe (o cofre sempre existe)
        _memory.pop(provider, None)
        return
    try:
        b[1](provider, key)
        _memory.pop(provider, None)
    except (OSError, subprocess.SubprocessError):
        _memory[provider] = key            # cofre trancado ou sem serviço: vale na sessão


def delete(provider: str) -> None:
    _memory.pop(provider, None)
    b = _backend()
    if b:
        try:
            b[2](provider)
        except (OSError, subprocess.SubprocessError):
            pass
