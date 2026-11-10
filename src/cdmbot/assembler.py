import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from os import environ
from pathlib import Path

from .model import MEMORY_SIZE, Architecture, Suite, TestError

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class Build:
    image: bytes
    symbols: dict[str, list[int]] = field(default_factory=dict)
    locations: dict[int, tuple[Path, int]] = field(default_factory=dict)


def _require_files(paths: tuple[Path, ...]) -> None:
    for path in paths:
        if not path.is_file():
            raise TestError(f"Input file does not exist: {path}")


def _run_cocas(command: list[str], architecture: Architecture, args: list[str], cwd: Path) -> None:
    try:
        process = subprocess.run(
            [sys.executable, "-m", *command, "-t", architecture, *args],
            capture_output=True, encoding="utf-8", errors="replace",
            env={**environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            timeout=30, check=False, cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise TestError("Cocas build exceeded 30 seconds") from exc
    if process.returncode:
        diagnostic = ANSI_ESCAPE.sub("", process.stderr + process.stdout).strip()
        raise TestError(f"Cocas build failed:\n{diagnostic or f'exit code {process.returncode}'}")


def check_syntax(architecture: Architecture, files: tuple[Path, ...]) -> None:
    _require_files(files)
    with tempfile.TemporaryDirectory(prefix="cdmbot-syntax-") as directory:
        _run_cocas(["cocas.main"], architecture, ["-c", *map(str, files)], Path(directory))


def build_program(program: Path, suite: Suite, labels: set[str], *, debug: bool = False) -> Build:
    program = program.resolve()
    paths = (program, *suite.sources)
    if suite.harness is not None:
        paths = (suite.harness, *paths)
    if len(set(paths)) != len(paths):
        raise TestError("The build lists the same input file more than once")
    _require_files(paths)
    if all(path.suffix.lower() == ".mlb" for path in paths):
        raise TestError("Macro libraries require an executable harness in build.harness or build.sources")
    size = MEMORY_SIZE[suite.architecture]
    # Cocas writes a full 64 KiB binary even for the 256-byte CdM-8 target.
    file_limit = MEMORY_SIZE["cdm16"]
    symbols = {}
    locations = {}
    try:
        if program.suffix.lower() == ".bin":
            if labels:
                raise TestError("setup.input requires assembly labels; a prebuilt .bin has no symbol table")
            if suite.sources or suite.harness is not None:
                raise TestError("build.sources and build.harness require assembly input instead of .bin")
            with program.open("rb") as file:
                image = file.read(file_limit + 1)
        else:
            with tempfile.TemporaryDirectory(prefix="cdmbot-build-") as directory:
                output = Path(directory) / "program.bin"
                command = ["cocas.main"]
                if labels:
                    symbol_file = output.with_name("labels.json")
                    symbol_file.write_text(json.dumps(sorted(labels)), encoding="utf-8")
                    command = ["cdmbot.cocas_symbols", str(symbol_file)]
                debug_file = output.with_name("debug.json")
                flags = ["--debug", str(debug_file)] if debug else []
                _run_cocas(command, suite.architecture, [*flags, "-o", str(output), *map(str, paths)], Path.cwd())
                if not output.is_file():
                    raise TestError("Cocas build failed: no image was produced")
                with output.open("rb") as file:
                    image = file.read(file_limit + 1)
                if labels:
                    symbols = json.loads(symbol_file.read_text(encoding="utf-8"))
                if debug:
                    data = json.loads(debug_file.read_text(encoding="utf-8"))
                    files = [Path(name).resolve() for name in data["files"]]
                    locations = {int(pc): (files[loc["f"]], loc["l"]) for pc, loc in data["codeLocations"].items()}
    except OSError as exc:
        raise TestError(f"Cannot build/read program: {exc}") from exc
    if size < len(image) == file_limit and not any(image[size:]):
        image = image[:size]
    if not 0 < len(image) <= size:
        raise TestError(f"Expected a nonempty {suite.architecture} image of at most {size} bytes")
    return Build(image, symbols, locations)
