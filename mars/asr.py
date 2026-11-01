"""Speech check with the PC's own Whisper model: a thin front end for core/tts/asr_check.py.

    python mars/asr.py lines.json voices_dir [--lang zh] [any other asr_check.py flag]

Adds what core/tts/asr_check.py can't know: the local model folder (mars/config.json "whisper_model", e.g. the
faster-whisper large-v3-turbo under H:\\win-ai\\whisper) and, if set, "whisper_python", an interpreter that already has
faster-whisper installed (otherwise the library's .venv, voice tier). Defaults to --lang zh. Same exit codes as asr_check.py:
0 all lines pass, 1 some lines differ (resynthesise them: tts_local.py --redo <ids>, or a new "take"), 2 the check could not run.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import LIB, child_env, config, python, utf8_console  # noqa: E402


def main() -> None:
    utf8_console()
    args = sys.argv[1:]
    if len(args) < 2 or args[0] in ('-h', '--help'):
        sys.exit(__doc__)
    if '--lang' not in args:
        args += ['--lang', 'zh']
    py = config().get('whisper_python') or python()
    cmd = [py, str(LIB / 'core' / 'tts' / 'asr_check.py'), *args]
    print('»', ' '.join(cmd), flush=True)
    sys.exit(subprocess.call(cmd, env=child_env()))


if __name__ == '__main__':
    main()
