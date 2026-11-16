import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from cdmbot.developer import create, build, run, analyze, parser, execute
from cdmbot import packages
from cdmbot.model import TestError
from cdmbot.config import load_suite
from cdmbot.runner import run_report


class DeveloperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_create_build_run_analyze_and_no_overwrite(self):
        app = self.root / 'app'
        create(app, 'cdm8', 'vonNeumann')
        with self.assertRaises(TestError):
            create(app, 'cdm8', 'vonNeumann')
        result = run(app)
        self.assertTrue(result['passed'])
        self.assertEqual(result['registers']['r0'], 42)
        self.assertEqual(result['registers']['sp'], 240)
        self.assertTrue(analyze(app)['syntax_checked'])
        good = Path(result['artifacts']['binary']).read_bytes()
        (app / 'src/main.asm').write_text('asect 0\ninvalid_instruction\nend\n')
        self.assertFalse(analyze(app)['passed'])
        with self.assertRaises(TestError):
            build(app)
        self.assertEqual(Path(result['artifacts']['binary']).read_bytes(), good)

    def test_generated_templates_run_on_both_processors_and_memory_models(self):
        for target in ('cdm8', 'cdm16'):
            for architecture in ('vonNeumann', 'harvard'):
                with self.subTest(target=target, architecture=architecture):
                    app = self.root / (target + '-' + architecture)
                    create(app, target, architecture)
                    result = run(app)
                    self.assertTrue(result['passed'], result)
                    self.assertEqual(result['registers']['r0'], 42)
                    self.assertEqual(result['registers']['sp'], 240 if target == 'cdm8' else 32768)

    def repository(self, name, dependencies=None):
        path = self.root / name
        create(path, 'cdm8', 'vonNeumann', True)
        manifest = packages.read_json(path / 'cdm-package.json')
        manifest['dependencies'] = dependencies or {}
        packages.save(path / 'cdm-package.json', manifest)
        self.git(['init', '-b', 'main'], path)
        self.commit(path)
        return path

    def git(self, args, directory):
        result = subprocess.run(['git', '-c', 'user.name=Tests', '-c', 'user.email=tests@example.invalid', *args],
                                cwd=directory, capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def commit(self, directory):
        self.git(['add', '.'], directory)
        self.git(['commit', '-m', 'Test fixture'], directory)

    def test_package_lock_update_transitive_sources_and_tamper_detection(self):
        child = self.repository('child')
        lib = self.repository('math', {'child': {'git': str(child), 'ref': 'main'}})
        app = self.root / 'consumer'
        create(app, 'cdm8', 'vonNeumann')
        config = {'version': 1, 'dependencies': {'math': {'git': str(lib), 'ref': 'main'}}}
        first = packages.restore(app, 'cdm8', changed=config)
        self.assertEqual(set(first), {'math', 'child'})
        (app / 'src/main.asm').write_text('# @include "@math/lib.asm"\nincrement: ext\nasect 0\nmain:\nsetsp 240\nldi r0, 1\njsr increment\nhalt\nend\n')
        self.assertEqual(run(app)['registers']['r0'], 2)
        (lib / 'lib.asm').write_text((lib / 'lib.asm').read_text().replace('inc r0', 'inc r0\n    inc r0'))
        self.commit(lib)
        self.assertEqual(packages.restore(app, 'cdm8')['math']['commit'], first['math']['commit'])
        self.assertEqual(run(app)['registers']['r0'], 2)
        updated = packages.restore(app, 'cdm8', update=True)
        self.assertNotEqual(updated['math']['commit'], first['math']['commit'])
        self.assertEqual(run(app)['registers']['r0'], 3)
        (app / '.cdm/packages/math/lib.asm').write_text('modified')
        with self.assertRaises(TestError):
            run(app)

    def test_failed_install_does_not_change_manifest_or_installed_dependency(self):
        lib = self.repository('math')
        app = self.root / 'consumer'
        create(app, 'cdm8', 'vonNeumann')
        config = {'version': 1, 'dependencies': {'math': {'git': str(lib)}}}
        packages.restore(app, 'cdm8', changed=config)
        before = (app / 'cdm-packages.lock.json').read_bytes()
        with self.assertRaises(TestError):
            packages.restore(app, 'cdm16', update=True)
        self.assertEqual((app / 'cdm-packages.lock.json').read_bytes(), before)
        packages.verify(app)
        manifest = packages.read_json(lib / 'cdm-package.json')
        manifest['files'] = ['../escape.asm']
        packages.save(lib / 'cdm-package.json', manifest)
        self.commit(lib)
        with self.assertRaises(TestError):
            packages.restore(app, 'cdm8', update=True)
        self.assertEqual((app / 'cdm-packages.lock.json').read_bytes(), before)

    def test_standalone_output_is_local(self):
        directory = self.root / 'nested'
        directory.mkdir()
        file = directory / 'hello.cdm8.asm'
        file.write_text('asect 0\nldi r0, 7\nhalt\nend\n')
        self.assertEqual(Path(build(file)['artifacts']['image']).parent, directory / 'build')

    def test_editor_standalone_auto_packages_stay_beside_the_source(self):
        directory = self.root / 'sorting'
        directory.mkdir()
        file = directory / 'sort.asm'
        file.write_text('# @include "@byte-utils/lib.asm"\nbyte_increment: ext\nasect 0\nsetsp 240\nldi r0, 41\njsr byte_increment\nhalt\nend\n')
        result = build(file, target='cdm8', workspace_root=self.root)
        self.assertEqual(Path(result['artifacts']['image']).parent, directory / 'build')
        self.assertTrue((directory / 'cdm-packages.json').is_file())
        self.assertTrue((directory / '.cdm/packages/byte-utils/lib.asm').is_file())
        self.assertFalse((self.root / 'cdm-packages.json').exists())

    def test_editor_boundary_does_not_adopt_a_parent_project(self):
        app = self.root / 'parent'
        create(app, 'cdm16', 'vonNeumann')
        directory = app / 'standalone'
        directory.mkdir()
        file = directory / 'hello.asm'
        file.write_text('asect 0\nldi r0, 7\nhalt\nend\n')
        result = build(file, target='cdm8', workspace_root=directory)
        self.assertEqual(result['target'], 'cdm8')
        self.assertEqual(Path(result['root']), directory)
        self.assertEqual(Path(result['artifacts']['image']).name, 'hello.img')

    def test_builtin_registry_and_legacy_tests_can_use_locked_packages(self):
        app = self.root / 'consumer'
        create(app, 'cdm8', 'vonNeumann')
        self.assertIn('byte-utils', packages.registry())
        packages.restore(app, 'cdm8', changed={'version': 1, 'dependencies': {'byte-utils': {'builtin': 'byte-utils'}}})
        source = app / 'src/main.asm'
        source.write_text('# @include "@byte-utils/lib.asm"\nbyte_increment: ext\nasect 0\nsetsp 240\nldi r0, 41\njsr byte_increment\nhalt\nend\n')
        self.assertEqual(run(app)['registers']['r0'], 42)
        report = run_report(source, load_suite(app / 'tests.yaml'))
        self.assertEqual(report.exit_code, 0)
        with self.assertRaises(TestError):
            run(app, target='cdm16')

    def test_cache_rehydration_uses_locked_commit_after_branch_moves(self):
        lib = self.repository('math')
        app = self.root / 'consumer'
        create(app, 'cdm8', 'vonNeumann')
        config = {'version': 1, 'dependencies': {'math': {'git': str(lib)}}}
        initial = packages.restore(app, 'cdm8', changed=config)
        (lib / 'lib.asm').write_text((lib / 'lib.asm').read_text() + '\n# newer version\n')
        self.commit(lib)
        (app / '.cdm/packages/math').rename(app / 'old-cache')
        restored = packages.restore(app, 'cdm8')
        self.assertEqual(initial['math']['commit'], restored['math']['commit'])
        self.assertEqual(initial['math']['files'], restored['math']['files'])

    def test_git_package_repository_paths_may_contain_unicode(self):
        lib = self.repository('math')
        renamed = self.root / '\u0411\u0438\u0431\u043b\u0438\u043e\u0442\u0435\u043a\u0430'
        lib.rename(renamed)
        app = self.root / 'consumer'
        create(app, 'cdm8', 'vonNeumann')
        result = packages.restore(app, 'cdm8', changed={'version': 1, 'dependencies': {'math': {'git': str(renamed)}}})
        self.assertIn('math', result)

    def test_package_cycles_and_overlapping_operations_are_rejected(self):
        lib = self.repository('math')
        manifest = packages.read_json(lib / 'cdm-package.json')
        manifest['dependencies'] = {'math': {'git': str(lib)}}
        packages.save(lib / 'cdm-package.json', manifest)
        self.commit(lib)
        app = self.root / 'consumer'
        create(app, 'cdm8', 'vonNeumann')
        with self.assertRaisesRegex(TestError, 'Cyclic'):
            packages.restore(app, 'cdm8', changed={'version': 1, 'dependencies': {'math': {'git': str(lib)}}})
        with packages.operation_lock(app):
            with self.assertRaisesRegex(TestError, 'in progress'):
                packages.restore(app, 'cdm8')

    def test_registry_registration_add_and_remove_commands(self):
        lib = self.repository('math')
        index = self.root / 'registry.json'
        result = execute(parser().parse_args(['pub', 'publish', str(lib), '--git', str(lib), '--ref', 'main', '--registry', str(index)]))
        self.assertTrue(result['passed'])
        self.assertIn('math', packages.registry(str(index)))
        app = self.root / 'consumer'
        create(app, 'cdm8', 'vonNeumann')
        result = execute(parser().parse_args(['pub', 'add', 'math', '--path', str(app), '--registry', str(index)]))
        self.assertIn('math', result['packages'])
        result = execute(parser().parse_args(['pub', 'remove', 'math', '--path', str(app)]))
        self.assertEqual(result['packages'], {})
        self.assertEqual(packages.read_json(app / 'cdm-packages.lock.json')['packages'], {})

    def test_folder_catalog_publishes_metadata_and_searches_publishers(self):
        lib = self.repository('math')
        manifest = packages.read_json(lib / 'cdm-package.json')
        manifest.update(description='Arithmetic for students', publisher='Example author')
        packages.save(lib / 'cdm-package.json', manifest)
        self.commit(lib)
        catalog = self.root / 'catalog'
        execute(parser().parse_args(['pub', 'publish', str(lib), '--git', str(lib), '--ref', 'main', '--registry', str(catalog)]))
        card = packages.read_json(catalog / 'packages/math/package.json')
        self.assertEqual(card['publisher'], 'Example author')
        for term in ['students', 'author', 'math']:
            result = execute(parser().parse_args(['pub', 'search', term, '--registry', str(catalog)]))
            self.assertIn('math', result['packages'])

    def test_includes_automatically_install_and_lock_git_packages_for_projects_and_standalone(self):
        child = self.repository('child')
        lib = self.repository('math', {'child': '*'})
        catalog = self.root / 'catalog'
        for name, repository in [('math', lib), ('child', child)]:
            packages.save(catalog / 'packages' / name / 'package.json', {'name': name, 'description': 'Test package',
                          'publisher': 'Tests', 'source': {'git': str(repository), 'ref': 'main'}})
        with patch.dict(os.environ, {'CDMBOT_REGISTRY': str(catalog)}):
            for project in [True, False]:
                app = self.root / str(project)
                if project:
                    create(app, 'cdm8', 'vonNeumann')
                    source = app / 'src/main.asm'
                else:
                    app.mkdir()
                    source = app / 'demo.asm'
                source.write_text('# @include "@math/lib.asm"\nincrement: ext\nasect 0\nsetsp 240\nldi r0, 41\njsr increment\nhalt\nend\n')
                self.assertEqual(run(source)['registers']['r0'], 42)
                lock = packages.read_json(app / 'cdm-packages.lock.json')
                self.assertEqual(set(lock['packages']), {'math', 'child'})
                with patch('cdmbot.packages.registry', side_effect=AssertionError('Locked builds must work offline')):
                    self.assertEqual(run(source)['registers']['r0'], 42)
                self.assertEqual(packages.read_json(app / 'cdm-packages.lock.json'), lock)

    def test_add_by_name_works_inside_a_library(self):
        library = self.root / 'library'
        create(library, 'cdm8', 'vonNeumann', True)
        result = execute(parser().parse_args(['pub', 'add', 'byte-utils', '--path', str(library)]))
        self.assertIn('byte-utils', result['packages'])
        self.assertIn('byte-utils', packages.read_json(library / 'cdm-package.json')['dependencies'])
        self.assertFalse((library / 'cdm-packages.json').exists())

    def test_remote_catalog_snapshot_and_offline_fallback(self):
        source = Path(__file__).parents[1] / 'registry'
        def clone(args, cwd=None):
            if args[0] == 'clone':
                import shutil
                shutil.copytree(source, Path(args[-1]) / 'registry')
        with patch.dict(os.environ, {'CDMBOT_CACHE_DIR': str(self.root / 'cache'), 'CDMBOT_REGISTRY': ''}):
            with patch('cdmbot.packages.git', side_effect=clone):
                self.assertIn('byte-utils', packages.registry())
            with patch('cdmbot.packages.git', side_effect=AssertionError('Use the cached snapshot')):
                self.assertIn('byte-utils', packages.registry())
        with patch.dict(os.environ, {'CDMBOT_CACHE_DIR': str(self.root / 'empty-cache'), 'CDMBOT_REGISTRY': ''}):
            with patch('cdmbot.packages.git', side_effect=TestError('Offline')):
                catalog = packages.registry()
                self.assertIn('byte-utils', catalog)
                self.assertIn('bundled', catalog.warning)

    def test_concurrent_manifest_edit_is_preserved(self):
        app = self.root / 'consumer'
        create(app, 'cdm8', 'vonNeumann')
        previous = {'version': 1, 'dependencies': {}}
        current = {'version': 1, 'dependencies': {'byte-utils': '*'}}
        packages.save(app / 'cdm-packages.json', current)
        with self.assertRaisesRegex(TestError, 'changed during'):
            packages.restore(app, 'cdm8', changed=previous, expected=previous)
        self.assertEqual(packages.read_json(app / 'cdm-packages.json'), current)

    def test_publication_failure_restores_previous_artifacts(self):
        import os
        app = self.root / 'consumer'
        create(app, 'cdm8', 'vonNeumann')
        previous = build(app)
        snapshots = {file: Path(file).read_bytes() for file in previous['artifacts'].values()}
        source = app / 'src/main.asm'
        source.write_text(source.read_text().replace('42', '43'))
        replace = os.replace
        def fail_binary_once(source, destination):
            if Path(source).name == 'out.bin':
                raise OSError('simulated publish failure')
            return replace(source, destination)
        with patch('cdmbot.developer.os.replace', side_effect=fail_binary_once):
            with self.assertRaises(OSError):
                build(app)
        for file, contents in snapshots.items():
            self.assertEqual(Path(file).read_bytes(), contents)


if __name__ == '__main__':
    unittest.main()
