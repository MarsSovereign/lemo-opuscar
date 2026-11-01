"""Shared settings for the mars/ tools: where the library is, which Python, which local services.

Settings come from, in order: environment variables, mars/config.json (local, not in git), then the defaults below.
Copy mars/config.example.json to mars/config.json on each machine and edit it there.
Standard library only, so every script here runs with any Python 3.9+ (the library's .venv or the system one).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent          # the Lemo-Opuscar library root
MARS = LIB / 'mars'
WINDOWS = os.name == 'nt'

DEFAULTS = {
    'tts_url': 'http://127.0.0.1:9880',                 # OpenMontage services/local_tts (IndexTTS2.5 / CosyVoice3)
    'tts_voice': '',                                     # voice name in LOCAL_TTS_VOICES_DIR; '' = the service default
    'comfy_url': 'http://127.0.0.1:8189',                # our ComfyUI (8188 is Mars's own)
    'whisper_model': '',                                 # local faster-whisper folder, e.g. H:\\win-ai\\whisper\\...; '' = core default
    'whisper_python': '',                                # interpreter with faster-whisper; '' = the library .venv
    'openmontage_dir': '',                               # e.g. H:\\win-ai\\OpenMontage (only for the OpenMontage bridge)
    'ffmpeg': 'ffmpeg',
    'ffprobe': 'ffprobe',
}
ENV = {
    'tts_url': 'LEMO_TTS_URL',
    'tts_voice': 'LEMO_TTS_VOICE',
    'comfy_url': 'LEMO_COMFY_URL',
    'whisper_model': 'WHISPER_MODEL',
    'openmontage_dir': 'OPENMONTAGE_DIR',
    'ffmpeg': 'LEMO_FFMPEG',
    'ffprobe': 'LEMO_FFPROBE',
}


def config() -> dict:
    cfg = dict(DEFAULTS)
    f = MARS / 'config.json'
    if f.is_file():
        try:
            cfg.update({k: v for k, v in json.loads(f.read_text(encoding='utf-8')).items() if not k.startswith('_')})
        except json.JSONDecodeError as e:
            sys.exit(f'mars/config.json is not valid JSON: {e}')
    for k, var in ENV.items():
        if os.environ.get(var):
            cfg[k] = os.environ[var]
    return cfg


def venv_python() -> Path | None:
    """The library's own Python, if its .venv exists (Windows: Scripts\\python.exe, elsewhere bin/python)."""
    p = LIB / '.venv' / ('Scripts/python.exe' if WINDOWS else 'bin/python')
    return p if p.exists() else None


def python() -> str:
    p = venv_python()
    return str(p) if p else sys.executable


def child_env(**extra: str) -> dict:
    """Environment for child processes: UTF-8 everywhere (Windows otherwise opens text files as GBK and core scripts
    that read Chinese JSON without an explicit encoding break), plus the configured Whisper model."""
    env = dict(os.environ)
    env.setdefault('PYTHONUTF8', '1')
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    wm = config().get('whisper_model')
    if wm:
        env.setdefault('WHISPER_MODEL', wm)
    env.update(extra)
    return env


def tool(name: str) -> str:
    """Path of ffmpeg / ffprobe / node, or exit with a clear message."""
    want = config().get(name, name)
    found = shutil.which(want)
    if not found:
        sys.exit(f'{name} not found ({want!r}). Install it and put it on PATH, or set "{name}" in mars/config.json.')
    return found


def run(cmd: list, cwd: Path | str | None = None, **kw) -> int:
    """Run a command with the UTF-8 environment, streaming its output; returns the exit code."""
    print('»', ' '.join(str(c) for c in cmd), flush=True)
    return subprocess.call([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=child_env(), **kw)


def utf8_console() -> None:
    """Make print() safe for Chinese text on a Windows console."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
