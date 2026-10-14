"""Discover an ordinary global AHK v2 interpreter; probing never sends input."""
import base64
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess

PROTOCOL = 1
SETUP = ('Install AutoHotkey 2.0.30 or newer stable v2, 64-bit, for all users '
         '(see README). Use --ahk-exe or AVF_AHK_EXE for a custom install.')

class AhkSetupError(RuntimeError):
    pass

@dataclass(frozen=True)
class AhkRuntime:
    executable: Path
    version: str
    bitness: int

def hidden_process_options():
    if os.name != 'nt':
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return {'startupinfo': startup, 'creationflags': subprocess.CREATE_NO_WINDOW}

def _machine_dirs():
    if os.name != 'nt':
        return []
    import winreg
    result = []
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'Software\AutoHotkey', 0, winreg.KEY_READ | view) as key:
                directory = Path(winreg.QueryValueEx(key, 'InstallDir')[0])
                if directory not in result:
                    result.append(directory)
        except OSError:
            pass
    return result

def _candidates():
    for folder in _machine_dirs():
        yield folder / 'v2' / 'AutoHotkey64.exe'
    yield Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'AutoHotkey/v2/AutoHotkey64.exe'
    found = shutil.which('AutoHotkey64.exe')
    if found:
        yield Path(found)

def _verify(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or '_uia' in path.name.casefold():
        raise AhkSetupError(f'Invalid ordinary AHK executable: {path}. {SETUP}')
    try:
        result = subprocess.run([str(path), '/ErrorStdOut', str(Path(__file__).parent.joinpath('ahk/probe.ahk').resolve())],
                                shell=False, close_fds=True, capture_output=True, encoding='utf-8',
                                timeout=3, **hidden_process_options())
        fields = result.stdout.strip('\r\n\ufeff').split('\t')
        if result.returncode or len(fields) != 6 or fields[:5] != ['1','0','ok','runtime','not_started']:
            raise ValueError('invalid compatibility response')
        version, bits = base64.b64decode(fields[5], validate=True).decode('utf-8').split('\t')
        if not re.fullmatch(r'2\.\d+\.\d+', version):
            raise ValueError('requires stable AutoHotkey v2')
        if tuple(map(int, version.split('.'))) < (2,0,30) or int(bits) != 64:
            raise ValueError('requires AutoHotkey >= 2.0.30 x64')
        return AhkRuntime(path, version, int(bits))
    except (OSError, subprocess.SubprocessError, ValueError, UnicodeError) as error:
        raise AhkSetupError(f'AHK compatibility check failed for {path}: {error}. {SETUP}') from error

def resolve_ahk(executable: Path | None = None) -> AhkRuntime:
    override = executable if executable is not None else os.environ.get('AVF_AHK_EXE')
    if override is not None:
        return _verify(override)
    failures = []
    for candidate in _candidates():
        if candidate.is_file():
            try:
                return _verify(candidate)
            except AhkSetupError as error:
                failures.append(str(error))
    raise AhkSetupError(failures[-1] if failures else 'AutoHotkey was not found. ' + SETUP)


_READINESS = {}

def ready_ahk(executable: Path | None = None) -> AhkRuntime:
    """Cache positive read-only readiness by configuration and executable identity."""
    override = executable if executable is not None else os.environ.get('AVF_AHK_EXE')
    paths = [Path(override)] if override is not None else list(_candidates())
    identities = []
    for path in paths:
        try:
            stat = path.stat()
            identity = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            identity = None
        identities.append((str(path.resolve()),identity))
    key = tuple(identities)
    if key not in _READINESS:
        runtime = resolve_ahk(executable)
        _READINESS.clear()
        _READINESS[key] = runtime
    return _READINESS[key]
