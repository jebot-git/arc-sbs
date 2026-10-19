#!/usr/bin/env python3
"""Build the wheel, sdist and a portable user-install bundle (no model weights)."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import tomllib


def main():
    root = Path(__file__).resolve().parent.parent
    version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    subprocess.run(['uv', 'build'], cwd=root, check=True)
    subprocess.run(['uv', 'export', '--locked', '--extra', 'gui', '--no-dev',
                    '--no-emit-project', '--no-header', '--no-annotate',
                    '--output-file', 'packaging/runtime-requirements.txt'], cwd=root, check=True,
                   stdout=subprocess.DEVNULL)
    wheel = root / f'dist/arc_sbs-{version}-py3-none-any.whl'
    bundle = root / f'dist/arc-sbs-{version}-linux-user.tar.gz'
    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary) / f'arc-sbs-{version}'
        staging.mkdir()
        for source in [wheel, root / 'LICENSE', root / 'THIRD_PARTY_NOTICES.md',
                       *(root / 'packaging' / name for name in
                         ('install.py', 'arc-sbs.svg', 'runtime-requirements.txt', 'INSTALL.md'))]:
            shutil.copy2(source, staging / source.name)
        # Optional native viewer integration carries its own GPLv3 patch/license.
        shutil.copytree(root / 'integrations/qimgv', staging / 'qimgv',
                        ignore=shutil.ignore_patterns('__pycache__'))
        with tarfile.open(bundle, 'w:gz') as archive:
            archive.add(staging, arcname=staging.name)
    for artifact in (wheel, root / f'dist/arc_sbs-{version}.tar.gz', bundle):
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact.with_name(artifact.name + '.sha256').write_text(f'{digest}  {artifact.name}\n')
    print(bundle)


if __name__ == '__main__':
    main()
