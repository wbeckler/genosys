set -e
# DSP48E2 simulation model: directed self-checking tests
iverilog -g2005 -s top -o test_dsp48e2 dsp48e2_tb.v ../../../techlibs/xilinx/cells_sim.v
vvp -N ./test_dsp48e2
# UltraScale+ netlist of the macc design against its RTL
${YOSYS} -qp "synth_xilinx -family xcup -top macc2; rename -top macc2_uut" -o macc_uut_xcup.v macc.v
iverilog -o test_macc_xcup macc_tb.v macc_uut_xcup.v macc.v ../../../techlibs/xilinx/cells_sim.v
vvp -N ./test_macc_xcup
# Packed pipelined MAC (dsp48e2_pack.v) against its RTL
${YOSYS} -qp "read_verilog dsp48e2_pack.v; synth_xilinx -family xcup -noiopad -top top; rename top gate" -o dsp48e2_pack_uut.v
sed 's/module top/module gold/' dsp48e2_pack.v > dsp48e2_pack_gold.v
iverilog -o test_dsp48e2_pack dsp48e2_pack_tb.v dsp48e2_pack_gold.v dsp48e2_pack_uut.v ../../../techlibs/xilinx/cells_sim.v
vvp -N ./test_dsp48e2_pack
