// Directed self-checking tests for the DSP48E2 simulation model (cells_sim.v):
// expected values follow UG579 and are computed with plain integer arithmetic.
`define COMB .AREG(0),.BREG(0),.ACASCREG(0),.BCASCREG(0),.CREG(0),.DREG(0),.ADREG(0),.MREG(0),.INMODEREG(0),.OPMODEREG(0),.ALUMODEREG(0),.CARRYINREG(0),.CARRYINSELREG(0)
`define TIE .ACIN(30'd0),.BCIN(18'd0),.PCIN(48'd0),.CARRYCASCIN(1'b0),.MULTSIGNIN(1'b0),.CARRYIN(1'b0),.CARRYINSEL(3'd0),.CLK(clk),\
 .CEA1(1'b1),.CEA2(1'b1),.CEAD(1'b1),.CEALUMODE(1'b1),.CEB1(1'b1),.CEB2(1'b1),.CEC(1'b1),.CECARRYIN(1'b1),.CECTRL(1'b1),.CED(1'b1),.CEINMODE(1'b1),.CEM(1'b1),.CEP(1'b1),\
 .RSTA(1'b0),.RSTALLCARRYIN(1'b0),.RSTALUMODE(1'b0),.RSTB(1'b0),.RSTC(1'b0),.RSTCTRL(1'b0),.RSTD(1'b0),.RSTINMODE(1'b0),.RSTM(1'b0)

module top;
    reg clk = 0;
    reg [29:0] A; reg [17:0] B; reg [47:0] C; reg [26:0] D;
    integer seed = 42, i, errors = 0;
    reg signed [47:0] exp;
    reg signed [26:0] a27, d27, ad27; reg signed [17:0] b18, bd18;

    // 1. plain 27x18 signed multiply
    wire [47:0] P1;
    DSP48E2 #(`COMB,.PREG(0)) m1 (.A(A),.B(B),.C(C),.D(D),.INMODE(5'b00000),.ALUMODE(4'b0000),.OPMODE(9'b000000101),.P(P1),.RSTP(1'b0),`TIE);
    // 2. pre-adder D - A into the multiplier
    wire [47:0] P2;
    DSP48E2 #(`COMB,.PREG(0),.AMULTSEL("AD")) m2 (.A(A),.B(B),.C(C),.D(D),.INMODE(5'b01100),.ALUMODE(4'b0000),.OPMODE(9'b000000101),.P(P2),.RSTP(1'b0),`TIE);
    // 3. pre-adder on B: A * (D + B)[17:0]
    wire [47:0] P3;
    DSP48E2 #(`COMB,.PREG(0),.PREADDINSEL("B"),.BMULTSEL("AD")) m3 (.A(A),.B(B),.C(C),.D(D),.INMODE(5'b00100),.ALUMODE(4'b0000),.OPMODE(9'b000000101),.P(P3),.RSTP(1'b0),`TIE);
    // 4. W mux: M + RND, and M + C (Z) + C (W)
    wire [47:0] P4, P5;
    DSP48E2 #(`COMB,.PREG(0),.RND(48'h000000020000)) m4 (.A(A),.B(B),.C(C),.D(D),.INMODE(5'b00000),.ALUMODE(4'b0000),.OPMODE(9'b100000101),.P(P4),.RSTP(1'b0),`TIE);
    DSP48E2 #(`COMB,.PREG(0)) m5 (.A(A),.B(B),.C(C),.D(D),.INMODE(5'b00000),.ALUMODE(4'b0000),.OPMODE(9'b110110101),.P(P5),.RSTP(1'b0),`TIE);
    // 5. wide XOR of A:B (X=A:B, Y=0, Z=0)
    wire [7:0] X12, X96;
    DSP48E2 #(`COMB,.PREG(0),.USE_MULT("NONE"),.USE_WIDEXOR("TRUE"),.XORSIMD("XOR12")) m6 (.A(A),.B(B),.C(C),.D(D),.INMODE(5'b0),.ALUMODE(4'b0100),.OPMODE(9'b000000011),.XOROUT(X12),.RSTP(1'b0),`TIE);
    DSP48E2 #(`COMB,.PREG(0),.USE_MULT("NONE"),.USE_WIDEXOR("TRUE"),.XORSIMD("XOR24_48_96")) m7 (.A(A),.B(B),.C(C),.D(D),.INMODE(5'b0),.ALUMODE(4'b0100),.OPMODE(9'b000000011),.XOROUT(X96),.RSTP(1'b0),`TIE);
    // 6. inverted pins: same function as m2 with INMODE/OPMODE/ALUMODE inverted at the port
    wire [47:0] P8;
    DSP48E2 #(`COMB,.PREG(0),.AMULTSEL("AD"),.IS_INMODE_INVERTED(5'b10101),.IS_OPMODE_INVERTED(9'b101010101),.IS_ALUMODE_INVERTED(4'b1001))
        m8 (.A(A),.B(B),.C(C),.D(D),.INMODE(5'b01100 ^ 5'b10101),.ALUMODE(4'b0000 ^ 4'b1001),.OPMODE(9'b000000101 ^ 9'b101010101),.P(P8),.RSTP(1'b0),`TIE);
    // 7. auto-reset counter: P <= P + 1, reset when P == 9 (RESET_MATCH)
    wire [47:0] P9; reg rstp = 1;
    DSP48E2 #(`COMB,.PREG(1),.USE_MULT("NONE"),.USE_PATTERN_DETECT("PATDET"),.PATTERN(48'd9),.MASK(48'd0),.AUTORESET_PATDET("RESET_MATCH"))
        m9 (.A(30'd0),.B(18'd1),.C(C),.D(D),.INMODE(5'b0),.ALUMODE(4'b0000),.OPMODE(9'b000100011),.P(P9),.RSTP(rstp),`TIE);
    // 8. W = P accumulate with the multiplier: P <= P + A*B (OPMODE W=01)
    wire [47:0] P10; reg [47:0] acc;
    DSP48E2 #(`COMB,.PREG(1)) m10 (.A(A),.B(B),.C(C),.D(D),.INMODE(5'b0),.ALUMODE(4'b0000),.OPMODE(9'b010000101),.P(P10),.RSTP(rstp),`TIE);

    task check(input [47:0] got, input [47:0] want, input [8*24-1:0] what);
        if (got !== want) begin
            errors = errors + 1;
            if (errors < 20) $display("FAIL %0s: got %h want %h (A=%h B=%h C=%h D=%h)", what, got, want, A, B, C, D);
        end
    endtask

    integer cnt; reg [7:0] xs;
    initial begin
        A = 0; B = 0; C = 0; D = 0; acc = 0;
        #1 clk = 1; #1 clk = 0; rstp = 0;
        cnt = 0;
        for (i = 0; i < 2000; i = i + 1) begin
            A = {$random(seed)}; B = $random(seed); C = {$random(seed), $random(seed)}; D = $random(seed);
            if (i % 7 == 0) begin A[26:0] = 27'h4000000; B = 18'h20000; end   // most negative operands
            if (i % 11 == 0) begin D = 27'h3ffffff; A[26:0] = 27'h4000000; end // pre-adder wrap
            #1;
            a27 = A[26:0]; b18 = B; d27 = D;
            exp = a27 * b18;                         check(P1, exp, "mul27x18");
            ad27 = d27 - a27; exp = ad27 * b18;      check(P2, exp, "preadd D-A");
            check(P8, exp, "inverted pins");
            bd18 = d27 + {{9{b18[17]}}, b18}; exp = a27 * bd18; check(P3, exp, "preadd D+B");
            exp = a27 * b18; exp = exp + 48'h20000;            check(P4, exp, "W=RND");
            exp = a27 * b18; exp = exp + C + C;                check(P5, exp, "W=C,Z=C");
            for (cnt = 0; cnt < 8; cnt = cnt + 1) xs[cnt] = ^({A, B} >> (6 * cnt) & 48'h3f);
            check(X12, xs, "xor12");
            check(X96, {xs[7], xs[6]^xs[7], ^xs[7:4], xs[4]^xs[5], ^xs, xs[2]^xs[3], ^xs[3:0], xs[0]^xs[1]}, "xor24_48_96");
            // clocked tests
            check(P9, i % 10, "autoreset counter");
            check(P10, acc, "W=P accumulate");
            exp = a27 * b18; acc = acc + exp;
            #1 clk = 1; #1 clk = 0;
        end
        if (errors == 0) $display("ALL DIRECTED TESTS PASS");
        else $fatal(1, "%0d errors", errors);
        $finish;
    end
endmodule
