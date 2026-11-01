"""Cross-platform installer for the library (Windows-native port of `setup.sh deps`; works on macOS/Linux too).

    python mars/setup.py                 check the machine, report what's missing
    python mars/setup.py deps [voice] [music]
                                          core = npm packages + headless browser + .venv (requirements.txt);
                                          voice = requirements-voice.txt (faster-whisper for the speech check; edge-tts; Kokoro);
                                          music = requirements-music.txt (numba)

Unlike setup.sh it never clones or updates the library: on Mars's PC the library is this git checkout.
Installed tiers are tracked by a checksum of their requirements file, so a changed file reinstalls.
Mirrors: set PIP_INDEX_URL / UV_INDEX_URL (e.g. Huawei Cloud) and npm_config_registry before running, if needed.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import LIB, WINDOWS, child_env, utf8_console, venv_python  # noqa: E402

NPM = 'npm.cmd' if WINDOWS else 'npm'


def sig(*files: Path) -> str:
    h = hashlib.sha1()
    for f in files:
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def req(tier: str) -> Path:
    return LIB / ('requirements.txt' if tier == 'core' else f'requirements-{tier}.txt')


def sh(cmd: list, **kw) -> None:
    print('»', ' '.join(map(str, cmd)), flush=True)
    r = subprocess.run([str(c) for c in cmd], cwd=str(LIB), env=child_env(), **kw)
    if r.returncode:
        sys.exit(f'✗ failed (exit {r.returncode}): {" ".join(map(str, cmd))}')


def find_python() -> str | None:
    """A Python 3.11-3.13 for the venv (kokoro-onnx in the voice tier needs < 3.14)."""
    cands = [['py', '-3.12'], ['py', '-3.13'], ['py', '-3.11']] if WINDOWS else []
    cands += [['python3.12'], ['python3.13'], ['python3.11'], ['python3'], ['python']]
    for c in cands:
        if not shutil.which(c[0]):
            continue
        try:
            r = subprocess.run(c + ['-c', 'import sys; print(sys.executable); sys.exit(not (3, 11) <= sys.version_info[:2] <= (3, 13))'],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def check() -> list[str]:
    miss = []
    node = shutil.which('node')
    if node:
        v = subprocess.run([node, '-p', 'process.versions.node.split(".")[0]'], capture_output=True, text=True).stdout.strip()
        if not v.isdigit() or int(v) < 20:
            miss.append(f'node20+ (found {v})')
    else:
        miss.append('node20+')
    ff = shutil.which('ffmpeg')
    if ff:
        line = subprocess.run([ff, '-version'], capture_output=True, text=True, encoding='utf-8', errors='replace').stdout.split('\n')[0]
        m = re.match(r'ffmpeg version n?(\d+)\.', line)
        if m and int(m.group(1)) < 7:
            miss.append(f'ffmpeg7+ (found {m.group(1)}.x; Windows: a gyan.dev or BtbN "full" build)')
    else:
        miss.append('ffmpeg (on PATH)')
    if not (shutil.which('uv') or find_python()):
        miss.append('python3.11-3.13 (or uv)')
    return miss


def node_ok() -> bool:
    m = LIB / 'node_modules' / '.lemo-ok'
    return m.exists() and m.read_text().strip() == sig(LIB / 'package.json', LIB / 'package-lock.json')


def py_ok(tier: str) -> bool:
    m = LIB / '.venv' / f'.lemo-{tier}'
    return m.exists() and m.read_text().strip() == sig(req(tier))


def deps(tiers: list[str]) -> None:
    if not shutil.which(NPM):
        sys.exit('✗ npm not found: install Node 20+ first')
    if not node_ok():
        sh([shutil.which(NPM), 'install', '--no-audit', '--no-fund', '--loglevel=error'])
        (LIB / 'node_modules' / '.lemo-ok').write_text(sig(LIB / 'package.json', LIB / 'package-lock.json'))
    sh([shutil.which('node'), 'node_modules/playwright-core/cli.js', 'install', 'chromium-headless-shell'])
    py = venv_python()
    if py:
        try:
            subprocess.run([str(py), '-c', 'pass'], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            print(f'! {LIB / ".venv"} is broken; making it again')
            shutil.rmtree(LIB / '.venv')
            py = None
    uv = shutil.which('uv')
    if not py:
        if uv:
            sh([uv, 'venv', '--quiet', '--python', '3.12', '.venv'])
        else:
            base = find_python() or sys.exit('✗ Python 3.11-3.13 is needed (or install uv, which fetches it)')
            sh([base, '-m', 'venv', '.venv'])
        py = venv_python()
    for t in tiers:
        if py_ok(t):
            continue
        if uv:
            sh([uv, 'pip', 'install', '--quiet', '--python', py, '-r', req(t)])
        else:
            sh([py, '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '-r', req(t)])
        (LIB / '.venv' / f'.lemo-{t}').write_text(sig(req(t)))
    print('✓ installed:', ', '.join(tiers))
    if 'voice' in tiers:
        print('  note: on this PC, narration comes from the local TTS service (mars/tts_local.py);'
              ' the Kokoro model is only needed for core/tts/tts.py: python mars/fetch.py voice')


def main() -> None:
    utf8_console()
    args = sys.argv[1:]
    if args and args[0] == 'deps':
        tiers = ['core']
        for t in args[1:]:
            if t not in ('voice', 'music'):
                sys.exit('usage: python mars/setup.py deps [voice] [music]')
            tiers.append(t)
        deps(tiers)
    elif args:
        sys.exit(__doc__)
    miss = check()
    if miss:
        print('! missing: ' + ', '.join(miss))
    if not (node_ok() and py_ok('core')):
        print("! packages not installed yet: run 'python mars/setup.py deps' before the first render")
    print(f'LIB={LIB}')


if __name__ == '__main__':
    main()
