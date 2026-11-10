from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

Architecture = Literal["cdm8", "cdm16"]
Layout = Literal["unified", "harvard"]
FLAGS = {"c": 8, "v": 4, "z": 2, "n": 1}
GPRS = {
    "cdm8": ("r0", "r1", "r2", "r3"),
    "cdm16": ("r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7"),
}
MEMORY_SIZE = {"cdm8": 256, "cdm16": 65536}
WORD_BYTES = {"cdm8": 1, "cdm16": 2}


class TestError(Exception):
    """A user-facing configuration, assembly, or infrastructure error."""


@dataclass(frozen=True)
class State:
    registers: dict[str, int] = field(default_factory=dict)
    flags: dict[str, int] = field(default_factory=dict)
    memory: dict[int, bytes] = field(default_factory=dict)
    stack: tuple[int, ...] = ()
    inputs: dict[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class Run:
    max_steps: int = 10_000
    entry: int | None = None
    stop_pc: int | None = None
    stop_steps: int | None = None

    @property
    def expected_stop(self) -> str:
        if self.stop_pc is not None:
            return "pc"
        if self.stop_steps is not None:
            return "steps"
        return "halt"


@dataclass(frozen=True)
class Case:
    name: str
    setup: State = field(default_factory=State)
    run: Run = field(default_factory=Run)
    expect: State = field(default_factory=State)
    preserve_registers: tuple[str, ...] = ()
    allowed_register_changes: tuple[str, ...] | None = None
    preserve_memory: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class Suite:
    architecture: Architecture
    layout: Layout
    sources: tuple[Path, ...]
    cases: tuple[Case, ...]
    harness: Path | None = None


@dataclass(frozen=True)
class Execution:
    reason: str
    steps: int
    registers: dict[str, int]
    flags: dict[str, int]
    memory: bytes
    changed_registers: frozenset[str]
    detail: str = ""
    visited: frozenset[int] = frozenset()


class Backend(Protocol):
    def execute(self, image: bytes, initial_ram: bytes, case: Case, layout: Layout) -> Execution:
        """Run a case on a fresh CPU with the supplied ROM image and RAM."""


def pack_flags(flags: dict[str, int]) -> tuple[int, int]:
    mask = sum(FLAGS[name] for name in flags)
    value = sum(FLAGS[name] for name, bit in flags.items() if bit)
    return mask, value


def unpack_flags(value: int) -> dict[str, int]:
    return {name: int(bool(value & bit)) for name, bit in FLAGS.items()}
