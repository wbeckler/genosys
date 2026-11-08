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
2. **Vivado hand-off.** EDIF out of Yosys into Vivado for US+ implementation: #2053, #568.
4. **Measurement.** Reproducible LUT/FF/DSP/BRAM and post-route Fmax reports per design.

## Status (2026-11-08)

### Done

| Branch | What | Evidence |
|---|---|---|
| `wbeckler/dspfuzz` | RTL-vs-netlist co-simulation harness for DSP packing (`scaled/dspfuzz`) | finds 246 wrong/crashing designs out of 1700 on upstream 0.70+360 (xc7) |
| `wbeckler/xilinx-dsp-correctness` | `xilinx_dsp` packs only what preserves function: zero-reset check, sign-extension tail rewiring, exact post-adder (interval arithmetic), wrap-aware pre-adder, cascade guard, #6046 crash | 1700/1700 pass; `tests/arch/xilinx/dsp_genosys_correctness.ys` proves #6311/#6312/#6252/PR6138/pre-adder cases with `equiv_opt`; resource use unchanged on 1699/1700 |
| `wbeckler/xilinx-dsp48e2-sim` | DSP48E2 simulation model in `cells_sim.v` (UG579; aligned with the Apache-2.0 Xilinx unisim model incl. U/V partial products, W mux, SIMD, wide XOR, pattern detect + AUTORESET, IS_*_INVERTED) | 1480 random configs agree cycle-for-cycle with the DSP48E1 model on their common space (`scaled/dsp48e2/e1e2.py`); directed tests for E2-only features (`tests/arch/xilinx/dsp48e2.sh`); xcup dspfuzz 1000/1000 (was 931 unsimulatable) |
| `wbeckler/xilinx-dsp48e2-pack` | `xilinx_dsp` packs A/B/D/M/P registers, pre-adder, post-adder/accumulator, CREG and PCOUT/ACOUT/BCOUT cascades into DSP48E2 (xcu/xcup); before, nothing was packed | xcup 2000/2000 match RTL; at equal DSP count FF -64%, LUT -72%, CARRY8 -75%, no design worse; xc7 unchanged |
| `wbeckler/xilinx-dsp48e2-xcheck` | Differential co-simulation of the DSP48E2 model against the Apache-2.0 Xilinx unisim DSP48E2 over the full E2 configuration space (`scaled/dsp48e2/e2xil.py`; unisim sources not vendored, pass their path) | 2000/2000 random configurations x 500 cycles agree on every output bit the reference defines |

Upstream issues addressed: #6311, #6312, #6252, #6046 (and the bug fixed by open PR #6138).

### Next, in order

1. **Parser-shaped QoR.** `$shiftx`/`$bmux`/`$pmux` mapping (#3893, #878, PR #3880):
   field extraction at variable offsets is the core of a feed handler.
2. **Vivado hand-off.** `write_edif` → Vivado implementation for US+ (#2053, #568);
   measure post-route Fmax against Vivado's own synthesis.
3. **Pattern detector.** Overflow matching compares a narrow `$ge` with
   48-bit P; add overflow patterns to the fuzzer, then verify.
4. **DSP48E2 timing data** (specify blocks / abc9 box) so `-abc9` sees DSP
   delays on US+, as it does for DSP48E1.
5. Small known conservatism: post-adder folding is declined when the pre-adder
   is not folded and the operand range cannot be proven (1 in 1000 cases).
6. `equiv_opt` cannot prove pipelined DSP packings (DSP state is not the RTL
   state) and multipliers above ~6x6 time out; co-simulation is the check
   there. A sequential equivalence flow (e.g. via SBY) would close this.
