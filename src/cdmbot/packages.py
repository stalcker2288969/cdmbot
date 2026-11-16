"""Assembly-only Git packages, immutable locks and a JSON package index."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from .model import TestError
from .workspace import confined, read_json

NAME = re.compile(r'[a-z][a-z0-9_-]{0,63}$')
INDEX = str(Path(__file__).with_name('data') / 'registry.json')


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def registry(location=None):
    location = location or os.environ.get('CDMBOT_REGISTRY') or INDEX
    if location.startswith('https://'):
        with urllib.request.urlopen(location, timeout=30) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise TestError('Registry index exceeds 1 MiB')
        result = json.loads(data)
    else:
        result = read_json(Path(location))
    if not isinstance(result, dict) or not isinstance(result.get('packages'), dict):
        raise TestError('Registry must contain a packages object')
    return result['packages']


def git(args, cwd=None):
    try:
        result = subprocess.run(['git', '-c', 'core.autocrlf=false', '-c', 'core.eol=lf', *args], cwd=cwd, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=90,
                                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TestError(f'Git package operation failed: {exc}') from exc
    if result.returncode:
        raise TestError(result.stderr.strip() or 'Git package operation failed')
    return result.stdout.strip()


def digest(directory):
    result = {}
    total = 0
    for file in sorted(directory.rglob('*')):
        if file.is_symlink():
            raise TestError('Package symlinks are not supported')
        if not file.is_file():
            continue
        total += file.stat().st_size
        if total > 8 * 1024 * 1024 or len(result) >= 1000:
            raise TestError('Package exceeds the source size limit')
        result[file.relative_to(directory).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    return result


def fetch(spec, destination, target, locked=None):
    if 'builtin' in spec:
        name = spec['builtin']
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise TestError('Invalid built-in package name')
        directory = Path(__file__).with_name('data') / 'packages' / name
        commit = hashlib.sha256(json.dumps(digest(directory), sort_keys=True).encode()).hexdigest()
    else:
        directory, commit = checkout(spec, destination, locked)
    return copy_package(spec, destination, target, locked, directory, commit)


def checkout(spec, destination, locked):
    source = spec.get('git')
    if not isinstance(source, str) or not source or source.startswith('-') or '\n' in source:
        raise TestError('Package source must be a Git URL or local Git repository')
    # HTTPS repositories and local paths need no shell or install hooks.
    if '://' in source and not source.startswith(('https://', 'file://')):
        raise TestError('Use an HTTPS Git URL or a local repository')
    ref = spec.get('ref', 'main')
    if not isinstance(ref, str) or not re.fullmatch(r'[A-Za-z0-9_./-]+', ref) or ref.startswith('-'):
        raise TestError('Invalid package ref')
    git(['clone', '--depth', '1', *([] if locked else ['--branch', ref]), '--', source, str(destination)])
    if locked:
        commit = locked.get('commit', '')
        if not re.fullmatch(r'[0-9a-f]{40,64}', commit):
            raise TestError('Invalid commit in package lock')
        if git(['rev-parse', 'HEAD'], destination) != commit:
            git(['fetch', '--depth', '1', 'origin', commit], destination)
            git(['checkout', '--detach', commit], destination)
    commit = git(['rev-parse', 'HEAD'], destination)
    subdir = spec.get('subdir', '.')
    if not isinstance(subdir, str):
        raise TestError('Invalid package subdirectory')
    directory = confined(destination, destination / subdir)
    return directory, commit


def copy_package(spec, destination, target, locked, directory, commit):
    manifest = read_json(directory / 'cdm-package.json')
    if not NAME.fullmatch(str(manifest.get('name', ''))):
        raise TestError('Invalid package name')
    targets = manifest.get('targets')
    if not isinstance(targets, list) or any(value not in ('cdm8', 'cdm8e', 'cdm16', 'cdm16e') for value in targets):
        raise TestError('Package targets must be a list of processor names')
    if target not in targets:
        raise TestError(f'Package {manifest["name"]} does not support {target}')
    if not isinstance(manifest.get('version'), str) or not manifest['version']:
        raise TestError('Package must declare a version')
    nested = manifest.get('dependencies', {})
    if not isinstance(nested, dict):
        raise TestError('Package dependencies must be a mapping of names to Git sources')
    files = manifest.get('files')
    if not isinstance(files, list) or not files or any(not isinstance(f, str) for f in files):
        raise TestError('Package files must be a nonempty list of relative source paths')
    if len(files) > 999:
        raise TestError('Package lists too many files')
    payload = destination.parent / 'payload'
    payload.mkdir()
    total = 0
    for name in ['cdm-package.json', *files]:
        file = confined(directory, directory / name)
        if file.suffix.lower() not in ('.asm', '.mlb', '.json', '.md', '.txt') and file.name != 'LICENSE':
            raise TestError(f'Package file type is not allowed: {name}')
        if not file.is_file() or file.is_symlink():
            raise TestError(f'Missing or unsupported package file: {name}')
        total += file.stat().st_size
        if total > 8 * 1024 * 1024:
            raise TestError('Package exceeds the source size limit')
        output = confined(payload, payload / name)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, output)
    hashes = digest(payload)
    if locked and (locked.get('files') != hashes or locked.get('source') != spec):
        raise TestError('Package differs from the locked source; use pub update explicitly')
    return payload, {'source': spec, 'commit': commit, 'version': manifest['version'], 'files': hashes,
                     'name': manifest['name'], 'dependencies': nested, 'targets': manifest['targets']}


def dependencies(root):
    path = root / 'cdm-packages.json'
    data = read_json(path) if path.is_file() else {'version': 1, 'dependencies': {}}
    if data.get('version') != 1 or not isinstance(data.get('dependencies'), dict):
        raise TestError('Invalid cdm-packages.json')
    return path, data


def _restore(root, target, update=False, changed=None, refresh=()):
    path, data = dependencies(root)
    data = changed or data
    lock_path = root / 'cdm-packages.lock.json'
    old = read_json(lock_path).get('packages', {}) if lock_path.is_file() else {}
    parent = root / '.cdm/packages'
    parent.mkdir(parents=True, exist_ok=True)
    result = {}
    refresh = set(refresh)
    with tempfile.TemporaryDirectory(prefix='.packages-', dir=root / '.cdm') as temp:
        prepared = []
        pending = [(name, spec, ()) for name, spec in data['dependencies'].items()]
        specifications = {}
        while pending:
            name, spec, ancestors = pending.pop(0)
            if not NAME.fullmatch(name) or not isinstance(spec, dict):
                raise TestError('Invalid dependency name or source')
            if name in ancestors:
                raise TestError('Cyclic package dependency: ' + ' -> '.join((*ancestors, name)))
            if name in specifications:
                if specifications[name] != spec:
                    raise TestError(f'Conflicting package sources for {name}; use one version per project')
                continue
            specifications[name] = spec
            locked = None if update or name in refresh else old.get(name)
            if locked and locked.get('source') != spec:
                raise TestError(f'{name}: dependency changed; use pub update')
            installed = parent / name
            if locked and installed.is_dir():
                if digest(installed) != locked.get('files'):
                    raise TestError(f'{name}: installed sources have changed; remove the cache and restore the lock')
                if target not in locked.get('targets', []):
                    raise TestError(f'{name}: incompatible processor')
                result[name] = locked
                pending.extend((child, source, (*ancestors, name)) for child, source in locked.get('dependencies', {}).items())
                continue
            work = Path(temp) / name
            work.mkdir()
            payload, entry = fetch(spec, work / 'repo', target, locked)
            if entry.pop('name') != name:
                raise TestError(f'{name}: package manifest has a different name')
            result[name] = entry
            if name in refresh:
                refresh.update(child for child, source in entry['dependencies'].items()
                               if old.get(child, {}).get('source') != source)
            pending.extend((child, source, (*ancestors, name)) for child, source in entry['dependencies'].items())
            prepared.append((payload, installed))
        # Downloads and validation finish before changing existing installations.
        backups = []
        metadata = {file: file.read_bytes() if file.exists() else None for file in (path, lock_path)}
        try:
            for payload, installed in prepared:
                backup = Path(temp) / (installed.name + '.previous')
                if installed.exists():
                    installed.rename(backup)
                backups.append((installed, backup))
                payload.rename(installed)
            save(path, data)
            save(lock_path, {'version': 1, 'packages': result})
        except BaseException:
            for installed, backup in reversed(backups):
                if installed.exists():
                    shutil.rmtree(installed)
                if backup.exists():
                    backup.rename(installed)
            for file, previous in metadata.items():
                if previous is None:
                    file.unlink(missing_ok=True)
                else:
                    file.write_bytes(previous)
            raise
    return result


@contextmanager
def operation_lock(root):
    path = root / '.cdm/operation.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise TestError('Another package operation is in progress') from exc
    try:
        os.write(handle, str(os.getpid()).encode('ascii'))
        yield
    finally:
        os.close(handle)
        path.unlink(missing_ok=True)


def restore(root, target, update=False, changed=None, refresh=()):
    with operation_lock(root):
        return _restore(root, target, update, changed, refresh)


def verify(root, target=None):
    if (root / '.cdm/operation.lock').exists():
        raise TestError('A package operation is in progress; retry the build after it finishes')
    _, data = dependencies(root)
    if not data['dependencies']:
        return
    lock = read_json(root / 'cdm-packages.lock.json').get('packages', {})
    pending = list(data['dependencies'].items())
    checked = set()
    while pending:
        name, spec = pending.pop(0)
        if name in checked:
            continue
        checked.add(name)
        if not NAME.fullmatch(name):
            raise TestError('Invalid dependency name')
        directory = root / '.cdm/packages' / name
        if name not in lock or lock[name].get('source') != spec or not directory.is_dir() or digest(directory) != lock[name].get('files'):
            raise TestError(f'{name}: dependency missing or modified; run cdmbot pub get')
        if target and target not in lock[name].get('targets', []):
            raise TestError(f'{name}: incompatible processor {target}')
        pending.extend(lock[name].get('dependencies', {}).items())
