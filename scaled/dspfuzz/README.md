# dspfuzz

Random multiply/accumulate designs → `synth_xilinx` → co-simulation of RTL
against the mapped netlist (Icarus Verilog + `share/xilinx/cells_sim.v`).

```sh
python3 gen.py /tmp/cases -n 1000 -s 2            # generate
python3 gen.py /tmp/wide -n 400 -s 3 --wide 1.0   # multi-DSP (mul2dsp + cascade)
python3 run.py /tmp/cases --yosys path/to/yosys -j 14 [--synth-opts "-family xcup"]
python3 qor.py /tmp/cases_baseline /tmp/cases     # resource diff between two runs
```

Statuses per case: `PASS`, `FAIL` (netlist output differs from RTL on a cycle
where the RTL output is fully defined), `VACUOUS` (RTL output undefined for
most cycles, so the run proves little), `SYNTH_ERROR`, `SIM_BUILD_ERROR`.

Generated features: operand widths/signedness, product truncation/extension,
pre-adder, post-adder, accumulator, input/M/output registers with clock enable,
zero and non-zero synchronous reset, and init values.

Results so far (xc7): upstream 0.70+360 fails 246 of 1700 cases; genosys
`wbeckler/xilinx-dsp-correctness` passes 1700/1700.
