import java.io.DataOutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.BitSet;
import java.util.stream.IntStream;

import org.cdm.cocoemu.components.memory.BankedRam;
import org.cdm.cocoemu.components.processors.cdm16.Cdm16;
import org.cdm.cocoemu.components.processors.cdm16.Status;
import org.cdm.cocoemu.core.Component;
import org.cdm.cocoemu.core.image.Image;
import org.cdm.cocoemu.core.ports.InputsClass;
import org.cdm.cocoemu.core.ports.OutputsClass;
import org.cdm.cocoemu.systems.HarvardSystem;
import org.cdm.cocoemu.systems.VonNeumannSystem;

/** Drives the official Cocoemu processor without a GUI or debugger server. */
public final class Cdm16Bridge {
    private static final int MAX_CYCLES_PER_INSTRUCTION = 128;

    public static final class Cpu extends Cdm16 {
        // Cocoemu discovers these annotations on the concrete class at construction.
        @InputsClass
        public static class Inputs extends Cdm16.Inputs { }

        @OutputsClass
        public static class Outputs extends Cdm16.Outputs { }

        void setEntry(int address) {
            startupLatch.reset();
            pc.setValue(address);
        }

        void setInitialState(String[] args) {
            int spValue = Integer.parseInt(args[8]);
            if (spValue >= 0) {
                sp.setValue(spValue);
            }
            int flagsMask = Integer.parseInt(args[9]);
            int flagsValue = Integer.parseInt(args[10]);
            ps.setFlags((ps.getFlags() & ~flagsMask) | flagsValue);
            for (int i = 0; i < 8; i++) {
                registerFile.setRegisterValue(i, Integer.parseInt(args[11 + i]));
            }
        }
    }

    private static Image readImage(Path path) throws Exception {
        byte[] data = Files.readAllBytes(path);
        return new Image(IntStream.range(0, data.length).map(i -> Byte.toUnsignedInt(data[i])).toArray());
    }

    private static boolean instruction(Cpu cpu, Component system) {
        for (int cycles = 0; cycles < MAX_CYCLES_PER_INSTRUCTION; cycles++) {
            system.clockRising();
            system.clockFalling();
            system.update();
            if (cpu.outputs.fetch || cpu.outputs.status != Status.RUNNING) {
                return true;
            }
        }
        return false;
    }

    public static void main(String[] args) throws Exception {
        Path imagePath = Path.of(args[0]);
        Path ramPath = Path.of(args[1]);
        Path outputPath = Path.of(args[2]);
        boolean harvard = args[3].equals("harvard");
        int entry = Integer.parseInt(args[4]);
        int stopPc = Integer.parseInt(args[5]);
        int stopSteps = Integer.parseInt(args[6]);
        int maxSteps = Integer.parseInt(args[7]);
        boolean coverage = Boolean.parseBoolean(args[19]);
        BitSet visited = new BitSet();
        Cpu cpu = new Cpu();
        Component system;
        BankedRam ram;
        if (harvard) {
            HarvardSystem machine = new HarvardSystem(cpu, readImage(imagePath));
            machine.ram.getBuffer().put(0, Files.readAllBytes(ramPath));
            system = machine;
            ram = machine.ram;
        } else {
            VonNeumannSystem machine = new VonNeumannSystem(cpu, readImage(ramPath));
            system = machine;
            ram = machine.ram;
        }
        String reason = "";
        if (entry >= 0) {
            cpu.setEntry(entry);
        } else if (!instruction(cpu, system) || cpu.outputs.status != Status.RUNNING) {
            reason = "boot";
        }
        cpu.setInitialState(args);
        system.update();
        int steps = 0;
        int changed = 0;
        int[] previous = cpu.outputs.gpRegisters.clone();
        while (reason.isEmpty()) {
            int pc = cpu.outputs.pc;
            if (pc == stopPc) {
                reason = "pc";
            } else if (steps == stopSteps) {
                reason = "steps";
            } else if (steps == maxSteps) {
                reason = "limit";
            } else if (!instruction(cpu, system)) {
                reason = "cycle_limit";
            } else {
                steps++;
                if (coverage) {
                    visited.set(pc);
                }
                for (int i = 0; i < 8; i++) {
                    if (previous[i] != cpu.outputs.gpRegisters[i]) {
                        changed |= 1 << i;
                        previous[i] = cpu.outputs.gpRegisters[i];
                    }
                }
                switch (cpu.outputs.status) {
                    case Status.HALTED -> reason = steps == stopSteps ? "steps" : "halt";
                    case Status.WAITING -> reason = "wait";
                    case Status.FAULT -> reason = "fault";
                    default -> { }
                }
            }
        }
        try (var output = new DataOutputStream(Files.newOutputStream(outputPath))) {
            output.writeUTF(reason);
            output.writeInt(steps);
            output.writeInt(changed);
            for (int value : cpu.outputs.gpRegisters) {
                output.writeInt(value);
            }
            output.writeInt(cpu.outputs.pc);
            output.writeInt(cpu.outputs.sp);
            output.writeInt(cpu.outputs.ps);
            output.write(ram.getBuffer().array());
            output.write(visited.toByteArray());
        }
    }
}
