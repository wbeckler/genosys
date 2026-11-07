# genosys

A fork of [Yosys](https://github.com/YosysHQ/yosys) aimed at making it dependable
for generated, latency-critical FPGA designs on AMD parts (Scaled / OMI code
generators). "gen" for code generation, "osys" for Yosys; pronounced *genesis*.

## Target choice: UltraScale+ first, Versal later (maybe)

| Family | Yosys synthesis | Place & route | Verdict |
|---|---|---|---|
| UltraScale+ (`-family xcup`) | Supported: LUT6, CARRY8, DSP48E2, RAMB36E2, URAM288, SRL | Vivado (import Yosys EDIF); no open P&R | **Start here.** Alveo/trading cards are mostly US+. |
| 7-series (`-family xc7`) | Most mature Xilinx target | Vivado, or open via openXC7/nextpnr-xilinx | Use for open-flow experiments and cheap boards. |
| Versal | Not supported at all (no family, no DSP58, no new CLB/imux-reg model) | Vivado only; device architecture largely undocumented | **Not now.** Would mean writing a new techlib against opaque docs before learning anything. |

## Branching

- `main` tracks `upstream/main` (YosysHQ). Never commit fixes there.
- One topic branch per change: `wbeckler/<topic>`.
- `genosys` integrates the topic branches we rely on.

## Workstreams

1. **Correctness of `synth_xilinx` (blocking).** Wrong arithmetic is worse than slow arithmetic.
   Harness: `scaled/dspfuzz` (random MAC designs, synth_xilinx, RTL vs netlist co-simulation).
   Known upstream reports: #6252, #6311, #6312, #6046; open PR #6138.
2. **QoR of parser-shaped logic.** Variable shifts / wide muxes (`$shiftx`, `$bmux`, `$pmux`): #3893, #878, PR #3880.
3. **Vivado hand-off.** EDIF out of Yosys into Vivado for US+ implementation: #2053, #568.
4. **Measurement.** Reproducible LUT/FF/DSP/BRAM and post-route Fmax reports per design.

## Status log

See `scaled/STATUS.md`.
