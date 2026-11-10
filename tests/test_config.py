import tempfile
import unittest
from pathlib import Path

from cdmbot.config import load_suite
from cdmbot.model import TestError


class ConfigTests(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.yaml"
            path.write_text("version: 1\narchitecture: cdm8\n" + text, encoding="utf-8")
            return load_suite(path)

    def test_defaults_are_merged_per_case(self):
        suite = self.load(
            "defaults:\n  setup:\n    registers: {r1: 4, r2: 9}\n"
            "cases:\n  first:\n    setup:\n      registers: {r1: 6}\n  second: {}\n"
        )
        first, second = suite.cases
        self.assertEqual(first.setup.registers, {"r1": 6, "r2": 9})
        self.assertEqual(second.setup.registers, {"r1": 4, "r2": 9})

    def test_rejects_bad_rules(self):
        invalid = {
            "unknown register": "cases: {t: {expect: {registers: {r7: 1}}}}\n",
            "duplicate key": "cases: {t: {}, t: {}}\n",
            "aliases are disabled": "cases: {t: &test {}, u: *test}\n",
            "unknown key": "cases: {t: {exepct: {}}}\n",
            "within memory": "cases: {t: {setup: {memory: {0xff: [1, 2]}}}}\n",
            "overlaps": "cases: {t: {setup: {memory: {0x30: [1, 2], 0x31: 3}}}}\n",
            "0..255": "cases: {t: {setup: {registers: {r0: true}}}}\n",
            "0..2": "cases: {t: {run: {max_steps: 2, until: {steps: 3}}}}\n",
        }
        for message, text in invalid.items():
            with self.subTest(message=message), self.assertRaisesRegex(TestError, message):
                self.load(text)

    def test_cdm16_entry_requires_alignment_and_fp_is_r7(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.yaml"
            prefix = "version: 1\narchitecture: cdm16\n"
            path.write_text(prefix + "cases: {t: {run: {entry: 1}}}\n", encoding="utf-8")
            with self.assertRaisesRegex(TestError, "must be even"):
                load_suite(path)
            path.write_text(prefix + "cases: {t: {setup: {registers: {fp: 0xffff}}}}\n", encoding="utf-8")
            self.assertEqual(load_suite(path).cases[0].setup.registers, {"r7": 65535})
