#!/usr/bin/env python3
"""Build a user-installable GNOME extension ZIP without activating anything."""
import argparse
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / 'gnome-extension'
FILES = ('extension.js', 'battery-state.js', 'metadata.json')
UUID = 'battery@scientifica007.github.io'


def build(out_dir):
    meta = json.loads((EXT / 'metadata.json').read_text(encoding='utf-8'))
    assert meta['uuid'] == UUID and meta['shell-version'] == ['46']
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / (UUID + '.shell-extension.zip')
    with ZipFile(dest, 'w', ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(EXT / name, name)
    with ZipFile(dest) as archive:
        assert set(archive.namelist()) == set(FILES)
    print(dest)
    return dest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    build(args.output)
