"""Instruction-level backend for CdM-8 Mark 5."""

from .model import GPRS, Case, Execution, Layout, pack_flags, unpack_flags

MASK = 0xFF


class CPU:
    def __init__(self, image: bytes, initial_ram: bytes, case: Case, layout: Layout):
        self.ram = bytearray(initial_ram)
        self.code = self.ram if layout == "unified" else image.ljust(256, b"\0")
        self.pc = case.run.entry or 0
        self.sp = case.setup.registers.get("sp", 0)
        self.registers = [case.setup.registers.get(name, 0) for name in GPRS["cdm8"]]
        self.ps = pack_flags(case.setup.flags)[1]
        self.changed = set()

    def fetch(self):
        value = self.code[self.pc]
        self.pc = (self.pc + 1) & MASK
        return value

    def write(self, index, value):
        if self.registers[index] != value:
            self.changed.add(GPRS["cdm8"][index])
        self.registers[index] = value

    def result(self, value, carry=False, overflow=False):
        value &= MASK
        self.ps = (int(carry) << 3) | (int(overflow) << 2) | (int(value == 0) << 1) | (value >> 7)
        return value

    def alu_binary(self, group, a, b):
        if group in (1, 2):
            total = a + b + (int(bool(self.ps & 8)) if group == 2 else 0)
            return self.result(total, total > MASK, bool(~(a ^ b) & (a ^ total) & 128))
        if group in (3, 7):
            total = a - b
            # The Mark 5 circuit clears C for CMP; SUB exposes the carry-out.
            return self.result(total, group == 3 and a >= b, bool((a ^ b) & (a ^ total) & 128))
        return self.result({0: a, 4: a & b, 5: a | b, 6: a ^ b}[group])

    def alu_unary(self, operation, value):
        if operation == 0:
            return self.result(~value)
        if operation == 1:
            return self.result(-value, value == 0, value == 128)
        if operation == 2:
            return self.result(value - 1, value != 0, value == 128)
        if operation == 3:
            return self.result(value + 1, value == 255, value == 127)
        if operation == 4:
            return self.result((value >> 1) | ((self.ps & 8) << 4), value & 1)
        if operation == 5:
            return self.result(value << 1, value >> 7, bool((value ^ (value << 1)) & 128))
        if operation == 6:
            return self.result((value >> 1) | (value & 128), value & 1)
        return self.result((value << 1) | (value >> 7), value >> 7)

    def push(self, value):
        self.sp = (self.sp - 1) & MASK
        self.ram[self.sp] = value

    def pop(self):
        value = self.ram[self.sp]
        self.sp = (self.sp + 1) & MASK
        return value

    def branch(self, condition):
        target = self.fetch()
        c, v, z, n = (bool(self.ps & bit) for bit in (8, 4, 2, 1))
        conditions = (
            z, not z, c, not c, n, not n, v, not v,
            c and not z, not c or z, n == v, n != v,
            not z and n == v, z or n != v, True, False,
        )
        if conditions[condition]:
            self.pc = target

    def step(self):
        address = self.pc
        op = self.fetch()
        group, src, dst = op >> 4, (op >> 2) & 3, op & 3
        a, b = self.registers[src], self.registers[dst]
        if group < 8:
            value = self.alu_binary(group, a, b)
            if group != 7:
                self.write(dst, value)
        elif group in (8, 9):
            self.write(dst, self.alu_unary((op - 0x80) >> 2, b))
        elif group == 0xA:
            self.ram[a] = b
        elif group == 0xB:
            self.write(dst, self.ram[a])
        elif 0xC0 <= op <= 0xC3:
            self.push(b)
        elif 0xC4 <= op <= 0xC7:
            self.write(dst, self.pop())
        elif 0xC8 <= op <= 0xCB:
            self.write(dst, (self.sp + self.fetch()) & MASK)
        elif op == 0xCC:
            self.sp = (self.sp + self.fetch()) & MASK
        elif op == 0xCD:
            self.sp = self.fetch()
        elif op == 0xCE:
            for value in reversed(self.registers):
                self.push(value)
        elif op == 0xCF:
            for register in range(4):
                self.write(register, self.pop())
        elif 0xD0 <= op <= 0xD3:
            self.write(dst, self.fetch())
        elif op == 0xD4:
            return "halt", ""
        elif op == 0xD5:
            return "wait", "CPU is waiting for an interrupt"
        elif op == 0xD6:
            target = self.fetch()
            self.push(self.pc)
            self.pc = target
        elif op == 0xD7:
            self.pc = self.pop()
        elif op == 0xDA:
            target = self.ram[self.sp]
            self.ram[self.sp] = self.pc
            self.pc = target
        elif group == 0xE:
            self.branch(op & 15)
        elif group == 0xF:
            self.write(dst, self.code[a])
        else:
            return "unsupported", f"CdM-8 opcode 0x{op:02x} at PC 0x{address:02x}"
        return None


class Cdm8Backend:
    def __init__(self, *, coverage: bool = False):
        self.coverage = coverage

    def execute(self, image: bytes, initial_ram: bytes, case: Case, layout: Layout) -> Execution:
        cpu = CPU(image, initial_ram, case, layout)
        steps = 0
        visited = set()
        while True:
            if cpu.pc == case.run.stop_pc:
                reason, detail = "pc", ""
                break
            if steps == case.run.stop_steps:
                reason, detail = "steps", ""
                break
            if steps == case.run.max_steps:
                reason, detail = "limit", f"Reached max_steps={case.run.max_steps}"
                break
            if self.coverage:
                visited.add(cpu.pc)
            stop = cpu.step()
            steps += 1
            if stop:
                reason, detail = stop
                if reason == "halt" and steps == case.run.stop_steps:
                    reason = "steps"
                break
        registers = dict(zip(GPRS["cdm8"], cpu.registers))
        registers.update(pc=cpu.pc, sp=cpu.sp)
        return Execution(
            reason, steps, registers, unpack_flags(cpu.ps), bytes(cpu.ram), frozenset(cpu.changed), detail,
            frozenset(visited),
        )
