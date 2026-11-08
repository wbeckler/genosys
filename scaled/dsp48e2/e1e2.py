#!/usr/bin/env python3
"""Differential test: DSP48E2 model vs the existing DSP48E1 model, over the
configuration space where the two primitives must behave identically
(25-bit A/D operands, W mux off, OPMODEs valid on DSP48E1).

Known, intended differences that are excluded:
  * CARRYCASCOUT during multiplies: DSP48E2 models the multiplier's two
    partial products (as the hardware and the Xilinx unisim model do); the
    DSP48E1 model feeds the full product through X with Y = 0.
  * logic ALUMODEs together with the multiplier (invalid per UG579).

Usage: python3 scaled/dsp48e2/e1e2.py --n 400 --seed 7 --out /tmp/e1e2
"""
import random, subprocess, sys, os, argparse, json

ap = argparse.ArgumentParser()
ap.add_argument('--n', type=int, default=200)
ap.add_argument('--seed', type=int, default=1)
ap.add_argument('--cycles', type=int, default=300)
ap.add_argument('--batch', type=int, default=25)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
ap.add_argument('--cells', default=os.path.join(ROOT, 'techlibs', 'xilinx', 'cells_sim.v'))
ap.add_argument('--out', default='e1e2_out')
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)

def valid_opmodes(preg, mult):
    res = []
    for z in range(8):
        for y in range(4):
            for x in range(4):
                if (x == 1) != (y == 1): continue
                if x == 1 and not mult: continue
                if x == 2 and not preg: continue
                if z in (2, 4, 6) and not preg: continue
                if z == 4 and (y << 2 | x) != 0b1000: continue
                if z == 7: continue
                res.append(z << 4 | y << 2 | x)
    return res

def gen_cfg(r, i):
    c = {}
    c['AREG'] = r.choice([0, 1, 2]); c['BREG'] = r.choice([0, 1, 2])
    c['ACASCREG'] = c['AREG'] if c['AREG'] < 2 else r.choice([1, 2])
    c['BCASCREG'] = c['BREG'] if c['BREG'] < 2 else r.choice([1, 2])
    for k in ['CREG', 'DREG', 'ADREG', 'MREG', 'PREG', 'INMODEREG', 'OPMODEREG', 'ALUMODEREG', 'CARRYINREG', 'CARRYINSELREG']:
        c[k] = r.choice([0, 1])
    c['simd'] = r.choice(['ONE48'] * 4 + ['TWO24', 'FOUR12'])
    c['mult'] = c['simd'] == 'ONE48' and r.random() < 0.7
    c['dport'] = c['mult'] and r.random() < 0.5
    c['patdet'] = r.random() < 0.5
    c['PATTERN'] = r.getrandbits(48) if r.random() < 0.5 else 0
    c['MASK'] = r.choice([r.getrandbits(48), (1 << 48) - (1 << r.randrange(1, 48)), 0x3FFFFFFFFFFF])
    c['opmodes'] = valid_opmodes(c['PREG'], c['mult'])
    c['logic'] = not c['mult'] and r.random() < 0.4   # logic ALUMODEs are invalid with the multiplier
    c['cinsel'] = [s for s in range(8) if (c['PREG'] or s not in (4, 5, 7)) and not (c['mult'] and s in (2, 4))]
    return c

def vparams(c, e2):
    p = {k: c[k] for k in ['AREG', 'BREG', 'ACASCREG', 'BCASCREG', 'CREG', 'DREG', 'ADREG', 'MREG', 'PREG',
                           'INMODEREG', 'OPMODEREG', 'ALUMODEREG', 'CARRYINREG', 'CARRYINSELREG']}
    s = ', '.join(f'.{k}({v})' for k, v in p.items())
    s += f', .USE_SIMD("{c["simd"]}"), .USE_MULT("{"MULTIPLY" if c["mult"] else "NONE"}")'
    s += f', .USE_PATTERN_DETECT("{"PATDET" if c["patdet"] else "NO_PATDET"}")'
    s += f", .PATTERN(48'h{c['PATTERN']:012x}), .MASK(48'h{c['MASK']:012x})"
    if e2:
        s += f', .AMULTSEL("{"AD" if c["dport"] else "A"}")'
    else:
        s += f', .USE_DPORT("{"TRUE" if c["dport"] else "FALSE"}")'
    return s

