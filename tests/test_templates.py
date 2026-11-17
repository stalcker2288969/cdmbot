import tempfile
import unittest
from pathlib import Path

from cdmbot.developer import run


class TemplateTests(unittest.TestCase):
    def test_multiple_named_templates_negative_terms_and_static_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            for target in ('cdm8', 'cdm16'):
                wide = target == 'cdm16'
                header = 'asect 0\ndc start, 0\nasect 0x200\nstart:\nldi r7, 0x8000\nstsp r7\n' if wide else 'asect 0\nsetsp 240\n'
                source = ('tplate Record\nname: ds 10\nage: ds 1\nid: ds 3\n'
                          'tplate Packet\nkind: ds 2\npayload: ds 6\n' + header +
                          'addsp -Record._\naddsp -Packet._\nldi r2, Record._+Packet._\n'
                          'if\nldi r3, 20-Record.age\ntst r3\nis nz\nmove r0, r0\nfi\n'
                          'addsp Packet._\naddsp Record._\nhalt\n' +
                          ('asect 0x9000\n' if wide else 'asect 0xA0\n') +
                          'record: ds Record._\npacket: ds Packet._\nend\n')
                file = Path(directory) / (target + '.asm')
                file.write_text(source, encoding='utf-8')
                for model in ('vonNeumann', 'harvard'):
                    with self.subTest(target=target, model=model):
                        report = run(file, target=target, architecture=model)
                        self.assertTrue(report['passed'], report)
                        self.assertEqual(report['registers']['sp'], 0x8000 if wide else 240)
                        self.assertEqual(report['registers']['r2'], 22)
                        self.assertEqual(report['registers']['r3'], 10)
