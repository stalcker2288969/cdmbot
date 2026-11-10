from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .assembler import check_syntax
from .config import integer, load_suite, load_yaml, mapping, read_architecture, read_path, read_paths
from .coverage import COVERAGE_COUNTS, Coverage, totals
from .model import Architecture, TestError
from .runner import SuiteReport, run_report

SOURCE_SUFFIXES = {".asm", ".mlb"}


@dataclass(frozen=True)
class SyntaxGroup:
    architecture: Architecture
    files: tuple[Path, ...]
    directories: tuple[Path, ...]
    recursive: bool


@dataclass(frozen=True)
class Project:
    root: Path
    source_roots: tuple[Path, ...]
    suites: dict[str, tuple[Path, Path]]
    syntax: dict[str, SyntaxGroup]
    files: tuple[Path, ...]
    directories: tuple[Path, ...]
    thresholds: dict[str, int | float] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckResult:
    name: str
    kind: str
    failure: str | None = None


@dataclass(frozen=True)
class SyntaxResult(CheckResult):
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThresholdCheck(CheckResult):
    minimum_percent: int | float = 0
    actual_percent: float | None = None


@dataclass(frozen=True)
class ProjectReport:
    suites: dict[str, SuiteReport]
    checks: tuple[CheckResult, ...]
    coverage: dict

    @property
    def exit_code(self) -> int:
        return max(
            int(any(check.failure for check in self.checks)),
            max((report.exit_code for report in self.suites.values()), default=0),
        )


def named_entries(raw, location):
    for name, value in mapping(raw, location).items():
        if not isinstance(name, str) or not name.strip() or not name.isprintable():
            raise TestError(f"{location}: names must be nonempty printable strings")
        yield name, value


def load_project(path: Path) -> Project:
    raw = mapping(
        load_yaml(path), "project", {"version", "source_roots", "suites", "syntax", "structure", "thresholds"},
    )
    integer(raw.get("version"), "version", 1, 1)
    root = path.resolve().parent
    source_roots = read_paths(raw.get("source_roots", []), root, "source_roots")
    if not source_roots:
        raise TestError("source_roots: list at least one source directory")
    suites = {}
    for name, value in named_entries(raw.get("suites", {}), "suites"):
        field = f"suites.{name}"
        entry = mapping(value, field, {"program", "suite"})
        suites[name] = tuple(read_path(entry.get(key), root, f"{field}.{key}") for key in ("program", "suite"))
    syntax = {}
    for name, value in named_entries(raw.get("syntax", {}), "syntax"):
        field = f"syntax.{name}"
        entry = mapping(value, field, {"architecture", "files", "directories", "recursive"})
        architecture = read_architecture(entry.get("architecture"), f"{field}.architecture")
        files = read_paths(entry.get("files", []), root, f"{field}.files")
        directories = read_paths(entry.get("directories", []), root, f"{field}.directories")
        if not (files or directories):
            raise TestError(f"{field}: list files, directories, or both")
        if any(file.suffix.lower() not in SOURCE_SUFFIXES for file in files):
            raise TestError(f"{field}.files: expected .asm or .mlb source files")
        recursive = entry.get("recursive", True)
        if type(recursive) is not bool:
            raise TestError(f"{field}.recursive: expected true or false")
        syntax[name] = SyntaxGroup(architecture, files, directories, recursive)
    structure = mapping(raw.get("structure", {}), "structure", {"files", "directories"})
    thresholds = mapping(raw.get("thresholds", {}), "thresholds", COVERAGE_COUNTS)
    for metric, minimum in thresholds.items():
        if type(minimum) not in (int, float) or not 0 <= minimum <= 100:
            raise TestError(f"thresholds.{metric}: expected a number from 0 to 100")
    return Project(
        root, tuple(dict.fromkeys(source_roots)), suites, syntax,
        read_paths(structure.get("files", []), root, "structure.files"),
        read_paths(structure.get("directories", []), root, "structure.directories"),
        thresholds,
    )


