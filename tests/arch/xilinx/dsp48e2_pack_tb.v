// Random co-simulation of dsp48e2_pack.v (gold) against its xcup netlist (gate)
module tb;
	reg clk = 0, ce;
	reg [5:0] a, d;
	reg [4:0] b;
	reg [13:0] c;
	wire [13:0] pg, px;

	gold G(.clk(clk), .ce(ce), .a(a), .d(d), .b(b), .c(c), .p(pg));
	gate X(.clk(clk), .ce(ce), .a(a), .d(d), .b(b), .c(c), .p(px));

	always #5 clk = !clk;

	integer i, e = 0, seed = 5;
	initial begin
		ce = 0; a = 0; d = 0; b = 0; c = 0;
		for (i = 0; i < 20000; i = i + 1) begin
			@(negedge clk);
			ce = ($random(seed) & 3) != 0;
			a = $random(seed); d = $random(seed); b = $random(seed); c = $random(seed);
			#1;
			if (pg !== px) begin
				e = e + 1;
				if (e < 5) $display("MISMATCH cycle %0d: gold %h gate %h", i, pg, px);
			end
		end
		if (e) $fatal(1, "FAIL: %0d mismatches", e);
		$display("PASS");
		$finish;
	end
endmodule
