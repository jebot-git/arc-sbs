#!/usr/bin/env python3
"""Build one GPU backend in isolation; never bundle weights or modify the checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib

BACKENDS = {
    'intel': None,
    'amd': ('2.8.0+rocm6.4', 'https://download.pytorch.org/whl/rocm6.4'),
    'nvidia': ('2.8.0+cu128', 'https://download.pytorch.org/whl/cu128'),
}


def stage_source(root, staging, backend):
    """Copy only source inputs, then freeze backend and dependency provenance."""
    for name in ('src', 'tests', 'scripts', 'docs', 'packaging', 'integrations'):
        shutil.copytree(root / name, staging / name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('pyproject.toml', 'uv.lock', 'README.md', 'LICENSE',
                 'THIRD_PARTY_NOTICES.md', 'AGENTS.md', '.gitignore'):
        shutil.copy2(root / name, staging / name)
    (staging / 'src/arc_sbs/_build_backend.py').write_text(f'BACKEND = {backend!r}\n')
    config_path = staging / 'pyproject.toml'
    text = config_path.read_text()
    config = tomllib.loads(text)
    dependencies = [d for d in config['project']['dependencies']
                    if not d.startswith(('openvino', 'torch', 'pytorch-triton-rocm'))]
    # Normalize a previously generated source distribution before selecting its
    # backend again, so source archives can be rebuilt or switched as well.
    text = re.sub(r'\n\[tool.arc-sbs\]\nbackend = [^\n]*\n', '\n', text)
    text = re.sub(r'\n\[tool.uv\]\nenvironments = [^\n]*\n', '\n', text)
    text = re.sub(r'^pytorch-triton-rocm = [^\n]*\n', '', text, flags=re.MULTILINE)
    text = text.replace('pytorch-runtime', 'pytorch-cpu')
    text = re.sub(r'https://download.pytorch.org/whl/(?:cpu|rocm6\.4|cu128)',
                  'https://download.pytorch.org/whl/cpu', text)
    if BACKENDS[backend]:
        version, index = BACKENDS[backend]
        dependencies.append(f'torch=={version}')
        text = text.replace('https://download.pytorch.org/whl/cpu', index)
        text = text.replace('pytorch-cpu', 'pytorch-runtime')
        if backend == 'amd':
            dependencies.append('pytorch-triton-rocm==3.4.0')
            text = text.replace('[tool.uv.sources]', '[tool.uv.sources]\npytorch-triton-rocm = { index = "pytorch-runtime" }')
        text += '\n[tool.uv]\nenvironments = ["sys_platform == \'linux\' and platform_machine == \'x86_64\'"]\n'
    else:
        dependencies.append('openvino>=2025.4,<2027')
    text = re.sub(r'^dependencies = .*$', 'dependencies = ' + json.dumps(dependencies),
                  text, count=1, flags=re.MULTILINE)
    text += '\n[tool.arc-sbs]\nbackend = ' + json.dumps(backend) + '\n'
    config_path.write_text(text)


def main():
    root = Path(__file__).resolve().parent.parent
    config = tomllib.loads((root / 'pyproject.toml').read_text())
    default_backend = config.get('tool', {}).get('arc-sbs', {}).get('backend', 'intel')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=BACKENDS, default=default_backend)
    args = parser.parse_args()
    version = config['project']['version']
    output = root / 'dist' / args.backend
    output.mkdir(parents=True, exist_ok=True)
    bundle = output / f'arc-sbs-{version}-{args.backend}-linux-user.tar.gz'
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / 'source'
        source.mkdir()
        stage_source(root, source, args.backend)
        if args.backend != default_backend or args.backend != 'intel':
            subprocess.run(['uv', 'lock'], cwd=source, check=True)
        requirements = source / 'packaging/runtime-requirements.txt'
        subprocess.run(['uv', 'export', '--locked', '--extra', 'gui', '--no-dev',
                        '--no-emit-project', '--no-header', '--no-annotate',
                        '--emit-index-url', '--output-file', str(requirements)], cwd=source, check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(['uv', 'build', '--out-dir', str(output)], cwd=source, check=True)
        wheel = output / f'arc_sbs-{version}-py3-none-any.whl'
        staging = Path(temporary) / f'arc-sbs-{version}-{args.backend}'
        staging.mkdir()
        (staging / 'backend.json').write_text(json.dumps({'backend': args.backend}) + '\n')
        for item in [wheel, root / 'LICENSE', root / 'THIRD_PARTY_NOTICES.md', requirements,
                     *(root / 'packaging' / name for name in ('install.py', 'arc-sbs.svg', 'INSTALL.md'))]:
            shutil.copy2(item, staging / item.name)
        shutil.copytree(root / 'integrations/qimgv', staging / 'qimgv',
                        ignore=shutil.ignore_patterns('__pycache__'))
        with tarfile.open(bundle, 'w:gz') as archive:
            archive.add(staging, arcname=staging.name)
    for artifact in (wheel, output / f'arc_sbs-{version}.tar.gz', bundle):
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact.with_name(artifact.name + '.sha256').write_text(f'{digest}  {artifact.name}\n')
    print(bundle)


if __name__ == '__main__':
    main()