def _display(path: Path, root: Path) -> str:
    return (path.relative_to(root) if path.is_relative_to(root) else path).as_posix()


def _discover_sources(directory: Path, recursive: bool = True) -> set[Path]:
    candidates = directory.rglob("*") if recursive else directory.iterdir()
    return {
        path.resolve() for path in candidates if path.suffix.lower() in SOURCE_SUFFIXES and path.is_file()
    }


def _syntax_files(group: SyntaxGroup, root: Path) -> tuple[Path, ...]:
    discovered = set()
    for directory in group.directories:
        name = _display(directory, root)
        if not directory.is_dir():
            raise TestError(f"Missing syntax directory: {name}")
        files = _discover_sources(directory, group.recursive)
        if not files:
            raise TestError(f"No .asm or .mlb files found in syntax directory: {name}")
        discovered.update(files)
    return tuple(dict.fromkeys((*group.files, *sorted(discovered))))


def _check_threshold(metric: str, minimum: int | float, summary: dict) -> ThresholdCheck:
    part_key, total_key = COVERAGE_COUNTS[metric]
    part, total = summary[part_key], summary[total_key]
    actual = 100 * part / total if total else None
    failure = None
    if actual is None:
        failure = f"Coverage is unavailable (minimum {minimum}%)"
    elif 100 * part < Fraction(str(minimum)) * total:
        failure = f"Coverage {actual}% ({part}/{total}) is below the minimum of {minimum}%"
    return ThresholdCheck(metric, "coverage", failure, minimum, actual)


def run_project(project: Project, *, jobs: int = 1) -> ProjectReport:
    if not 1 <= jobs <= 32:
        raise TestError("jobs must be in 1..32")
    checks = []
    for kind, paths in (("file", project.files), ("directory", project.directories)):
        for path in paths:
            name = _display(path, project.root)
            exists = path.is_file() if kind == "file" else path.is_dir()
            checks.append(CheckResult(f"{kind} {name}", "structure", None if exists else f"Missing {kind}: {name}"))

    inventory = {}
    for directory in project.source_roots:
        name = _display(directory, project.root)
        files = _discover_sources(directory)
        inventory.setdefault(name, set()).update(files)
        for path in files:
            for parent in path.parents:
                if parent == directory or not parent.is_relative_to(directory):
                    break
                inventory.setdefault(_display(parent, project.root), set()).add(path)
        failure = None
        if not files:
            failure = f"No .asm or .mlb files found in {name}" if directory.is_dir() else f"Missing source root: {name}"
        checks.append(CheckResult(f"source root {name}", "structure", failure))

    for name, group in project.syntax.items():
        files = ()
        failure = None
        try:
            files = _syntax_files(group, project.root)
            check_syntax(group.architecture, files)
        except (TestError, OSError) as exc:
            failure = str(exc)
        checks.append(SyntaxResult(name, "syntax", failure, tuple(_display(path, project.root) for path in files)))

    coverage = Coverage()
    suites = {}
    for name, (program, suite_path) in project.suites.items():
        try:
            suite = load_suite(suite_path)
        except (TestError, OSError, ValueError) as exc:
            suites[name] = SuiteReport(0, 0, error=str(exc))
        else:
            suites[name] = run_report(program, suite, jobs=jobs, coverage=coverage)

    file_reports = {}
    for path in sorted(set().union(*inventory.values())):
        name = _display(path, project.root)
        try:
            file_reports[path] = coverage.file_report(path)
        except (OSError, UnicodeError) as exc:
            checks.append(CheckResult(f"read {name}", "structure", str(exc)))
    summary = totals(list(file_reports.values()))
    checks.extend(_check_threshold(metric, minimum, summary) for metric, minimum in project.thresholds.items())
    return ProjectReport(suites, tuple(checks), {
        "summary": summary,
        "directories": {
            name: totals([file_reports[path] for path in paths if path in file_reports])
            for name, paths in sorted(inventory.items())
        },
        "files": {_display(path, project.root): metrics for path, metrics in file_reports.items()},
    })
