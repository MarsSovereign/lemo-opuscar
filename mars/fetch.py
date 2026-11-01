"""Cross-platform front end for tools/fetch.sh (big assets from the GitHub "assets" release):

    python mars/fetch.py voice | hdri | instruments <lib> | instruments all

It runs the Python downloader embedded in tools/fetch.sh itself, so it always matches upstream's manifest and checks.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import LIB, child_env, python, utf8_console  # noqa: E402

LIBS = ['freepats', 'karoryfer', 'salamander', 'vcsl', 'vsco2ce']


def main() -> None:
    utf8_console()
    a = sys.argv[1:]
    if a[:1] in (['voice'], ['hdri']):
        packs = [a[0]]
    elif a[:1] == ['instruments'] and len(a) > 1 and (a[1] in LIBS or a[1] == 'all'):
        packs = [f'instruments-{x}' for x in (LIBS if a[1] == 'all' else [a[1]])]
    else:
        sys.exit(__doc__ + f'\nlibs: {" ".join(LIBS)}')
    src = (LIB / 'tools' / 'fetch.sh').read_text(encoding='utf-8')
    m = re.search(r"<<'PY'\n(.*?)\nPY\n", src, re.S)
    if not m:
        sys.exit('mars/fetch.py: could not find the downloader in tools/fetch.sh (did upstream change it?)')
    # the script is fed on stdin, as fetch.sh does; it uses curl when present (Windows 10+ ships curl.exe), else urllib
    r = subprocess.run([python(), '-', *packs], input=m.group(1).encode('utf-8'), cwd=str(LIB), env=child_env())
    sys.exit(r.returncode)


if __name__ == '__main__':
    main()
