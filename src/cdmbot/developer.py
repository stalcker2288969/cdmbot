"""Development commands. Legacy test commands remain in cli.py."""
import argparse
import json
import os
import re
import tempfile
import shutil
from pathlib import Path

from .assembler import _run_cocas, build_program, check_syntax
from .config import read_case
from .coverage import source_metrics
from .model import TestError, Suite
from .runner import run_report
from .workspace import TARGETS, workspace, resolve_sources, package_directory, confined
from . import packages

CAPABILITIES = {'api': 1, 'version': '0.2.0', 'commands': ['create', 'build', 'run', 'analyze', 'test', 'pub'],
                'package_format': 1, 'project_format': 'cdm-project.json'}


def create(directory, target, architecture, library=False):
    root = Path(directory).resolve()
    if root.exists() and any(root.iterdir()):
        raise TestError('The destination must be empty; existing files will not be overwritten')
    root.mkdir(parents=True, exist_ok=True)
    if library:
        name = root.name.lower().replace(' ', '-')
        if not packages.NAME.fullmatch(name):
            raise TestError('Use a lowercase package name such as my-library')
        packages.save(root / 'cdm-package.json', {'version': '0.1.0', 'name': name,
                      'targets': [target], 'files': ['lib.asm', 'README.md']})
        source = 'rsect library\n\n# @param r0 Input\n# @return r0 Input plus one\n# @changed PS Flags\nincrement>\n    inc r0\n    rts\nend\n'
        entry = root / 'lib.asm'
    else:
        packages.save(root / 'cdm-project.json', {'entry': 'src/main.asm', 'target': target, 'architecture': architecture,
                                                'environment': 'emulator', 'extraSources': []})
        source = ('asect 0\n    dc main, 0\nasect 0x0200\nmain:\n    ldi r7, 0x8000\n    stsp r7\n'
                  if target.startswith('cdm16') else 'asect 0\nmain:\n    setsp 0xF0\n')
        source += '    ldi r0, 42\n    halt\nend\n'
        (root / 'src').mkdir()
        entry = root / 'src/main.asm'
        if target in ('cdm8', 'cdm16'):
            (root / 'project.yaml').write_text('version: 1\nsource_roots: [src]\nsuites:\n  smoke:\n    program: src/main.asm\n    suite: tests.yaml\n', encoding='utf-8')
            (root / 'tests.yaml').write_text('version: 1\narchitecture: ' + target + '\nmemory_model: ' +
                ('unified' if architecture == 'vonNeumann' else 'harvard') +
                '\ncases:\n  returns_42:\n    run: {until: halt, max_steps: 100}\n    expect:\n      registers: {r0: 42}\n', encoding='utf-8')
    entry.write_text(source, encoding='utf-8')
    (root / 'README.md').write_text('# ' + root.name + '\n\nCreated with cdmbot.\n', encoding='utf-8')
    (root / '.gitignore').write_text('build/\n.cdm/\n', encoding='utf-8')
    return {'passed': True, 'root': str(root), 'entry': str(entry)}


def build(value, target=None, architecture=None, output=None, name=None, workspace_root=None):
    root, entry, config, project = workspace(value, target, architecture, workspace_root)
    if workspace_root and not project:
        root = Path(workspace_root).resolve()
        confined(root, entry)
    packages.verify(package_directory(root, entry), config['target'])
    sources = resolve_sources(root, entry, config)
    destination = Path(output).resolve() if output else (root if project else entry.parent) / 'build'
    destination.mkdir(parents=True, exist_ok=True)
    basename = name or ('out' if project else entry.stem)
    if not re.fullmatch(r'[\w.-]+', basename) or basename in ('.', '..'):
        raise TestError('Invalid artifact name')
    with tempfile.TemporaryDirectory(prefix='.cdmbot-', dir=destination) as temporary:
        temp = Path(temporary)
        binary, debug = temp / (basename + '.bin'), temp / (basename + '.dbg.json')
        module = ['cdmbot.compiler'] if config['target'].startswith('cdm16') else ['cocas.main']
        _run_cocas(module, config['target'], ['-o', str(binary), '--debug', str(debug), *map(str, sources)], root,
                   strict=not any(file.suffix.lower() == '.mlb' for file in sources))
        raw = binary.read_bytes()
        size = 65536 if config['target'].startswith('cdm16') else 256
        if len(raw) > size and any(raw[size:]):
            raise TestError('Image exceeds the processor address space')
        raw = raw[:size]
        binary.write_bytes(raw)
        image = temp / (basename + '.img')
        image.write_text('v2.0 raw\n' + ' '.join(f'{byte:02x}' for byte in raw) + '\n', encoding='utf-8')
        combined = temp / (basename + '.combined.asm')
        combined.write_text('\n'.join('# --- ' + str(file.relative_to(root)) + ' ---\n' +
                            file.read_text(encoding='utf-8-sig') for file in sources if file.suffix not in ('.obj', '.o', '.lib')),
                            encoding='utf-8')
        artifacts = {}
        published = []
        try:
            for key, file in [('image', image), ('binary', binary), ('debug', debug), ('combined', combined)]:
                final = destination / file.name
                backup = temp / ('previous-' + file.name)
                if final.exists():
                    shutil.copyfile(final, backup)
                os.replace(file, final)
                published.append((final, backup))
                artifacts[key] = str(final)
        except BaseException:
            for final, backup in reversed(published):
                if backup.exists():
                    os.replace(backup, final)
                else:
                    final.unlink(missing_ok=True)
            raise
    return {'passed': True, 'root': str(root), 'target': config['target'], 'architecture': config['architecture'],
            'sources': list(map(str, sources)), 'artifacts': artifacts}


