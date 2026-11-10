import argparse
import sys
from pathlib import Path

from .config import load_suite
from .model import TestError
from .project import load_project, run_project
from .report import print_project, print_suite, render_json, render_project_json, write_junit, write_project_junit
from .runner import run_report, select_cases


def report_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--jobs", type=int, default=1, metavar="N", help="run up to N cases concurrently (1..32)")
    parser.add_argument("--json", action="store_true", help="emit a JSON report to stdout")
    parser.add_argument("--junit", type=Path, metavar="FILE", help="write a JUnit XML report")


def project_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="cdmbot project", description="Check a project's suites, layout and coverage.")
    parser.add_argument("config", type=Path, help="YAML project manifest")
    report_options(parser)
    options = parser.parse_args(argv)
    report = run_project(load_project(options.config), jobs=options.jobs)
    if options.junit:
        write_project_junit(options.junit, report)
    if options.json:
        print(render_project_json(report))
    else:
        print_project(report)
    return report.exit_code


def suite_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cdmbot", description="Assemble a CdM program and test it against a YAML suite.",
        epilog="Use cdmbot project --help for project checks and coverage.",
    )
    parser.add_argument("program", type=Path, help="assembly source, library or prebuilt .bin image")
    parser.add_argument("suite", type=Path, help="YAML suite")
    parser.add_argument("--case", action="append", default=[], metavar="PATTERN", help="select by glob (repeatable)")
    parser.add_argument("--list", action="store_true", help="list selected cases without building or running")
    report_options(parser)
    options = parser.parse_args(argv)

    suite = load_suite(options.suite)
    patterns = tuple(options.case)
    if options.list:
        for case in select_cases(suite, patterns):
            print(case.name)
        return 0
    report = run_report(options.program, suite, patterns=patterns, jobs=options.jobs)
    if options.junit:
        write_junit(options.junit, options.suite.stem, report)
    if options.json:
        print(render_json(report))
    print_suite(report, json_output=options.json)
    return report.exit_code


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if arguments and arguments[0] == "project":
            return project_main(arguments[1:])
        return suite_main(arguments)
    except (TestError, OSError, ValueError) as exc:
        print(f"cdmbot: {exc}", file=sys.stderr)
        return 2
