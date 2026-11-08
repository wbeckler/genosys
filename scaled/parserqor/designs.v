// Parser-shaped variable-offset extraction kernels (combinational).
// Each module is a single kernel; parameters set the sizes.

// Byte-aligned field extraction from a bus: out = in[sel*8 +: W]
module byte_extract #(parameter IN = 512, parameter SELW = 6, parameter W = 32) (
	input [IN-1:0] in, input [SELW-1:0] sel, output [W-1:0] out);
	assign out = in[sel*8 +: W];
endmodule

// Bit-granular extraction: out = in[off +: W]
module bit_extract #(parameter IN = 128, parameter OFFW = 7, parameter W = 16) (
	input [IN-1:0] in, input [OFFW-1:0] off, output [W-1:0] out);
	assign out = in[off +: W];
endmodule

// Strided record select (PR #3880 testcase): out = in[sel*34 +: W], 13 records
module stride_select #(parameter N = 13, parameter STRIDE = 34, parameter W = 14) (
	input [N*STRIDE-1:0] in, input [3:0] sel, output [W-1:0] out);
	assign out = in[sel*STRIDE +: W];
endmodule

// Right shift by a byte count (stream realignment): out = in >> (sel*8)
module byte_shift #(parameter IN = 256, parameter SELW = 5) (
	input [IN-1:0] in, input [SELW-1:0] sel, output [IN-1:0] out);
	assign out = in >> {sel, 3'b000};
endmodule

// Message-type table: type selects a field offset and length, field is
// extracted and zero-extended. 16 message types.
module msgtype_field #(parameter IN = 512) (
	input [IN-1:0] in, input [3:0] mtype, output reg [31:0] out);
	reg [5:0] off; reg [2:0] len;
	always @* begin
		case (mtype)
			4'd0: begin off = 6'd2;  len = 3'd4; end
			4'd1: begin off = 6'd6;  len = 3'd2; end
			4'd2: begin off = 6'd10; len = 3'd4; end
			4'd3: begin off = 6'd14; len = 3'd1; end
			4'd4: begin off = 6'd8;  len = 3'd4; end
			4'd5: begin off = 6'd20; len = 3'd2; end
			4'd6: begin off = 6'd24; len = 3'd4; end
			4'd7: begin off = 6'd30; len = 3'd4; end
			4'd8: begin off = 6'd34; len = 3'd2; end
			4'd9: begin off = 6'd36; len = 3'd4; end
			4'd10: begin off = 6'd40; len = 3'd1; end
			4'd11: begin off = 6'd44; len = 3'd4; end
			4'd12: begin off = 6'd48; len = 3'd2; end
			4'd13: begin off = 6'd52; len = 3'd4; end
			4'd14: begin off = 6'd56; len = 3'd4; end
			default: begin off = 6'd0; len = 3'd4; end
		endcase
		out = in[off*8 +: 32];
		case (len)
			3'd1: out = out & 32'h000000ff;
			3'd2: out = out & 32'h0000ffff;
			default: ;
		endcase
	end
endmodule

// Same table, written as a case per message type selecting the field directly
// (what a code generator emitting one arm per message would produce).
module msgtype_case #(parameter IN = 512) (
	input [IN-1:0] in, input [3:0] mtype, output reg [31:0] out);
	always @* begin
		case (mtype)
			4'd0: out = in[16 +: 32];
			4'd1: out = {16'b0, in[48 +: 16]};
			4'd2: out = in[80 +: 32];
			4'd3: out = {24'b0, in[112 +: 8]};
			4'd4: out = in[64 +: 32];
			4'd5: out = {16'b0, in[160 +: 16]};
			4'd6: out = in[192 +: 32];
			4'd7: out = in[240 +: 32];
			4'd8: out = {16'b0, in[272 +: 16]};
			4'd9: out = in[288 +: 32];
			4'd10: out = {24'b0, in[320 +: 8]};
			4'd11: out = in[352 +: 32];
			4'd12: out = {16'b0, in[384 +: 16]};
			4'd13: out = in[416 +: 32];
			4'd14: out = in[448 +: 32];
			default: out = in[0 +: 32];
		endcase
	end
endmodule

// Big-endian field: extract then byte-reverse (network byte order)
module be_extract #(parameter IN = 512, parameter SELW = 6) (
	input [IN-1:0] in, input [SELW-1:0] sel, output [31:0] out);
	wire [31:0] f = in[sel*8 +: 32];
	assign out = {f[7:0], f[15:8], f[23:16], f[31:24]};
endmodule

// Explicit array indexing of 8-byte words: 64-bit word from a 16-entry array
module word_index #(parameter N = 16) (
	input [N*64-1:0] in, input [3:0] idx, output [63:0] out);
	wire [63:0] arr [0:N-1];
	genvar i;
	for (i = 0; i < N; i = i + 1) assign arr[i] = in[i*64 +: 64];
	assign out = arr[idx];
endmodule
