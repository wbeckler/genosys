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
| `wbeckler/xilinx-widemux-fix` | `synth_xilinx -widemux` produced wrong logic for `in[sel*8 +: W]` near the top of the bus and for strided selects (`$__XILINX_SHIFTX` bit-blast range, >16-input group select bits, A wider than 2**B) | upstream fails 26/60 random `$shiftx` shapes; all 180 shape/option combos + 40 large ones + all parser kernels prove with SAT; `tests/arch/xilinx/widemux_shiftx.ys` fails upstream |
| `wbeckler/xilinx-parser-qor` | peepopt: overlapping strided select (`in[sel*34 +: 40]`, #3873 W2>W) becomes a `$bmux` on `sel` (the multiply used to land in a DSP48E2); `-widemux` maps `$bmux` onto MUXF7/F8; `scaled/parserqor` benchmark | stride_select W=40: 1636 LUT + 1 DSP, depth 8 -> 209 LUT, depth 3 |
| `wbeckler/xilinx-widemux-default` | `-widemux 4` is the default on xcu/xcup (`-widemux 0` disables; xc7 unchanged) | 13 parser kernels: 8924 -> 3066 LUT (-66%); picorv32 -3..-8% LUT; dspfuzz xcup 1000/1000, resources identical |
| `wbeckler/seqeq` | Sequential equivalence (eqy/SBY k-induction) of RTL vs netlist per dspfuzz case, ports-only matching so DSP pipeline state is proved as a whole (`scaled/seqeq`) | genosys xcup: 518/1000 proved, 482 time out (180 s), 0 counterexamples; catches the upstream xc7 miscompiles (cases 3, 8, 21) |

Upstream issues addressed: #6311, #6312, #6252, #6046 (and the bug fixed by open PR #6138); #3873/PR #3880 (overlapping strided selects). #3893 does not apply to synth_xilinx: it already runs `opt_expr -mux_undef` before abc9.

### Next, in order

1. **Vivado hand-off.** `write_edif` → Vivado implementation for US+ (#2053, #568);
   measure post-route Fmax against Vivado's own synthesis. Also needed to
   judge the wide-mux depth trade-off (msgtype_field: 345 -> 166 LUT but
   4 -> 7 cells deep, counting MUXF7/F8 levels).
2. **Remaining parser QoR gaps** (`scaled/parserqor`): `byte_shift`
   (`in >> {sel,3'b0}`, 917 LUT; a radix-4 shifter should be ~640) and
   `bit_extract` 64->32 (228 LUT; the wide-mux threshold
   `(A_WIDTH+Y_WIDTH-1)/Y_WIDTH` skips it). Index multiplies that peepopt
   does not fold still go to DSP48E2s.
3. **Pattern detector.** Overflow matching compares a narrow `$ge` with
   48-bit P; add overflow patterns to the fuzzer, then verify.
4. **DSP48E2 timing data** (specify blocks / abc9 box) so `-abc9` sees DSP
   delays on US+, as it does for DSP48E1.
5. Small known conservatism: post-adder folding is declined when the pre-adder
   is not folded and the operand range cannot be proven (1 in 1000 cases).
6. **Sequential equivalence timeouts.** `scaled/seqeq` proves about half of
   the xcup dspfuzz cases; the rest (mostly wide multipliers, including
   single-DSP ones and multipliers split across several DSPs) time out on
   every engine tried (bitwuzla, yices, z3, abc pdr). Co-simulation stays the
   check there. Possible next steps: prove the DSP model once against a
   word-level multiply, then abstract it in the netlist.
