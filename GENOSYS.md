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

## Status (2026-11-07)

### Done

| Branch | What | Evidence |
|---|---|---|
| `wbeckler/dspfuzz` | RTL-vs-netlist co-simulation harness for DSP packing (`scaled/dspfuzz`) | finds 246 wrong/crashing designs out of 1700 on upstream 0.70+360 (xc7) |
| `wbeckler/xilinx-dsp-correctness` | `xilinx_dsp` packs only what preserves function: zero-reset check, sign-extension tail rewiring, exact post-adder (interval arithmetic), wrap-aware pre-adder, cascade guard, #6046 crash | 1700/1700 pass; `tests/arch/xilinx/dsp_genosys_correctness.ys` proves #6311/#6312/#6252/PR6138/pre-adder cases with `equiv_opt`; resource use unchanged on 1699/1700 |

Upstream issues addressed: #6311, #6312, #6252, #6046 (and the bug fixed by open PR #6138).

### Next, in order

1. **DSP48E2 simulation model.** `cells_sim.v` has none (only a blackbox in
   `cells_xtra.v`), so no UltraScale/US+ netlist with a DSP can be simulated or
   formally checked with Yosys's own libraries. Prerequisite for everything below.
2. **DSP48E2 packing.** `xilinx_dsp -family xcu/xcup` packs nothing (no A/B/M/P
   registers, pre-adder, post-adder, accumulator, cascade). Every pipeline
   register stays in fabric: a large Fmax/LUT loss on the target family.
3. **Parser-shaped QoR.** `$shiftx`/`$bmux`/`$pmux` mapping (#3893, #878, PR #3880):
   field extraction at variable offsets is the core of a feed handler.
4. **Vivado hand-off.** `write_edif` → Vivado implementation for US+ (#2053, #568);
   measure post-route Fmax against Vivado's own synthesis.
5. **Pattern detector exactness.** Overflow matching compares a narrow `$ge` with
   48-bit P; not yet covered by the fuzzer.
6. Small known conservatism: post-adder folding is declined when the pre-adder
   is not folded and the operand range cannot be proven (1 in 1000 cases).
