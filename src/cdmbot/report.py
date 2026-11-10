import json
import re
import sys
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree as ET

from .project import ProjectReport, SyntaxResult, ThresholdCheck
from .runner import SuiteReport

XML_INVALID = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def summarize(reports: Iterable[SuiteReport]) -> dict[str, int]:
    reports = tuple(reports)
    cases = [case for report in reports for case in report.cases]
    found = sum(report.found for report in reports)
    selected = sum(report.selected for report in reports)
    launched = sum(case.started for case in cases)
    return {
        "found": found,
        "selected": selected,
        "launched": launched,
        "passed": sum(case.passed for case in cases),
        "failed": sum(bool(case.failures) for case in cases),
        "errors": sum(case.error is not None for case in cases) + sum(report.error is not None for report in reports),
        "not_run": selected - launched,
        "filtered": found - selected,
    }


def summary_line(summary: dict[str, int]) -> str:
    return (
        "{found} found, {selected} selected, {launched} launched, {passed} passed, "
        "{failed} failed, {errors} errors, {not_run} not run, {filtered} filtered"
    ).format(**summary)


def suite_data(report: SuiteReport) -> dict:
    return {
        "passed": report.exit_code == 0,
        "summary": summarize((report,)),
        "error": report.error,
        "cases": [{**asdict(case), "passed": case.passed} for case in report.cases],
    }


def render_json(report: SuiteReport) -> str:
    return json.dumps({"version": 1, **suite_data(report)}, ensure_ascii=True, indent=2)


def print_suite(report: SuiteReport, *, summary: bool = True, json_output: bool = False) -> None:
    if report.error is not None:
        print(f"cdmbot: {report.error}", file=sys.stderr)
    for case in report.cases:
        if case.error is not None:
            print(f"ERROR {case.name}: {case.error}", file=sys.stderr)
        elif not json_output:
            status = "PASS" if case.passed else "FAIL"
            print(f"{status} {case.name} ({case.steps} step(s))")
            for failure in case.failures:
                print(f"  {failure}")
    if summary and not json_output:
        print(summary_line(summarize((report,))))


def render_project_json(report: ProjectReport) -> str:
    failed = sum(bool(check.failure) for check in report.checks)
    return json.dumps({
        "version": 1,
        "passed": report.exit_code == 0,
        "summary": summarize(report.suites.values()),
        "suites": {name: suite_data(suite) for name, suite in report.suites.items()},
        "checks": [asdict(check) for check in report.checks],
        "check_summary": {"total": len(report.checks), "passed": len(report.checks) - failed, "failed": failed},
        "coverage": report.coverage,
    }, ensure_ascii=True, indent=2)


def _percent(value: float | None) -> str:
    return f"{value:.2f}%" if value is not None else "n/a"


def print_project(report: ProjectReport) -> None:
    for name, suite in report.suites.items():
        print(f"Suite {name}")
        print_suite(suite)
    for check in report.checks:
        status = "FAIL" if check.failure else "PASS"
        details = ""
        if isinstance(check, ThresholdCheck) and not check.failure:
            details = f" ({_percent(check.actual_percent)}, minimum {check.minimum_percent}%)"
        elif isinstance(check, SyntaxResult) and check.files:
            details = f" ({len(check.files)} file(s))"
        print(f"{status} {check.kind}: {check.name}{details}")
        if check.failure:
            print(f"  {check.failure}")
    groups = (("Project", report.coverage["summary"]), *report.coverage["directories"].items())
    for name, metrics in groups:
        print(
            f"{name}: {metrics['tested_files']}/{metrics['files']} files executed "
            f"({_percent(metrics['file_percent'])}), {metrics['executed_lines']}/{metrics['mapped_lines']} "
            f"mapped lines ({_percent(metrics['line_percent'])}), comments {_percent(metrics['comment_percent'])}"
        )
    for name, metrics in report.coverage["files"].items():
        execution = (
            f"{metrics['executed_lines']}/{metrics['mapped_lines']} mapped lines executed"
            if metrics["mapped_lines"] else "no mapped instructions"
        )
        print(
            f"  {name}: {execution}, "
            f"comments {_percent(metrics['comment_percent'])}"
        )
        if metrics["unexecuted_lines"]:
            print("    Unexecuted lines: " + ", ".join(map(str, metrics["unexecuted_lines"])))
    print(summary_line(summarize(report.suites.values())))
    failed = sum(bool(check.failure) for check in report.checks)
    print(f"{len(report.checks) - failed} project checks passed, {failed} failed")


def _junit_suite(name: str, report: SuiteReport) -> ET.Element:
    stats = summarize((report,))
    root = ET.Element("testsuite", {
        "name": name,
        "tests": str(len(report.cases) + int(report.error is not None)),
        "failures": str(stats["failed"]),
        "errors": str(stats["errors"]),
        "time": f"{sum(case.seconds for case in report.cases):.6f}",
    })
    if report.error is not None:
        node = ET.SubElement(root, "testcase", {"classname": name, "name": "suite setup"})
        ET.SubElement(node, "error").text = report.error
    for case in report.cases:
        node = ET.SubElement(root, "testcase", {
            "classname": name, "name": case.name, "time": f"{case.seconds:.6f}",
        })
        if case.error is not None:
            ET.SubElement(node, "error").text = case.error
        elif case.failures:
            ET.SubElement(node, "failure", {"message": case.failures[0]}).text = "\n".join(case.failures)
    return root


def _write_xml(path: Path, root: ET.Element) -> None:
    for node in root.iter():
        if node.text:
            node.text = XML_INVALID.sub("\ufffd", node.text)
        for key, value in node.attrib.items():
            node.attrib[key] = XML_INVALID.sub("\ufffd", value)
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_junit(path: Path, name: str, report: SuiteReport) -> None:
    _write_xml(path, _junit_suite(name, report))


def write_project_junit(path: Path, report: ProjectReport) -> None:
    root = ET.Element("testsuites")
    for name, suite in report.suites.items():
        root.append(_junit_suite(name, suite))
    for kind in ("syntax", "structure", "coverage"):
        checks = [check for check in report.checks if check.kind == kind]
        if not checks:
            continue
        group = ET.SubElement(root, "testsuite", {
            "name": kind, "tests": str(len(checks)), "errors": "0",
            "failures": str(sum(bool(check.failure) for check in checks)),
        })
        for check in checks:
            node = ET.SubElement(group, "testcase", {"classname": kind, "name": check.name})
            if check.failure:
                ET.SubElement(node, "failure").text = check.failure
    _write_xml(path, root)
