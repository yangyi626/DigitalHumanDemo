"""Build an immutable, self-contained web release; activation is a separate step."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare(source=ROOT):
    inputs = [(p.relative_to(source).as_posix(), p.read_bytes())
              for folder in ('web', 'avatar') for p in sorted((source/folder).rglob('*')) if p.is_file()]
    digest = hashlib.sha256()
    for name, content in inputs:
        digest.update(name.encode() + b'\0' + content)
    version = digest.hexdigest()[:16]
    releases = ROOT/'releases'
    releases.mkdir(exist_ok=True)
    releases.chmod(0o755)
    destination = releases/version
    if destination.exists():
        return version
    staging = Path(tempfile.mkdtemp(prefix='.building-', dir=releases))
    try:
        for folder in ('web','avatar'):
            shutil.copytree(source/folder, staging/folder)
        index = staging/'web/index.html'
        text = index.read_text().replace('assets/', f'releases/{version}/web/')
        text = text.replace('<html lang="zh-CN">', f'<html lang="zh-CN" data-release="{version}">')
        index.write_text(text)
        demo = staging/'web/demo.js'
        text = demo.read_text()
        needle = 'avatar.src = `avatar/frame.html'
        if text.count(needle) != 1:
            raise RuntimeError('Cannot safely version the avatar entry path')
        demo.write_text(text.replace(needle, f'avatar.src = `releases/{version}/avatar/frame.html'))
        for file in staging.rglob('*.js'):
            subprocess.run(['node','--check',str(file)],check=True,capture_output=True)
        manifest = {p.relative_to(staging).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in staging.rglob('*') if p.is_file()}
        (staging/'manifest.json').write_text(json.dumps({'version':version,'files':manifest},indent=2))
        staging.chmod(0o755)
        for path in staging.rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)
        staging.rename(destination)
        return version
    finally:
        if staging.exists(): shutil.rmtree(staging)


def activate(version):
    if len(version)!=16 or any(c not in '0123456789abcdef' for c in version):
        raise ValueError('Invalid release ID')
    release=ROOT/'releases'/version
    manifest=json.loads((release/'manifest.json').read_text())
    for name,digest in manifest['files'].items():
        if hashlib.sha256((release/name).read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Release integrity check failed')
    marker=ROOT/'active-release'
    temporary=ROOT/'active-release.next'
    with temporary.open('w') as stream:
        stream.write(version+'\n'); stream.flush(); os.fsync(stream.fileno())
    temporary.chmod(0o644)
    temporary.replace(marker)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--activate',metavar='RELEASE')
    parser.add_argument('--source',type=Path,default=ROOT)
    options=parser.parse_args()
    if options.activate:
        activate(options.activate);print(options.activate)
    else:
        print(prepare(options.source))
