import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
from pathlib import Path
from time import perf_counter

from .assembler import Build, build_program
from .cdm8 import Cdm8Backend
from .cdm16 import Cdm16Backend
from .coverage import Coverage
from .model import MEMORY_SIZE, WORD_BYTES, Backend, Case, Execution, State, Suite, TestError


@dataclass(frozen=True)
class CaseResult:
    name: str
    reason: str
    steps: int
    registers: dict[str, int]
    flags: dict[str, int]
    changed_registers: tuple[str, ...]
    failures: tuple[str, ...]
    seconds: float
    error: str | None = None
    started: bool = True

    @property
    def passed(self) -> bool:
        return self.error is None and not self.failures


@dataclass(frozen=True)
class SuiteReport:
    found: int
    selected: int
    cases: tuple[CaseResult, ...] = ()
    error: str | None = None

    @property
    def exit_code(self) -> int:
        if self.error is not None or any(case.error is not None for case in self.cases):
            return 2
        return int(any(not case.passed for case in self.cases))


def select_cases(suite: Suite, patterns: tuple[str, ...] = ()) -> tuple[Case, ...]:
    for pattern in patterns:
        if not any(fnmatchcase(case.name, pattern) for case in suite.cases):
            raise TestError(f"No case matches {pattern!r}")
    return tuple(
        case for case in suite.cases if not patterns or any(fnmatchcase(case.name, p) for p in patterns)
    )


def memory_difference(address: int, expected: bytes, actual: bytes) -> str | None:
    if expected == actual:
        return None
    for offset, (wanted, found) in enumerate(zip(expected, actual)):
        if wanted != found:
            return f"memory[0x{address + offset:x}]: expected 0x{wanted:02x}, got 0x{found:02x}"
    return f"memory[0x{address:x}]: expected {len(expected)} bytes, got {len(actual)}"


def check(case: Case, execution: Execution, initial_ram: bytes) -> tuple[str, ...]:
    failures = []
    if execution.reason != case.run.expected_stop:
        detail = f" ({execution.detail})" if execution.detail else ""
        failures.append(
            f"Stop: expected {case.run.expected_stop}, got {execution.reason}"
            f" at PC 0x{execution.registers['pc']:x} after {execution.steps} step(s){detail}"
        )
    for label, expected, actual in (
        ("register", case.expect.registers, execution.registers),
        ("flag", case.expect.flags, execution.flags),
    ):
        for name, wanted in expected.items():
            found = actual[name]
            if found != wanted:
                failures.append(f"{label} {name}: expected 0x{wanted:x}, got 0x{found:x}")
    for address, wanted in case.expect.memory.items():
        failure = memory_difference(address, wanted, execution.memory[address:address + len(wanted)])
        if failure:
            failures.append(failure)
    for name in case.preserve_registers:
        wanted, found = case.setup.registers.get(name, 0), execution.registers[name]
        if wanted != found:
            failures.append(f"register {name} was not preserved: initial 0x{wanted:x}, got 0x{found:x}")
    if case.allowed_register_changes is not None:
        forbidden = execution.changed_registers.difference(case.allowed_register_changes)
        if forbidden:
            failures.append(f"Registers changed outside the allowlist: {', '.join(sorted(forbidden))}")
    for address, length in case.preserve_memory:
        span = slice(address, address + length)
        failure = memory_difference(address, initial_ram[span], execution.memory[span])
        if failure:
            failures.append(f"Memory was not preserved: {failure}")
    return tuple(failures)