def tb(c, i, seed):
    ops = c['opmodes']
    optab = '\n'.join(f"        {j}: op = 7'd{o};" for j, o in enumerate(ops))
    cs = c['cinsel']
    cstab = '\n'.join(f"        {j}: cs = 3'd{o};" for j, o in enumerate(cs))
    alus = list(range(16)) if c['logic'] else [0, 1, 2, 3]
    alutab = '\n'.join(f"        {j}: al = 4'd{o};" for j, o in enumerate(alus))
    a0 = 'inm[0] = 1\'b0;' if c['AREG'] == 0 else ''
    b0 = 'inm[4] = 1\'b0;' if c['BREG'] == 0 else ''
    return f'''
module tb{i}(input clk, input go, output reg fail);
    reg [29:0] A; reg [17:0] B; reg [47:0] C, PCIN; reg [24:0] D;
    reg [4:0] INMODE; reg [6:0] OPMODE; reg [3:0] ALUMODE; reg [2:0] CARRYINSEL;
    reg CARRYIN, CARRYCASCIN;
    reg CEA1, CEA2, CEAD, CEALUMODE, CEB1, CEB2, CEC, CECARRYIN, CECTRL, CED, CEINMODE, CEM, CEP;
    reg RSTA, RSTALLCARRYIN, RSTALUMODE, RSTB, RSTC, RSTCTRL, RSTD, RSTINMODE, RSTM, RSTP;
    wire [47:0] P1, P2, PC1, PC2; wire [29:0] AC1, AC2; wire [17:0] BC1, BC2;
    wire [3:0] CO1, CO2; wire CCO1, CCO2, PD1, PD2, PBD1, PBD2, OF1, OF2, UF1, UF2;
    DSP48E1 #({vparams(c, False)}) e1 (
        .A(A), .B(B), .C(C), .D(D), .PCIN(PCIN), .ACIN(30'd0), .BCIN(18'd0), .CARRYCASCIN(CARRYCASCIN), .MULTSIGNIN(1'b0),
        .INMODE(INMODE), .OPMODE(OPMODE), .ALUMODE(ALUMODE), .CARRYINSEL(CARRYINSEL), .CARRYIN(CARRYIN), .CLK(clk),
        .CEA1(CEA1), .CEA2(CEA2), .CEAD(CEAD), .CEALUMODE(CEALUMODE), .CEB1(CEB1), .CEB2(CEB2), .CEC(CEC), .CECARRYIN(CECARRYIN),
        .CECTRL(CECTRL), .CED(CED), .CEINMODE(CEINMODE), .CEM(CEM), .CEP(CEP),
        .RSTA(RSTA), .RSTALLCARRYIN(RSTALLCARRYIN), .RSTALUMODE(RSTALUMODE), .RSTB(RSTB), .RSTC(RSTC), .RSTCTRL(RSTCTRL),
        .RSTD(RSTD), .RSTINMODE(RSTINMODE), .RSTM(RSTM), .RSTP(RSTP),
        .P(P1), .PCOUT(PC1), .ACOUT(AC1), .BCOUT(BC1), .CARRYOUT(CO1), .CARRYCASCOUT(CCO1),
        .PATTERNDETECT(PD1), .PATTERNBDETECT(PBD1), .OVERFLOW(OF1), .UNDERFLOW(UF1));
    DSP48E2 #({vparams(c, True)}) e2 (
        .A(A), .B(B), .C(C), .D({{{{2{{D[24]}}}}, D}}), .PCIN(PCIN), .ACIN(30'd0), .BCIN(18'd0), .CARRYCASCIN(CARRYCASCIN), .MULTSIGNIN(1'b0),
        .INMODE(INMODE), .OPMODE({{2'b00, OPMODE}}), .ALUMODE(ALUMODE), .CARRYINSEL(CARRYINSEL), .CARRYIN(CARRYIN), .CLK(clk),
        .CEA1(CEA1), .CEA2(CEA2), .CEAD(CEAD), .CEALUMODE(CEALUMODE), .CEB1(CEB1), .CEB2(CEB2), .CEC(CEC), .CECARRYIN(CECARRYIN),
        .CECTRL(CECTRL), .CED(CED), .CEINMODE(CEINMODE), .CEM(CEM), .CEP(CEP),
        .RSTA(RSTA), .RSTALLCARRYIN(RSTALLCARRYIN), .RSTALUMODE(RSTALUMODE), .RSTB(RSTB), .RSTC(RSTC), .RSTCTRL(RSTCTRL),
        .RSTD(RSTD), .RSTINMODE(RSTINMODE), .RSTM(RSTM), .RSTP(RSTP),
        .P(P2), .PCOUT(PC2), .ACOUT(AC2), .BCOUT(BC2), .CARRYOUT(CO2), .CARRYCASCOUT(CCO2),
        .PATTERNDETECT(PD2), .PATTERNBDETECT(PBD2), .OVERFLOW(OF2), .UNDERFLOW(UF2));

    integer seed = {seed}, cyc = 0, k;
    reg [6:0] op; reg [2:0] cs; reg [3:0] al; reg [4:0] inm;
    function bit_rand(input integer pct); bit_rand = ($unsigned($random(seed)) % 100) < pct; endfunction
    function [47:0] corner48(input integer dummy);
        case ($unsigned($random(seed)) % 6)
            0: corner48 = 48'd0; 1: corner48 = {{48{{1'b1}}}}; 2: corner48 = {{1'b0, {{47{{1'b1}}}}}}; 3: corner48 = {{1'b1, 47'd0}};
            default: corner48 = {{$random(seed), $random(seed)}};
        endcase
    endfunction
    task randomize; begin
        // A: bits 26:24 equal (25-bit signed multiplier operand); A[24:23] equal keeps the pre-adder exact
        A = corner48(0); A[26:24] = {{3{{A[23]}}}};
        D = corner48(0); D[24] = D[23];
        B = corner48(0); C = corner48(0); PCIN = corner48(0);
        k = $unsigned($random(seed)) % {len(ops)};
        case (k)
{optab}
        endcase
        k = $unsigned($random(seed)) % {len(cs)};
        case (k)
{cstab}
        endcase
        k = $unsigned($random(seed)) % {len(alus)};
        case (k)
{alutab}
        endcase
        inm = $random(seed); {a0} {b0}
        OPMODE = op; CARRYINSEL = cs; ALUMODE = al; INMODE = inm;
        CARRYIN = $random(seed); CARRYCASCIN = $random(seed);
        {{CEA1, CEA2, CEAD, CEALUMODE, CEB1, CEB2, CEC, CECARRYIN, CECTRL, CED, CEINMODE, CEM, CEP}} = {{13{{1'b1}}}};
        if (bit_rand(25)) {{CEA1, CEA2, CEAD, CEALUMODE, CEB1, CEB2, CEC, CECARRYIN, CECTRL, CED, CEINMODE, CEM, CEP}} = $random(seed);
        {{RSTA, RSTALLCARRYIN, RSTALUMODE, RSTB, RSTC, RSTCTRL, RSTD, RSTINMODE, RSTM, RSTP}} = 10'd0;
        if (bit_rand(5)) {{RSTA, RSTALLCARRYIN, RSTALUMODE, RSTB, RSTC, RSTCTRL, RSTD, RSTINMODE, RSTM, RSTP}} = $random(seed) & $random(seed);
    end endtask

    // compare bits where E1 is defined
    function ok(input [47:0] e1v, input [47:0] e2v);
        integer b; begin ok = 1;
            for (b = 0; b < 48; b = b + 1) if (e1v[b] !== 1'bx && e1v[b] !== e2v[b]) ok = 0;
        end
    endfunction

    initial begin fail = 0; end
    always @(negedge clk) if (go) begin
        cyc = cyc + 1;
        if (cyc > 2) begin
            if (!ok(P1, P2) || !ok(PC1, PC2) || !ok(AC1, AC2) || !ok(BC1, BC2) || !ok(CO1, CO2) || (!ok(CCO1, CCO2) && !{int(c['mult'])}) ||
                !ok(PD1, PD2) || !ok(PBD1, PBD2) || ({c['PREG']} && {int(c['patdet'])} && (!ok(OF1, OF2) || !ok(UF1, UF2)))) begin
                if (!fail) $display("tb{i} FAIL cyc=%0d P1=%h P2=%h CO1=%b CO2=%b CCO=%b/%b PD=%b%b/%b%b AC=%h/%h BC=%h/%h OF=%b/%b UF=%b/%b",
                    cyc, P1, P2, CO1, CO2, CCO1, CCO2, PD1, PBD1, PD2, PBD2, AC1, AC2, BC1, BC2, OF1, OF2, UF1, UF2);
                fail = 1;
            end
        end
        randomize;
    end
endmodule
'''

