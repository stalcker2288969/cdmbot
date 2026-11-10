"""Adapter to the version of Cocoemu shipped with cdm-devkit."""

import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from importlib.util import find_spec
from pathlib import Path

from .model import GPRS, MEMORY_SIZE, Case, Execution, Layout, TestError, pack_flags, unpack_flags

TIMEOUT = 30


def _find_jdk(bin_directory: Path | None) -> tuple[str, str] | None:
    search = str(bin_directory) if bin_directory is not None else None
    java, javac = (shutil.which(name, path=search) for name in ("java", "javac"))
    if java is None or javac is None:
        return None
    if sys.platform == "darwin" and bin_directory is None and javac == "/usr/bin/javac":
        # Check for a registered JDK without launching Apple's Java stub.
        try:
            probe = subprocess.run(
                ["/usr/libexec/java_home", "-F"], capture_output=True, text=True, errors="replace", timeout=10
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        home = probe.stdout.strip()
        return _find_jdk(Path(home) / "bin") if probe.returncode == 0 and home else None
    for executable in (java, javac):
        try:
            probe = subprocess.run(
                [executable, "-version"], capture_output=True, text=True, errors="replace", timeout=10
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        match = re.search(r'\b(?:version\s+"|javac\s+)(\d+)', probe.stdout + probe.stderr)
        if probe.returncode or match is None or int(match[1]) < 17:
            return None
    return java, javac


def _jdk_tools() -> tuple[str, str]:
    home = os.environ.get("JAVA_HOME")
    tools = _find_jdk(Path(home) / "bin") if home else None
    tools = tools or _find_jdk(None)
    if tools is not None:
        return tools
    if find_spec("cjdk") is None:
        raise TestError("CdM-16 requires JDK 17+. Set JAVA_HOME or use the quick installer.")

    from cjdk import CjdkError, java_home

    try:
        # A provisioned tool must also work when the JDK index is offline.
        managed_home = java_home(vendor="temurin", version="21", index_ttl=float("inf"))
    except CjdkError as exc:
        raise TestError(f"Cannot prepare Temurin JDK 21: {exc}") from exc
    tools = _find_jdk(managed_home / "bin")
    if tools is None:
        raise TestError(f"Managed JDK is unusable: {managed_home}")
    return tools


def _run(command: list[str], operation: str) -> None:
    try:
        process = subprocess.run(
            command, capture_output=True, encoding="utf-8", errors="replace", timeout=TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise TestError(f"{operation} exceeded {TIMEOUT} seconds") from exc
    except OSError as exc:
        raise TestError(f"{operation}: {exc}") from exc
    if process.returncode:
        raise TestError(f"{operation} failed:\n{(process.stderr + process.stdout).strip()}")


class Cdm16Backend:
    def __init__(self, directory: Path, *, coverage: bool = False):
        wrapper = find_spec("cocoemu_server_wrapper")
        if wrapper is None or not wrapper.submodule_search_locations:
            raise TestError("Cannot find Cocoemu. Install cdm-devkit==0.2.3.")
        jar = Path(next(iter(wrapper.submodule_search_locations))) / "jar" / "cocoemu-server.jar"
        if not jar.is_file():
            raise TestError(f"Cannot find Cocoemu JAR: {jar}")
        self.directory = directory
        self.coverage = coverage
        self.java, javac = _jdk_tools()
        self.classpath = os.pathsep.join((str(directory), str(jar)))
        source = Path(__file__).with_name("Cdm16Bridge.java")
        _run(
            [javac, "--release", "17", "-cp", str(jar), "-d", str(directory), str(source)],
            "Compiling the CdM-16 bridge",
        )

    def execute(self, image: bytes, initial_ram: bytes, case: Case, layout: Layout) -> Execution:
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            root = Path(directory)
            image_path, ram_path, result = (root / name for name in ("rom", "ram", "result"))
            image_path.write_bytes(image)
            ram_path.write_bytes(initial_ram)
            mask, flags = pack_flags(case.setup.flags)
            gprs = GPRS["cdm16"]
            values = (
                case.run.entry, case.run.stop_pc, case.run.stop_steps, case.run.max_steps,
                case.setup.registers.get("sp"), mask, flags, *(case.setup.registers.get(name, 0) for name in gprs),
            )
            _run(
                [self.java, "-cp", self.classpath, "Cdm16Bridge", str(image_path), str(ram_path), str(result),
                 layout, *(str(-1 if value is None else value) for value in values), str(self.coverage)],
                f"CdM-16 case {case.name!r}",
            )
            raw = result.read_bytes()
        try:
            length = struct.unpack_from(">H", raw)[0]
            reason = raw[2:2 + length].decode("ascii")
            numbers = struct.unpack_from(">13I", raw, 2 + length)
            ram_offset = 2 + length + 52
            memory_size = MEMORY_SIZE["cdm16"]
            ram = raw[ram_offset:ram_offset + memory_size]
            visited_bits = raw[ram_offset + memory_size:]
            if len(ram) != memory_size or len(visited_bits) > memory_size // 8:
                raise ValueError("incorrect RAM or execution trace length")
        except (struct.error, ValueError) as exc:
            raise TestError(f"Invalid response from the CdM-16 bridge: {exc}") from exc
        steps, changed, *register_values, pc, sp, ps = numbers
        detail = {
            "limit": f"Reached max_steps={case.run.max_steps}",
            "cycle_limit": "Cocoemu did not finish an instruction within the cycle budget",
            "boot": "Cocoemu could not complete the reset sequence",
            "wait": "CPU is waiting for an interrupt",
            "fault": "Cocoemu reported a processor fault",
        }.get(reason, "")
        registers = dict(zip(gprs, register_values))
        registers.update(pc=pc, sp=sp)
        changed_registers = frozenset(name for i, name in enumerate(gprs) if changed & (1 << i))
        visited = frozenset(
            8 * i + bit for i, value in enumerate(visited_bits) if value for bit in range(8) if value & (1 << bit)
        )
        return Execution(reason, steps, registers, unpack_flags(ps), ram, changed_registers, detail, visited)
