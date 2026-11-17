"""Apply CoCAS compatibility fixes before executing an assembler entry point."""
import runpy
import sys
from .cocas_templates import install
install()
module = sys.argv.pop(1)
runpy.run_module(module, run_name='__main__')
