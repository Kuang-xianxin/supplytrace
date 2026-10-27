"""Project commands with an allowlisted environment; no inherited model secrets."""
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def clean_env():
    allowed = {'SYSTEMROOT','WINDIR','PATH','PATHEXT','TEMP','TMP','COMSPEC','LOCALAPPDATA','APPDATA','USERPROFILE','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMDATA','VIRTUAL_ENV','PYTHONUTF8','PYTHONIOENCODING'}
    env = {k:v for k,v in os.environ.items() if k.upper() in allowed}
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', SUPPLYTRACE_DISABLE_DOTENV='true', PYTHONPATH=str(ROOT))
    return env

if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        raise SystemExit('Usage: python scripts/safe_run.py <command> [arguments]')
    env = clean_env()
    if any(part in ('pytest', 'pytest.exe') or part.endswith('/pytest') or part.endswith('\\pytest.exe') for part in args):
        # Importing supplytrace.app constructs a default app. Keep that initialization
        # away from the running user's database even when tests pass explicit Settings.
        env['SUPPLYTRACE_RUNTIME'] = str(ROOT/'verification'/'test-startup'/str(uuid.uuid4()))
    result = subprocess.run(args, cwd=ROOT, env=env)
    raise SystemExit(result.returncode)
