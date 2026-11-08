"""Launchers find Compose from both source and packaged layouts."""

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('layout', ['source', 'release'])
@pytest.mark.parametrize(('name', 'commands'), [
    ('start', ['compose up -d']), ('stop', ['compose down']),
    ('update', ['compose pull', 'compose up -d']),
])
def test_bash_launchers_find_compose_from_another_directory(tmp_path, layout, name, commands):
    project = tmp_path / 'dashboard with spaces'
    project.mkdir()
    (project / 'docker-compose.yml').touch()
    launcher_dir = project / 'scripts' if layout == 'source' else project
    launcher_dir.mkdir(exist_ok=True)
    launcher = launcher_dir / f'{name}.sh'
    shutil.copy2(ROOT / 'scripts' / launcher.name, launcher)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    log = tmp_path / 'docker-calls'
    docker = binaries / 'docker'
    docker.write_text('#!/bin/sh\nprintf "%s\\t%s\\n" "$PWD" "$*" >> "$LAUNCHER_TEST_LOG"\n')
    docker.chmod(0o755)
    for executable in ('sleep', 'open', 'xdg-open'):
        path = binaries / executable
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
    env = dict(os.environ, PATH=f'{binaries}{os.pathsep}{os.environ["PATH"]}',
               LAUNCHER_TEST_LOG=str(log))
    subprocess.run(['bash', str(launcher)], cwd=tmp_path, env=env,
                   capture_output=True, text=True, check=True, timeout=10)
    assert log.read_text().splitlines() == [f'{project}\t{command}' for command in commands]


def test_release_packaging_uses_moved_scripts_and_preserves_zip_layout(tmp_path):
    if not shutil.which('zip'):
        pytest.skip('zip is not installed')
    workflow = yaml.safe_load((ROOT / '.github/workflows/release.yml').read_text())
    step = next(step for step in workflow['jobs']['release']['steps']
                if step.get('name') == 'Build ibkrdash-setup.zip')
    for filename in ('docker-compose.yml', '.env.example', 'SETUP.txt'):
        shutil.copy2(ROOT / filename, tmp_path / filename)
    shutil.copytree(ROOT / 'scripts', tmp_path / 'scripts')
    subprocess.run(['bash', '-e', '-c', step['run']], cwd=tmp_path,
                   capture_output=True, text=True, check=True, timeout=10)
    with zipfile.ZipFile(tmp_path / 'ibkrdash-setup.zip') as archive:
        for name in ('start.sh', 'stop.sh', 'update.sh', 'start.bat', 'stop.bat', 'update.bat'):
            assert archive.read(f'ibkrdash/{name}') == (ROOT / 'scripts' / name).read_bytes()
        assert 'ibkrdash/docker-compose.yml' in archive.namelist()