def prepare_case(case: Case, suite: Suite, symbols: dict[str, list[int]]) -> Case:
    if not (case.setup.stack or case.setup.inputs):
        return case
    location = f"cases.{case.name}.setup"
    size, word_bytes = MEMORY_SIZE[suite.architecture], WORD_BYTES[suite.architecture]
    registers = dict(case.setup.registers)
    blocks = [
        (address, data, f"{location}.memory[0x{address:x}]")
        for address, data in case.setup.memory.items()
    ]
    for name, data in case.setup.inputs.items():
        matches = symbols.get(name, [])
        field = f"{location}.input.{name}"
        if not matches:
            raise TestError(f"{field}: label {name!r} was not found in the linked program")
        if len(matches) != 1:
            raise TestError(f"{field}: ambiguous label {name!r}; use a unique label")
        blocks.append((matches[0], data, field))
    if case.setup.stack:
        data = b"".join(value.to_bytes(word_bytes, "little") for value in case.setup.stack)
        sp = registers.get("sp", size - len(data))
        if sp % word_bytes:
            raise TestError(f"{location}.stack: SP must be aligned to {word_bytes} bytes")
        registers["sp"] = sp
        blocks.append((sp, data, f"{location}.stack"))

    memory = {}
    end, previous = 0, ""
    for address, data, field in sorted(blocks, key=lambda block: block[0]):
        if address < 0 or address + len(data) > size:
            raise TestError(f"{field}: initial data does not fit in RAM")
        if address < end:
            raise TestError(f"{field}: overlaps {previous} at address 0x{address:x}")
        memory[address] = data
        end, previous = address + len(data), field
    return replace(case, setup=State(registers, case.setup.flags, memory))


def _run_cases(
    build: Build, suite: Suite, cases: tuple[Case, ...], backend: Backend, jobs: int,
    coverage: Coverage | None,
) -> tuple[CaseResult, ...]:
    base = build.image if suite.layout == "unified" else b""
    base = base.ljust(MEMORY_SIZE[suite.architecture], b"\0")

    def run_case(case: Case) -> tuple[CaseResult, frozenset[int]]:
        start = perf_counter()
        started = False
        try:
            case = prepare_case(case, suite, build.symbols)
            ram = bytearray(base)
            for address, values in case.setup.memory.items():
                ram[address:address + len(values)] = values
            initial_ram = bytes(ram)
            started = True
            execution = backend.execute(build.image, initial_ram, case, suite.layout)
        except (TestError, OSError, ValueError) as exc:
            return CaseResult(
                case.name, "error", 0, {}, {}, (), (), perf_counter() - start, str(exc), started,
            ), frozenset()
        failures = check(case, execution, initial_ram)
        return CaseResult(
            case.name, execution.reason, execution.steps, execution.registers, execution.flags,
            tuple(sorted(execution.changed_registers)), failures, perf_counter() - start,
        ), execution.visited

    if jobs == 1 or len(cases) == 1:
        outcomes = tuple(map(run_case, cases))
    else:
        with ThreadPoolExecutor(max_workers=min(jobs, len(cases))) as workers:
            outcomes = tuple(workers.map(run_case, cases))
    if coverage is not None:
        visited = set().union(*(addresses for _, addresses in outcomes))
        coverage.record(build.locations, visited)
    return tuple(result for result, _ in outcomes)


def run_report(
    program: Path,
    suite: Suite,
    *,
    patterns: tuple[str, ...] = (),
    backend: Backend | None = None,
    jobs: int = 1,
    coverage: Coverage | None = None,
) -> SuiteReport:
    if not 1 <= jobs <= 32:
        raise TestError("jobs must be in 1..32")
    found = len(suite.cases)
    selected = 0
    try:
        cases = select_cases(suite, patterns)
        selected = len(cases)
        labels = {label for case in cases for label in case.setup.inputs}
        build = build_program(program, suite, labels, debug=coverage is not None)
        if coverage is not None:
            coverage.register(build.locations)
        if backend is not None:
            results = _run_cases(build, suite, cases, backend, jobs, coverage)
        elif suite.architecture == "cdm8":
            results = _run_cases(build, suite, cases, Cdm8Backend(coverage=coverage is not None), jobs, coverage)
        else:
            with tempfile.TemporaryDirectory(prefix="cdmbot-cdm16-") as directory:
                cpu = Cdm16Backend(Path(directory), coverage=coverage is not None)
                results = _run_cases(build, suite, cases, cpu, jobs, coverage)
    except (TestError, OSError, ValueError) as exc:
        return SuiteReport(found, selected, error=str(exc))
    return SuiteReport(found, selected, results)


def run_suite(
    program: Path,
    suite: Suite,
    *,
    patterns: tuple[str, ...] = (),
    backend: Backend | None = None,
    jobs: int = 1,
) -> tuple[CaseResult, ...]:
    """Build once and run each selected case from an independent machine state."""
    report = run_report(program, suite, patterns=patterns, backend=backend, jobs=jobs)
    if report.error is not None:
        raise TestError(report.error)
    for case in report.cases:
        if case.error is not None:
            raise TestError(case.error)
    return report.cases
