from pathlib import Path

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode

from .model import FLAGS, GPRS, MEMORY_SIZE, WORD_BYTES, Case, Run, State, Suite, TestError

MAX_YAML_BYTES = 1_048_576
MAX_STEPS = 10_000_000


class SuiteLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(AliasEvent):
            event = self.peek_event()
            raise yaml.YAMLError(f"line {event.start_mark.line + 1}: YAML aliases are disabled")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        if isinstance(node, MappingNode):
            seen = set()
            for key_node, _ in node.value:
                key = self.construct_object(key_node, deep=deep)
                if type(key) not in (str, int):
                    raise yaml.YAMLError(f"line {key_node.start_mark.line + 1}: invalid mapping key")
                if key in seen:
                    raise yaml.YAMLError(f"line {key_node.start_mark.line + 1}: duplicate key {key!r}")
                seen.add(key)
        return super().construct_mapping(node, deep=deep)


def mapping(value, path, keys=None):
    if not isinstance(value, dict):
        raise TestError(f"{path}: expected a mapping")
    if keys is not None:
        for key in value:
            if key not in keys:
                raise TestError(f"{path}: unknown key {key!r}")
    return value


def sequence(value, path):
    if not isinstance(value, list):
        raise TestError(f"{path}: expected a list")
    return value


def integer(value, path, upper, lower=0):
    if type(value) is not int or not lower <= value <= upper:
        raise TestError(f"{path}: expected an integer in {lower}..{upper}")
    return value


def register_name(name, allowed, path):
    if name == "fp" and "r7" in allowed:
        name = "r7"
    if name not in allowed:
        raise TestError(f"{path}: unknown register {name!r}")
    return name


def register_list(value, allowed, path):
    names = tuple(register_name(name, allowed, path) for name in sequence(value, path))
    if len(set(names)) != len(names):
        raise TestError(f"{path}: duplicate register")
    return names


def read_inputs(raw, path, size, word_bytes):
    inputs = {}
    for label, value in mapping(raw, path).items():
        if not isinstance(label, str) or not label or not label.isprintable() or any(ch.isspace() for ch in label):
            raise TestError(f"{path}: expected an assembly label, got {label!r}")
        location = f"{path}.{label}"
        if isinstance(value, str):
            try:
                payload = value.encode("utf-8")
            except UnicodeError as exc:
                raise TestError(f"{location}: invalid UTF-8 text") from exc
        elif isinstance(value, list):
            payload = bytes(integer(item, f"{location}[{i}]", 255) for i, item in enumerate(value))
        else:
            payload = integer(value, location, size - 1).to_bytes(word_bytes, "little")
        if not 0 < len(payload) <= size:
            raise TestError(f"{location}: expected between 1 and {size} bytes")
        inputs[label] = payload
    return inputs


def read_state(raw, path, architecture, *, expectation=False):
    gprs, size = GPRS[architecture], MEMORY_SIZE[architecture]
    keys = {"registers", "flags", "memory"}
    if not expectation:
        keys.update(("stack", "input"))
    raw = mapping(raw, path, keys)
    allowed = (*gprs, "sp", "pc") if expectation else (*gprs, "sp")
    registers = {}
    for key, value in mapping(raw.get("registers", {}), f"{path}.registers").items():
        name = register_name(key, allowed, f"{path}.registers")
        if name in registers:
            raise TestError(f"{path}.registers: duplicate register {name}")
        registers[name] = integer(value, f"{path}.registers.{key}", size - 1)
    flag_values = mapping(raw.get("flags", {}), f"{path}.flags", FLAGS)
    flags = {name: integer(value, f"{path}.flags.{name}", 1) for name, value in flag_values.items()}
    memory = {}
    end = 0
    addresses = mapping(raw.get("memory", {}), f"{path}.memory")
    for address in addresses:
        integer(address, f"{path}.memory address {address!r}", size - 1)
    for address, value in sorted(addresses.items()):
        location = f"{path}.memory[0x{address:x}]"
        values = value if isinstance(value, list) else [value]
        if not values or address + len(values) > size:
            raise TestError(f"{location}: expected a nonempty byte sequence within memory")
        if address < end:
            raise TestError(f"{location}: overlaps another memory block")
        memory[address] = bytes(integer(item, f"{location}[{i}]", 255) for i, item in enumerate(values))
        end = address + len(values)
    stack = tuple(
        integer(item, f"{path}.stack[{i}]", size - 1)
        for i, item in enumerate(sequence(raw.get("stack", []), f"{path}.stack"))
    )
    inputs = read_inputs(raw.get("input", {}), f"{path}.input", size, WORD_BYTES[architecture])
    return State(registers, flags, memory, stack, inputs)