def run(value, target=None, architecture=None, max_steps=100000):
    root, entry, config, _ = workspace(value, target, architecture)
    if config['target'] not in ('cdm8', 'cdm16'):
        raise TestError('Local CLI execution currently supports cdm8 and cdm16')
    built = build(value, target, architecture)
    from .cdm8 import Cdm8Backend
    from .cdm16 import Cdm16Backend
    case = read_case('run', {'run': {'until': 'halt', 'max_steps': max_steps}}, 'run', config['target'])
    image = Path(built['artifacts']['binary']).read_bytes()
    size = 256 if config['target'] == 'cdm8' else 65536
    image = image.ljust(size, b'\0')
    layout = 'unified' if config['architecture'] == 'vonNeumann' else 'harvard'
    ram = image if layout == 'unified' else bytes(size)
    with tempfile.TemporaryDirectory(prefix='cdmbot-run-') as directory:
        backend = Cdm8Backend() if config['target'] == 'cdm8' else Cdm16Backend(Path(directory))
        result = backend.execute(image, ram, case, layout)
    ram_path = Path(built['artifacts']['image']).with_suffix('.ram.bin')
    ram_path.write_bytes(result.memory)
    return {**built, 'passed': result.reason == 'halt', 'reason': result.reason, 'steps': result.steps,
            'registers': result.registers, 'flags': result.flags, 'ram': str(ram_path)}


def analyze(value, target=None, architecture=None, syntax=True, workspace_root=None):
    root, entry, config, project = workspace(value, target, architecture, workspace_root)
    if workspace_root and not project:
        root = Path(workspace_root).resolve()
    packages.verify(package_directory(root, entry), config['target'])
    sources = resolve_sources(root, entry, config)
    rows, diagnostics = [], []
    for file in sources:
        if file.suffix.lower() in ('.obj', '.o', '.lib'):
            continue
        metrics, instructions = source_metrics(file)
        rows.append({'file': str(file.relative_to(root)), 'lines': metrics.lines, 'code': metrics.code,
                     'comments': metrics.comment_only + metrics.inline_comments, 'blank': metrics.blank,
                     'instruction_lines': len(instructions)})
    if syntax:
        try:
            check_syntax(config['target'], tuple(sources), strict=not any(file.suffix.lower() == '.mlb' for file in sources))
        except TestError as exc:
            diagnostics.append(str(exc))
    return {'passed': not diagnostics, 'root': str(root), 'target': config['target'], 'files': rows,
            'totals': {key: sum(row[key] for row in rows) for key in ('lines', 'code', 'comments', 'blank', 'instruction_lines')},
            'syntax_checked': syntax, 'diagnostics': diagnostics}


def parser():
    root = argparse.ArgumentParser(prog='cdmbot', description='CdM development tools, tests and packages')
    sub = root.add_subparsers(dest='command', required=True)
    capabilities = sub.add_parser('capabilities', help='report the machine-readable integration API')
    capabilities.add_argument('--json', action='store_true')
    init = sub.add_parser('create', aliases=['init'], help='create an application or library')
    init.add_argument('path')
    init.add_argument('--library', action='store_true')
    for name in ('build', 'run', 'analyze'):
        command = sub.add_parser(name)
        command.add_argument('path', nargs='?', default='.')
        command.add_argument('--target', choices=TARGETS)
        command.add_argument('--architecture', choices=('vonNeumann', 'harvard'))
        command.add_argument('--json', action='store_true')
        if name == 'build':
            command.add_argument('--output')
            command.add_argument('--name')
            command.add_argument('--workspace')
        if name == 'run':
            command.add_argument('--max-steps', type=int, default=100000)
        if name == 'analyze':
            command.add_argument('--no-syntax', action='store_true')
            command.add_argument('--workspace')
    init.add_argument('--target', choices=TARGETS, default='cdm8')
    init.add_argument('--architecture', choices=('vonNeumann', 'harvard'), default='vonNeumann')
    init.add_argument('--json', action='store_true')
    tests = sub.add_parser('test', help='run a project test manifest')
    tests.add_argument('path', nargs='?', default='.')
    tests.add_argument('--jobs', type=int, default=1)
    tests.add_argument('--json', action='store_true')
    pub = sub.add_parser('pub', help='manage assembly libraries')
    operations = pub.add_subparsers(dest='operation', required=True)
    for name in ('add', 'get', 'update', 'list', 'search', 'remove', 'publish'):
        command = operations.add_parser(name)
        command.add_argument('--path', default='.')
        command.add_argument('--json', action='store_true')
        command.add_argument('--target', choices=TARGETS)
        command.add_argument('--workspace')
        if name == 'add':
            command.add_argument('package')
            command.add_argument('--name')
            command.add_argument('--git')
            command.add_argument('--ref', default='main')
            command.add_argument('--subdir', default='.')
        if name in ('add', 'search'):
            command.add_argument('--registry')
        if name == 'search':
            command.add_argument('query', nargs='?', default='')
        if name == 'remove':
            command.add_argument('package')
        if name == 'publish':
            command.add_argument('directory')
            command.add_argument('--git', required=True)
            command.add_argument('--ref', required=True)
            command.add_argument('--subdir', default='.')
            command.add_argument('--registry', required=True)
    return root


