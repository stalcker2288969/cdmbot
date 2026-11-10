import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


class ProjectTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def file(self, name, contents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        return path

    def run_cli(self, *arguments):
        process = subprocess.run(
            [sys.executable, "-m", "cdmbot", *map(str, arguments), "--json"],
            capture_output=True, encoding="utf-8", timeout=45,
        )
        self.assertTrue(process.stdout, process.stderr)
        return process, json.loads(process.stdout)

    def test_example_tests_library_with_harness_and_linked_dependency(self):
        process, report = self.run_cli("project", ROOT / "examples/project/project.yaml")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(report["summary"]["passed"], 2)
        self.assertEqual(report["check_summary"]["failed"], 0)
        self.assertEqual(
            {check["name"] for check in report["checks"] if check["kind"] == "coverage"},
            {"file_percent", "line_percent", "comment_percent"},
        )
        coverage = report["coverage"]
        self.assertEqual(coverage["summary"]["files"], 3)
        self.assertEqual(coverage["directories"]["lib"]["tested_files"], 2)
        self.assertEqual(coverage["files"]["lib/delta.asm"]["executed_lines"], 6)
        self.assertIsNone(coverage["files"]["lib/unused.asm"]["line_percent"])

    def test_macro_library_as_primary_input_and_filter_statistics(self):
        library = self.file("ops.mlb", """macro increase/1
    inc $1
mend
""")
        self.file("harness.asm", """asect 0
    increase r0
    halt
end.
""")
        suite = self.file("suite.yaml", """version: 1
architecture: cdm8
build: {harness: harness.asm}
cases:
  increase: {expect: {registers: {r0: 1}}}
  filtered: {}
""")
        process, report = self.run_cli(library, suite, "--case", "increase")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(report["summary"], {
            "found": 2, "selected": 1, "launched": 1, "passed": 1,
            "failed": 0, "errors": 0, "not_run": 0, "filtered": 1,
        })

    def test_project_reports_failures_and_continues_after_invalid_cases(self):
        self.file("src/good.asm", """# Program description
asect 0
    halt  # finish
value: dc "#"
end.
""")
        self.file("src/bad.asm", "asect 0\n    unknown_instruction\nend.\n")
        self.file("suite.yaml", """version: 1
architecture: cdm8
cases:
  invalid_setup: {setup: {input: {missing_label: 1}}}
  correct: {}
  incorrect: {expect: {registers: {r0: 1}}}
""")
        config = self.file("project.yaml", """version: 1
source_roots: [src]
suites:
  good: {program: src/good.asm, suite: suite.yaml}
  broken: {program: src/bad.asm, suite: suite.yaml}
syntax:
  bad_source: {architecture: cdm8, files: [src/bad.asm]}
structure:
  directories: [missing]
""")
        junit = self.root / "report.xml"
        process, report = self.run_cli("project", config, "--junit", junit)
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(report["summary"], {
            "found": 6, "selected": 6, "launched": 2, "passed": 1,
            "failed": 1, "errors": 2, "not_run": 4, "filtered": 0,
        })
        self.assertEqual(report["check_summary"]["failed"], 2)
        self.assertIn("Cocas build failed", report["suites"]["broken"]["error"])
        self.assertEqual(report["coverage"]["files"]["src/good.asm"]["comment_percent"], 40.0)
        self.assertEqual(report["coverage"]["files"]["src/good.asm"]["mapped_lines"], 1)
        xml = ET.parse(junit).getroot()
        self.assertEqual(len(xml.findall(".//error")), 2)
        self.assertEqual(len(xml.findall(".//failure")), 3)

    def test_cdm16_line_coverage_excludes_data_and_shows_unreached_instructions(self):
        self.file("src/main.asm", """asect 0
    dc start, 0
asect 0x80
start:
    halt
    add r0, r1, r0
    halt
end.
""")
        self.file("suite.yaml", "version: 1\narchitecture: cdm16\ncases: {stop: {}}\n")
        config = self.file("project.yaml", """version: 1
source_roots: [src]
suites:
  cdm16: {program: src/main.asm, suite: suite.yaml}
""")
        process, report = self.run_cli("project", config)
        self.assertEqual(process.returncode, 0, process.stderr)
        metrics = report["coverage"]["files"]["src/main.asm"]
        self.assertEqual(metrics["mapped_lines"], 3)
        self.assertEqual(metrics["executed_lines"], 1)
        self.assertEqual(metrics["unexecuted_lines"], [6, 7])

    def test_coverage_thresholds_fail_independently_and_compare_before_rounding(self):
        self.file("src/main.asm", "# Program\nasect 0\n    halt\nend.\n")
        self.file("src/unused.asm", "asect 0\n    halt\nend.\n")
        self.file("suite.yaml", "version: 1\narchitecture: cdm8\ncases: {stop: {}}\n")
        manifest = """version: 1
source_roots: [src]
suites:
  program: {program: src/main.asm, suite: suite.yaml}
"""
        junit = self.root / "thresholds.xml"
        for files, comments, failed_metric in ((51, 0, "file_percent"), (50, 14.29, "comment_percent")):
            with self.subTest(metric=failed_metric):
                config = self.file(
                    "project.yaml", manifest + f"thresholds: {{file_percent: {files}, comment_percent: {comments}}}\n"
                )
                process, report = self.run_cli("project", config, "--junit", junit)
                self.assertEqual(process.returncode, 1, process.stderr)
                self.assertFalse(report["passed"])
                self.assertEqual(report["summary"]["passed"], 1)
                self.assertEqual(report["coverage"]["summary"]["file_percent"], 50)
                self.assertEqual(report["coverage"]["summary"]["comment_percent"], 14.29)
                checks = {check["name"]: check for check in report["checks"] if check["kind"] == "coverage"}
                self.assertEqual([name for name, check in checks.items() if check["failure"]], [failed_metric])
                xml = ET.parse(junit).getroot()
                failed = xml.find("./testsuite[@name='coverage']/testcase/failure")
                self.assertEqual(failed.text, checks[failed_metric]["failure"])

    def test_configured_threshold_requires_measurable_coverage(self):
        self.file("src/main.asm", "asect 0\n    halt\nend.\n")
        config = self.file("project.yaml", "version: 1\nsource_roots: [src]\nthresholds: {line_percent: 0}\n")
        process, report = self.run_cli("project", config)
        self.assertEqual(process.returncode, 1, process.stderr)
        check = next(check for check in report["checks"] if check["kind"] == "coverage")
        self.assertIsNone(check["actual_percent"])
        self.assertIn("unavailable", check["failure"])

    def test_rejects_invalid_coverage_thresholds(self):
        for value in ("-1", "100.1", "true", ".nan", '"20%"'):
            with self.subTest(value=value):
                config = self.file(
                    "project.yaml", f"version: 1\nsource_roots: [src]\nthresholds: {{file_percent: {value}}}\n"
                )
                process = subprocess.run(
                    [sys.executable, "-m", "cdmbot", "project", str(config)],
                    capture_output=True, encoding="utf-8", timeout=15,
                )
                self.assertEqual(process.returncode, 2, process.stderr)
                self.assertIn("thresholds.file_percent: expected a number from 0 to 100", process.stderr)

    def test_syntax_discovers_directories_root_and_explicit_files(self):
        good = "asect 0\n    halt\nend.\n"
        bad = "asect 0\n    unknown_instruction\nend.\n"
        self.file("root.asm", bad)
        self.file("src/main.asm", good)
        self.file("src/deep/bad.asm", bad)
        self.file("other/extra.asm", good)
        self.file("src/notes.txt", bad)
        self.file("src/ops.mlb", "macro increment/1\n    inc $1\nmend\n")
        config = self.file("project.yaml", """version: 1
source_roots: [src]
syntax:
  nested: {architecture: cdm8, directories: [src]}
  direct:
    architecture: cdm8
    directories: [src]
    recursive: false
    files: [other/extra.asm, src/main.asm]
  root: {architecture: cdm8, directories: [.], recursive: false}
  missing: {architecture: cdm8, directories: [does-not-exist]}
""")
        process, report = self.run_cli("project", config)
        self.assertEqual(process.returncode, 1, process.stderr)
        checks = {check["name"]: check for check in report["checks"] if check["kind"] == "syntax"}
        self.assertEqual(checks["nested"]["files"], ["src/deep/bad.asm", "src/main.asm", "src/ops.mlb"])
        self.assertIn("bad.asm", checks["nested"]["failure"])
        self.assertEqual(checks["direct"]["files"], ["other/extra.asm", "src/main.asm", "src/ops.mlb"])
        self.assertIsNone(checks["direct"]["failure"])
        self.assertEqual(checks["root"]["files"], ["root.asm"])
        self.assertIn("root.asm", checks["root"]["failure"])
        self.assertIn("Missing syntax directory", checks["missing"]["failure"])
