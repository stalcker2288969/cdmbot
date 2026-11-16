"""CoCAS CdM-16 source-map correction used by development builds."""
from cocas.assembler.targets import IVaryingLengthSegment
from cocas.main import main

original = IVaryingLengthSegment.update_surroundings


def update(diff, pos, section, labels):
    locations = section.code_locations
    section.code_locations = {}
    original(diff, pos, section, labels)
    offset = pos - section.address
    section.code_locations = {address + (diff if address > offset else 0): location for address, location in locations.items()}


IVaryingLengthSegment.update_surroundings = staticmethod(update)
raise SystemExit(main())