def read_run(raw, path, size):
    raw = mapping(raw, path, {"entry", "until", "max_steps"})
    max_steps = integer(raw.get("max_steps", Run.max_steps), f"{path}.max_steps", MAX_STEPS, 1)

    def pc(value, location):
        address = integer(value, location, size - 1)
        if size == MEMORY_SIZE["cdm16"] and address % 2:
            raise TestError(f"{location}: CdM-16 instruction addresses must be even")
        return address

    entry = pc(raw["entry"], f"{path}.entry") if "entry" in raw else None
    stop = raw.get("until", "halt")
    if stop == "halt":
        return Run(max_steps, entry)
    stop = mapping(stop, f"{path}.until", {"pc", "steps"})
    if set(stop) == {"pc"}:
        return Run(max_steps, entry, stop_pc=pc(stop["pc"], f"{path}.until.pc"))
    if set(stop) == {"steps"}:
        steps = integer(stop["steps"], f"{path}.until.steps", max_steps)
        return Run(max_steps, entry, stop_steps=steps)
    raise TestError(f"{path}.until: expected 'halt', {{pc: address}} or {{steps: count}}")


def merge_case(defaults, override):
    merged = {**defaults, **override}
    for section, state_fields in (
        ("setup", ("registers", "flags", "memory", "input")),
        ("expect", ("registers", "flags", "memory")),
        ("run", ()),
    ):
        base, extra = defaults.get(section), override.get(section)
        if isinstance(base, dict) and isinstance(extra, dict):
            merged[section] = {**base, **extra}
            for key in state_fields:
                if isinstance(base.get(key), dict) and isinstance(extra.get(key), dict):
                    merged[section][key] = {**base[key], **extra[key]}
    return merged


CASE_KEYS = {"setup", "run", "expect", "preserve_registers", "allowed_register_changes", "preserve_memory"}


def read_case(name, raw, path, architecture):
    gprs, size = GPRS[architecture], MEMORY_SIZE[architecture]
    ranges = []
    for i, value in enumerate(sequence(raw.get("preserve_memory", []), f"{path}.preserve_memory")):
        location = f"{path}.preserve_memory[{i}]"
        value = mapping(value, location, {"address", "length"})
        start = integer(value.get("address"), f"{location}.address", size - 1)
        length = integer(value.get("length"), f"{location}.length", size - start, 1)
        ranges.append((start, length))
    preserved = register_list(raw.get("preserve_registers", []), (*gprs, "sp"), f"{path}.preserve_registers")
    allowed = None
    if "allowed_register_changes" in raw:
        allowed = register_list(raw["allowed_register_changes"], gprs, f"{path}.allowed_register_changes")
    return Case(
        name=name,
        setup=read_state(raw.get("setup", {}), f"{path}.setup", architecture),
        run=read_run(raw.get("run", {}), f"{path}.run", size),
        expect=read_state(raw.get("expect", {}), f"{path}.expect", architecture, expectation=True),
        preserve_registers=preserved,
        allowed_register_changes=allowed,
        preserve_memory=tuple(ranges),
    )


def load_yaml(path: Path):
    try:
        with path.open("rb") as file:
            content = file.read(MAX_YAML_BYTES + 1)
        if len(content) > MAX_YAML_BYTES:
            raise TestError(f"{path}: YAML exceeds {MAX_YAML_BYTES} bytes")
        return yaml.load(content.decode("utf-8-sig"), Loader=SuiteLoader)
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError, ValueError) as exc:
        raise TestError(f"{path}: {exc}") from exc


def read_path(value, base: Path, field: str) -> Path:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise TestError(f"{field}: expected a file or directory path")
    return (base / value).resolve()


def read_paths(value, base: Path, field: str) -> tuple[Path, ...]:
    return tuple(read_path(item, base, f"{field}[{i}]") for i, item in enumerate(sequence(value, field)))


def read_architecture(value, field="architecture"):
    if value not in ("cdm8", "cdm16"):
        raise TestError(f"{field}: expected cdm8 or cdm16")
    return value


def load_suite(path: Path) -> Suite:
    root = mapping(load_yaml(path), "suite", {"version", "architecture", "memory_model", "build", "defaults", "cases"})
    integer(root.get("version"), "version", 1, 1)
    architecture = read_architecture(root.get("architecture"))
    layout = root.get("memory_model", "unified")
    if layout not in ("unified", "harvard"):
        raise TestError("memory_model: expected unified or harvard")
    build = mapping(root.get("build", {}), "build", {"sources", "harness"})
    sources = read_paths(build.get("sources", []), path.parent, "build.sources")
    harness = read_path(build["harness"], path.parent, "build.harness") if "harness" in build else None
    defaults = mapping(root.get("defaults", {}), "defaults", CASE_KEYS)
    read_case("<defaults>", defaults, "defaults", architecture)
    raw_cases = mapping(root.get("cases"), "cases")
    if not raw_cases:
        raise TestError("cases: expected at least one named case")
    cases = []
    for name, raw in raw_cases.items():
        if not isinstance(name, str) or not name.strip() or not name.isprintable():
            raise TestError("cases: case names must be nonempty printable strings")
        location = f"cases.{name}"
        overrides = mapping(raw, location, CASE_KEYS)
        cases.append(read_case(name, merge_case(defaults, overrides), location, architecture))
    return Suite(architecture, layout, sources, tuple(cases), harness)
