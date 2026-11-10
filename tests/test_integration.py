import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from cdmbot.config import load_suite
from cdmbot.runner import run_suite

ROOT = Path(__file__).resolve().parents[1]


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def file(self, name, contents):
        path = self.root / name
        path.write_text(contents, encoding="utf-8")
        return path

    def cli(self, program, suite, *options):
        return subprocess.run(
            [sys.executable, "-m", "cdmbot", str(program), str(suite), *map(str, options)],
            capture_output=True, encoding="utf-8", timeout=45,
        )

    def test_shipped_examples(self):
        for architecture in ("cdm8", "cdm16"):
            with self.subTest(architecture=architecture):
                root = ROOT / "examples" / architecture
                results = run_suite(root / "add.asm", load_suite(root / "add.yaml"), jobs=2)
                last_case = "before_halt" if architecture == "cdm8" else "direct_entry"
                self.assertEqual([result.name for result in results], ["simple", "wrap", last_case])
                self.assertEqual([result.failures for result in results], [(), (), ()])

    def test_cdm8_links_library_and_runs_stack_in_harvard_mode(self):
        program = self.file("wrapper.asm", """asect 0
    jsr update
    halt
update: ext
end.
""")
        self.file("routine.asm", """rsect routine
update>
    push r1
    ld r2, r1
    inc r1
    st r2, r1
    pop r1
    rts
end.
""")
        suite = self.file("test.yaml", """version: 1
architecture: cdm8
memory_model: harvard
build:
  sources: [routine.asm]
defaults:
  setup:
    registers: {r1: 0x77, r2: 0, sp: 0xff}
    memory: {0: 40}
  preserve_registers: [r1, sp]
cases:
  update:
    allowed_register_changes: [r1]
    expect: {memory: {0: 41}}
  same_initial_ram:
    run: {until: {steps: 0}}
    expect: {memory: {0: 40}}
""")
        results = run_suite(program, load_suite(suite))
        self.assertEqual([result.failures for result in results], [(), ()])
        self.assertEqual(results[0].changed_registers, ("r1",))

    def test_cdm8_cmp_clears_carry_as_in_mark5_circuit(self):
        program = self.file("compare.asm", """asect 0
    cmp r0, r1
    halt
end.
""")
        suite = self.file("test.yaml", """version: 1
architecture: cdm8
cases:
  compare:
    setup:
      registers: {r0: 2, r1: 1}
      flags: {c: 1}
    expect:
      flags: {c: 0, v: 0, z: 0, n: 0}
    allowed_register_changes: []
""")
        result, = run_suite(program, load_suite(suite))
        self.assertEqual(result.failures, ())

    def test_cdm8_pop_reads_initial_stack_and_program_reads_label_inputs(self):
        root = ROOT / "examples" / "cdm8"
        result, = run_suite(root / "inputs.asm", load_suite(root / "inputs.yaml"))
        self.assertEqual(result.failures, ())

    def test_cdm16_initial_stack_and_labels_in_linked_section(self):
        program = self.file("program.asm", """asect 0
    dc start, 0
asect 0x80
start:
    pop r0
    pop r3
    ldi r1, payload
    ldw r1, r2
    ldi r1, payload+2
    ldw r1, r4
    halt
payload: ext
end.
""")
        self.file("data.asm", """rsect data
payload>
    ds 2
text_label: ds 2
end.
""")
        suite = self.file("inputs.yaml", """version: 1
architecture: cdm16
memory_model: harvard
build:
  sources: [data.asm]
defaults:
  setup:
    registers: {sp: 0xff00}
    stack: [0x1234, 0x5678]
    input: {payload: 0xabcd, text_label: "AB"}
cases:
  text:
    expect:
      registers: {r0: 0x1234, r2: 0xabcd, r3: 0x5678, r4: 0x4241, sp: 0xff04}
  bytes:
    setup:
      input: {text_label: [0, 1]}
    expect:
      registers: {r0: 0x1234, r2: 0xabcd, r3: 0x5678, r4: 0x0100, sp: 0xff04}
""")
        results = run_suite(program, load_suite(suite), jobs=2)
        self.assertEqual([result.failures for result in results], [(), ()])

    def test_rejects_unresolved_labels_and_conflicting_initial_writes(self):
        program = self.file("program.asm", """asect 0
    halt
asect 0xfe
data_label: ds 2
end.
""")
        for message, setup in (
            ("was not found", "input: {missing: 10}"),
            ("overlaps", "input: {data_label: 10}, stack: [1, 2]"),
            ("does not fit in RAM", "input: {data_label: 'abc'}"),
            ("does not fit in RAM", "registers: {sp: 0xff}, stack: [1, 2]"),
        ):
            with self.subTest(setup=setup):
                suite = self.file("inputs.yaml", "version: 1\narchitecture: cdm8\ncases:\n"
                                  f"  bad:\n    setup: {{{setup}}}\n")
                process = self.cli(program, suite)
                self.assertEqual(process.returncode, 2, process.stderr)
                self.assertIn(message, process.stderr)

    def test_rejects_duplicate_local_input_labels_in_linked_files(self):
        program = self.file("program.asm", """asect 0
    halt
asect 0x80
item: ds 1
end.
""")
        self.file("extra.asm", """asect 0x90
item: ds 1
end.
""")
        suite = self.file("inputs.yaml", """version: 1
architecture: cdm8
build: {sources: [extra.asm]}
cases:
  ambiguous:
    setup: {input: {item: 10}}
""")
        process = self.cli(program, suite)
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertIn("ambiguous label 'item'", process.stderr)

    def test_cdm16_harvard_uses_byte_addresses_and_little_endian_ram(self):
        program = self.file("memory.asm", """asect 0
    dc start, 0
asect 0x80
start:
    ldw r1, r0
    stw r2, r0
    halt
end.
""")
        suite = self.file("test.yaml", """version: 1
architecture: cdm16
memory_model: harvard
cases:
  copy_word:
    setup:
      registers: {r1: 0x80, r2: 0x100}
      memory: {0x80: [0x34, 0x12]}
    expect:
      registers: {r0: 0x1234}
      memory: {0x100: [0x34, 0x12]}
    preserve_memory: [{address: 0x80, length: 2}]
""")
        result, = run_suite(program, load_suite(suite))
        self.assertEqual(result.failures, ())
        self.assertEqual(result.steps, 3)

    def test_limit_and_register_clobber_appear_in_reports(self):
        program = self.file("loop.asm", """asect 0
loop:
    inc r1
    dec r1
    br loop
end.
""")
        suite = self.file("test.yaml", """version: 1
architecture: cdm8
cases:
  loop:
    run: {max_steps: 3}
    allowed_register_changes: []
    preserve_registers: [r1]
""")
        junit = self.root / "report.xml"
        process = self.cli(program, suite, "--json", "--junit", junit)
        self.assertEqual(process.returncode, 1, process.stderr)
        case, = json.loads(process.stdout)["cases"]
        self.assertEqual(case["reason"], "limit")
        self.assertEqual(case["steps"], 3)
        self.assertEqual(case["registers"]["r1"], 0)
        self.assertEqual(len(case["failures"]), 2)
        self.assertIn("allowlist: r1", case["failures"][1])
        self.assertEqual(ET.parse(junit).getroot().get("failures"), "1")

    def test_list_filter_and_bad_assembly_exit_status(self):
        suite = ROOT / "examples" / "cdm8" / "add.yaml"
        program = self.file("bad.asm", "asect 0\n    bad_instruction\nend.\n")
        listed = self.cli(program, suite, "--list", "--case", "wr*")
        self.assertEqual((listed.returncode, listed.stdout.strip()), (0, "wrap"))
        unmatched = self.cli(program, suite, "--case", "missing")
        self.assertEqual(unmatched.returncode, 2)
        self.assertIn("No case matches", unmatched.stderr)
        failed = self.cli(program, suite)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("Cocas build failed", failed.stderr)