r = random.Random(args.seed)
cfgs = [gen_cfg(r, i) for i in range(args.n)]
total_fail = 0
for b0 in range(0, args.n, args.batch):
    idx = list(range(b0, min(args.n, b0 + args.batch)))
    src = ''.join(tb(cfgs[i], i, r.randrange(1, 1 << 30)) for i in idx)
    fails = ' | '.join(f't{i}.fail' for i in idx)
    inst = '\n'.join(f'    wire f{i}; tb{i} t{i}(.clk(clk), .go(go), .fail(f{i}));' for i in idx)
    rep = '\n'.join(f'        if (f{i}) $display("CFG {i} FAIL");' for i in idx)
    src += f'''
module top; reg clk = 0, go = 0;
{inst}
    initial begin #1 go = 1; repeat ({args.cycles}) begin #5 clk = 1; #5 clk = 0; end
{rep}
        $display("DONE"); $finish; end
endmodule
'''
    fn = f'{args.out}/b{b0}.v'
    open(fn, 'w').write(src)
    exe = f'{args.out}/b{b0}'
    cp = subprocess.run(['iverilog', '-g2005', '-s', 'top', '-o', exe, args.cells, fn], capture_output=True, text=True)
    if cp.returncode:
        print(cp.stderr[:3000]); sys.exit(1)
    out = subprocess.run(['vvp', '-n', exe], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if 'FAIL' in line: print(line)
    nf = out.count('CFG ')
    total_fail += nf
    if 'DONE' not in out: print(out[-2000:]); sys.exit(1)
print(f'{args.n - total_fail}/{args.n} configs agree')
json.dump(cfgs, open(f'{args.out}/cfgs.json', 'w'))
