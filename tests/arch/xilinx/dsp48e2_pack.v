// Pipelined pre-add / multiply / post-add, fits one DSP48E2 (see dsp48e2_pack.ys)
module top(input clk, input ce, input signed [5:0] a, input signed [5:0] d, input signed [4:0] b, input signed [13:0] c, output reg signed [13:0] p);
  reg signed [5:0] ar = 0, dr = 0; reg signed [4:0] br = 0; reg signed [12:0] m = 0;
  initial p = 0;
  always @(posedge clk) if (ce) begin
    ar <= a; dr <= d; br <= b;
    m <= (dr + ar) * br;
    p <= m + c;
  end
endmodule
