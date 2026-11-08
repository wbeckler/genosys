// -widemux mapping of $shiftx: cases that upstream mapped incorrectly
// Y_WIDTH > 1: bit i can select up to A[A_WIDTH-1] (bit-blast was cut at A_WIDTH-Y_WIDTH+i)
module sx_wide_y(input [15:0] a, input [3:0] b, output [3:0] y);
	\$shiftx #(.A_SIGNED(0), .B_SIGNED(0), .A_WIDTH(16), .B_WIDTH(4), .Y_WIDTH(4)) s (.A(a), .B(b), .Y(y));
endmodule
// inferred byte-field extraction running off the top of the bus
module field_top(input [127:0] in, input [3:0] sel, output [31:0] out);
	assign out = in[sel*8 +: 32];
endmodule
// stride with a constant-zero LSB in B: >16-input muxes picked the wrong B bits
module stride_even(input [16*6-1:0] in, input [3:0] sel, output [7:0] out);
	assign out = in[sel*6 +: 8];
endmodule
// A wider than 2**B_WIDTH with Y_WIDTH = 1
module sx_narrow_b(input [39:0] a, input [2:0] b, output y);
	\$shiftx #(.A_SIGNED(0), .B_SIGNED(0), .A_WIDTH(40), .B_WIDTH(3), .Y_WIDTH(1)) s (.A(a), .B(b), .Y(y));
endmodule
// single constant-zero B bit
module sx_b_zero(input [3:0] a, output y);
	\$shiftx #(.A_SIGNED(0), .B_SIGNED(0), .A_WIDTH(4), .B_WIDTH(1), .Y_WIDTH(1)) s (.A(a), .B(1'b0), .Y(y));
endmodule
// word select that peepopt turns into a $bmux: maps onto MUXF7/MUXF8
module bmux_word(input [32*8-1:0] in, input [4:0] sel, output [7:0] out);
	assign out = in[sel*8 +: 8];
endmodule
