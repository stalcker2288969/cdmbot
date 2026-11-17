"""Shared project and source resolution for the CLI and editor integrations."""
import json
import re
from pathlib import Path

from .model import TestError
from .language import language_version

INCLUDE = re.compile(r'^\s*#\s*@include\s+["\']([^"\']+)["\']\s*(?:#.*)?$')
TARGETS = ('cdm8', 'cdm8e', 'cdm16', 'cdm16e')


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise TestError(f'{path}: {exc}') from exc
    if not isinstance(value, dict):
        raise TestError(f'{path}: expected an object')
    return value


def confined(root, path):
    root, path = root.resolve(), path.resolve()
    if not path.is_relative_to(root):
        raise TestError(f'Path escapes project: {path}')
    return path


def workspace(value='.', target=None, architecture=None, boundary=None):
    path = Path(value).resolve()
    boundary = Path(boundary).resolve() if boundary else None
    if boundary:
        confined(boundary, path)
    manifest = path / 'cdm-project.json' if path.is_dir() else None
    if path.name == 'cdm-project.json':
        manifest = path
    if path.is_file() and manifest is None:
        manifest = next((p / 'cdm-project.json' for p in path.parents if (not boundary or p.is_relative_to(boundary)) and
                         (p / 'cdm-project.json').is_file()), None)
    if manifest and manifest.is_file():
        root = manifest.parent
        config = read_json(manifest)
        entry = confined(root, root / config.get('entry', 'main.asm'))
    else:
        if not path.is_file() or path.suffix.lower() not in ('.asm', '.cdm8', '.cdm8e', '.cdm16', '.cdm16e', '.cocoasm'):
            raise TestError('Open an assembly file or a folder with cdm-project.json')
        root, entry, config = path.parent, path, {}
    suffix = re.search(r'\.(cdm(?:8|16)e?)(?:\.asm)?$', entry.name, re.I)
    config = {**config, 'target': target or config.get('target', suffix[1].lower() if suffix else 'cdm8'),
              'architecture': architecture or config.get('architecture', 'vonNeumann')}
    if config['target'] not in TARGETS or config['architecture'] not in ('vonNeumann', 'harvard'):
        raise TestError('Unsupported processor or memory architecture')
    if not isinstance(config.get('extraSources', []), list):
        raise TestError('extraSources must be a list')
    language_version(config)
    return root, entry, config, bool(manifest and manifest.is_file())


def package_directory(root, entry):
    return next((directory for directory in entry.parents if directory.is_relative_to(root) and
                 '.cdm' not in directory.relative_to(root).parts and
                 any((directory / name).is_file() for name in ('cdm-project.json', 'cdm-package.json', 'cdm-packages.json', 'cdm-packages.lock.json'))), entry.parent)


def resolve_sources(root, entry, config):
    sources, visited, active = [], set(), set()
    package_root = package_directory(root, entry)

    def visit(path):
        path = confined(root, path)
        if path in active:
            raise TestError(f'Cyclic include: {path}')
        if path in visited:
            return
        if not path.is_file():
            raise TestError(f'Missing source: {path}')
        visited.add(path)
        active.add(path)
        sources.append(path)
        if path.suffix.lower() in ('.asm', '.mlb', '.cdm8', '.cdm16', '.cdm8e', '.cdm16e', '.cocoasm'):
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                match = INCLUDE.match(line)
                if not match:
                    continue
                name = match[1].replace('\\', '/')
                if name.startswith('@'):
                    parts = name[1:].split('/')
                    if len(parts) < 2 or not re.fullmatch(r'[a-z][a-z0-9_-]*', parts[0]):
                        raise TestError('Package imports use @name/path.asm')
                    lock = read_json(package_root / 'cdm-packages.lock.json')
                    if parts[0] not in lock.get('packages', {}):
                        raise TestError(f'{parts[0]} is not locked; add it with cdmbot pub add')
                    base = confined(root, package_root / '.cdm/packages' / parts[0])
                    dependency = confined(base, base.joinpath(*parts[1:]))
                else:
                    dependency = confined(root, path.parent / name)
                if name.endswith('/*'):
                    directory = dependency.parent
                    if not directory.is_dir():
                        raise TestError(f'Missing include folder: {directory}')
                    for child in sorted(directory.iterdir()):
                        if child.suffix.lower() in ('.asm', '.mlb') and child != path and not child.name.startswith('.'):
                            visit(child)
                else:
                    visit(dependency)
        active.remove(path)

    visit(entry)
    for extra in config.get('extraSources', []):
        if not isinstance(extra, str):
            raise TestError('extraSources entries must be paths')
        visit(root / extra)
    return sources