def execute(options):
    if options.command == 'capabilities':
        return CAPABILITIES
    if options.command == 'test':
        from .project import load_project, run_project
        from .report import render_project_json
        path = Path(options.path).resolve()
        if path.is_dir():
            path = path / ('project.yaml' if (path / 'project.yaml').exists() else 'project.yml')
        return json.loads(render_project_json(run_project(load_project(path), jobs=options.jobs)))
    if options.command in ('create', 'init'):
        return create(options.path, options.target, options.architecture, options.library)
    if options.command == 'build':
        return build(options.path, options.target, options.architecture, options.output, options.name, options.workspace)
    if options.command == 'run':
        if not 1 <= options.max_steps <= 10000000:
            raise TestError('max-steps must be between 1 and 10000000')
        return run(options.path, options.target, options.architecture, options.max_steps)
    if options.command == 'analyze':
        return analyze(options.path, options.target, options.architecture, not options.no_syntax, options.workspace)
    if options.operation == 'search':
        return {'passed': True, 'packages': {name: spec for name, spec in packages.registry(options.registry).items()
                                            if options.query.lower() in name.lower()}}
    if options.operation == 'publish':
        if '://' in options.registry:
            raise TestError('Publish to a local clone of the registry index, then submit its Git change')
        manifest = packages.read_json(Path(options.directory).resolve() / 'cdm-package.json')
        spec = {'git': options.git, 'ref': options.ref, 'subdir': options.subdir}
        with tempfile.TemporaryDirectory(prefix='cdmbot-publish-') as directory:
            _, entry = packages.fetch(spec, Path(directory) / 'repo', manifest['targets'][0])
            if entry['name'] != manifest['name'] or entry['version'] != manifest['version']:
                raise TestError('The remote Git ref does not match the local package name/version')
        index = Path(options.registry).resolve()
        data = packages.read_json(index) if index.exists() else {'version': 1, 'packages': {}}
        data['packages'][manifest['name']] = spec
        packages.save(index, data)
        return {'passed': True, 'registry': str(index), 'package': manifest['name'], 'commit': entry['commit']}
    root, _, config, _ = workspace(options.path, options.target, boundary=options.workspace)
    _, data = packages.dependencies(root)
    if options.operation == 'list':
        return {'passed': True, 'dependencies': data['dependencies']}
    if options.operation == 'add':
        name = options.name or options.package
        spec = {'git': options.git, 'ref': options.ref, 'subdir': options.subdir} if options.git else packages.registry(options.registry).get(options.package)
        if not spec:
            raise TestError('Package not found; supply --git URL or a registry entry')
        data['dependencies'][name] = spec
    if options.operation == 'remove':
        if options.package not in data['dependencies']:
            raise TestError('Package is not a direct dependency')
        del data['dependencies'][options.package]
    result = packages.restore(root, config['target'], update=options.operation == 'update', changed=data,
                              refresh=(name,) if options.operation == 'add' else ())
    return {'passed': True, 'packages': result}


def main(argv):
    options = parser().parse_args(argv)
    try:
        report = execute(options)
    except (TestError, OSError, ValueError, KeyError, TypeError) as exc:
        report = {'passed': False, 'error': str(exc)}
    if options.json:
        print(json.dumps(report))
    elif options.command == 'analyze' and 'files' in report:
        print('File | Lines | Code | Comments | Instruction lines')
        for row in report['files']:
            print(f'{row["file"]} | {row["lines"]} | {row["code"]} | {row["comments"]} | {row["instruction_lines"]}')
        for diagnostic in report['diagnostics']:
            print(diagnostic)
    else:
        print(json.dumps(report, indent=2))
    return 0 if report.get('passed', True) else 1
