"""Collect resolved labels in the isolated Cocas 0.2.3 build process."""

import json
import sys
from pathlib import Path

from cocas.assembler.code_block import Section
from cocas.linker import linker
from cocas.main import main as cocas_main


def main() -> int | None:
    symbol_file = Path(sys.argv.pop(1))
    requested = set(json.loads(symbol_file.read_text(encoding="utf-8")))
    resolved: dict[str, list[int]] = {name: [] for name in requested}
    synthetic: dict[str, str] = {}
    section_record = Section.to_object_section_record
    gather_entries = linker.gather_ents

    def record(section, labels, templates):
        result = section_record(section, labels, templates)
        # Cocas discards local labels when forming the object record. Private
        # entries let its linker relocate them without changing code or layout.
        for name in sorted((section.labels.keys() & requested) - result.entries.keys()):
            key = f"\0cdmbot:{len(synthetic)}"
            synthetic[key] = name
            result.entries[key] = section.labels[name]
        return result

    def gather(sections, addresses):
        entries = gather_entries(sections, addresses)
        for key, address in entries.items():
            if key in synthetic:
                resolved[synthetic[key]].append(address)
            elif key in requested:
                resolved[key].append(address)
        return entries

    # These hooks live only in the short-lived build subprocess.
    Section.to_object_section_record = record
    linker.gather_ents = gather
    result = cocas_main()
    if not result:
        symbol_file.write_text(json.dumps(resolved), encoding="utf-8")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
