#!/usr/bin/env python3
"""Differential test: genosys DSP48E2 model (techlibs/xilinx/cells_sim.v) vs
the Xilinx unisim DSP48E2 model (Apache-2.0, github.com/Xilinx/XilinxUnisimLibrary)
over the full DSP48E2 configuration space: 27-bit pre-adder, AMULTSEL/BMULTSEL/
PREADDINSEL, W mux (RND/C/P), SIMD, wide XOR, pattern detect with SEL_MASK/
SEL_PATTERN, AUTORESET, A/B cascade inputs, IS_*_INVERTED.

Only bits the unisim model drives to 0/1 are compared (it deliberately outputs X
for e.g. CARRYOUT during multiplies).

Usage:
  python3 scaled/dsp48e2/e2xil.py --unisim <path to unisims/DSP48E2.v> --glbl <glbl.v> --n 400 --out DIR
"""
import random, subprocess, sys, os, argparse, json, re

ap = argparse.ArgumentParser()
ap.add_argument('--n', type=int, default=200)
ap.add_argument('--seed', type=int, default=1)
ap.add_argument('--cycles', type=int, default=300)
ap.add_argument('--batch', type=int, default=25)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
ap.add_argument('--cells', default=os.path.join(ROOT, 'techlibs', 'xilinx', 'cells_sim.v'))
ap.add_argument('--unisim', required=True)
ap.add_argument('--glbl', required=True)
ap.add_argument('--out', default='e2xil_out')
ap.add_argument('--only', type=int, default=-1, help='run a single config index')
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)

# renamed copy of the reference model
xil = os.path.join(args.out, 'DSP48E2_XIL.v')
src = open(args.unisim).read()
src = re.sub(r'^module DSP48E2\b', 'module DSP48E2_XIL', src, count=1, flags=re.M)
open(xil, 'w').write(src)

REGS1 = ['CREG', 'DREG', 'ADREG', 'MREG', 'PREG', 'INMODEREG', 'OPMODEREG', 'ALUMODEREG', 'CARRYINREG', 'CARRYINSELREG']
INV1 = ['IS_CARRYIN_INVERTED', 'IS_CLK_INVERTED', 'IS_RSTALLCARRYIN_INVERTED', 'IS_RSTALUMODE_INVERTED',
        'IS_RSTA_INVERTED', 'IS_RSTB_INVERTED', 'IS_RSTCTRL_INVERTED', 'IS_RSTC_INVERTED', 'IS_RSTD_INVERTED',
        'IS_RSTINMODE_INVERTED', 'IS_RSTM_INVERTED', 'IS_RSTP_INVERTED']

def gen_cfg(r):
    c = {}
    c['AREG'] = r.choice([0, 1, 2]); c['BREG'] = r.choice([0, 1, 2])
    c['ACASCREG'] = c['AREG'] if c['AREG'] < 2 else r.choice([1, 2])
    c['BCASCREG'] = c['BREG'] if c['BREG'] < 2 else r.choice([1, 2])
    for k in REGS1:
        c[k] = r.choice([0, 1])
    c['USE_SIMD'] = r.choice(['ONE48'] * 4 + ['TWO24', 'FOUR12'])
    c['USE_MULT'] = 'NONE' if c['USE_SIMD'] != 'ONE48' else r.choice(['MULTIPLY', 'MULTIPLY', 'DYNAMIC', 'NONE'])
    if c['USE_MULT'] == 'NONE':
        c['MREG'] = 0
    c['AMULTSEL'] = r.choice(['A', 'AD'])
    c['BMULTSEL'] = r.choice(['B', 'B', 'AD'])
    c['PREADDINSEL'] = r.choice(['A', 'A', 'B'])
    c['A_INPUT'] = r.choice(['DIRECT', 'DIRECT', 'CASCADE'])
    c['B_INPUT'] = r.choice(['DIRECT', 'DIRECT', 'CASCADE'])
    c['USE_PATTERN_DETECT'] = r.choice(['NO_PATDET', 'PATDET'])
    c['SEL_MASK'] = r.choice(['MASK', 'MASK', 'C', 'ROUNDING_MODE1', 'ROUNDING_MODE2'])
    c['SEL_PATTERN'] = r.choice(['PATTERN', 'PATTERN', 'C'])
    c['AUTORESET_PATDET'] = r.choice(['NO_RESET'] * 2 + ['RESET_MATCH', 'RESET_NOT_MATCH']) if c['PREG'] else 'NO_RESET'
    c['AUTORESET_PRIORITY'] = r.choice(['RESET', 'CEP'])
    c['USE_WIDEXOR'] = r.choice(['FALSE', 'FALSE', 'TRUE'])
    c['XORSIMD'] = r.choice(['XOR24_48_96', 'XOR12'])
    c['PATTERN'] = r.getrandbits(48) if r.random() < 0.5 else r.choice([0, 1, 9])
    c['MASK'] = r.choice([r.getrandbits(48), (1 << 48) - (1 << r.randrange(1, 48)), 0x3FFFFFFFFFFF, 0])
    c['RND'] = r.choice([0, 1 << r.randrange(0, 47), r.getrandbits(48)])
    inv = r.random() < 0.3
    for k in INV1:
        c[k] = r.getrandbits(1) if inv else 0
    c['IS_ALUMODE_INVERTED'] = r.getrandbits(4) if inv else 0
    c['IS_INMODE_INVERTED'] = r.getrandbits(5) if inv else 0
    c['IS_OPMODE_INVERTED'] = r.getrandbits(9) if inv else 0
    c['logic'] = c['USE_MULT'] == 'NONE' and r.random() < 0.4
    return c

def valid_opmodes(c):
    mult = c['USE_MULT'] != 'NONE'
    res = []
    for w in range(4):
        for z in range(8):
            for y in range(4):
                for x in range(4):
                    if (x == 1) != (y == 1): continue      # M partial products go to X and Y together
                    if x == 1 and not mult: continue
                    if z == 7: continue                    # reserved
                    if c['logic'] and y == 1: continue
                    res.append(w << 7 | z << 4 | y << 2 | x)
    return res

def vparams(c):
    ints = ['AREG', 'BREG', 'ACASCREG', 'BCASCREG'] + REGS1
    s = [f'.{k}({c[k]})' for k in ints]
    for k in ['USE_SIMD', 'USE_MULT', 'AMULTSEL', 'BMULTSEL', 'PREADDINSEL', 'A_INPUT', 'B_INPUT', 'USE_PATTERN_DETECT',
              'SEL_MASK', 'SEL_PATTERN', 'AUTORESET_PATDET', 'AUTORESET_PRIORITY', 'USE_WIDEXOR', 'XORSIMD']:
        s.append(f'.{k}("{c[k]}")')
    for k in ['PATTERN', 'MASK', 'RND']:
        s.append(f".{k}(48'h{c[k]:012x})")
    for k in INV1:
        s.append(f".{k}(1'b{c[k]})")
    s.append(f".IS_ALUMODE_INVERTED(4'd{c['IS_ALUMODE_INVERTED']})")
    s.append(f".IS_INMODE_INVERTED(5'd{c['IS_INMODE_INVERTED']})")
    s.append(f".IS_OPMODE_INVERTED(9'd{c['IS_OPMODE_INVERTED']})")
    return ', '.join(s)

PORTS = '''.A(A), .B(B), .C(C), .D(D), .PCIN(PCIN), .ACIN(ACIN), .BCIN(BCIN), .CARRYCASCIN(CARRYCASCIN), .MULTSIGNIN(MULTSIGNIN),
        .INMODE(INMODE), .OPMODE(OPMODE), .ALUMODE(ALUMODE), .CARRYINSEL(CARRYINSEL), .CARRYIN(CARRYIN), .CLK(clk),
        .CEA1(CEA1), .CEA2(CEA2), .CEAD(CEAD), .CEALUMODE(CEALUMODE), .CEB1(CEB1), .CEB2(CEB2), .CEC(CEC), .CECARRYIN(CECARRYIN),
        .CECTRL(CECTRL), .CED(CED), .CEINMODE(CEINMODE), .CEM(CEM), .CEP(CEP),
        .RSTA(RSTA), .RSTALLCARRYIN(RSTALLCARRYIN), .RSTALUMODE(RSTALUMODE), .RSTB(RSTB), .RSTC(RSTC), .RSTCTRL(RSTCTRL),
        .RSTD(RSTD), .RSTINMODE(RSTINMODE), .RSTM(RSTM), .RSTP(RSTP)'''

OUTS = ['P:48', 'PCOUT:48', 'ACOUT:30', 'BCOUT:18', 'CARRYOUT:4', 'CARRYCASCOUT:1', 'MULTSIGNOUT:1',
        'PATTERNDETECT:1', 'PATTERNBDETECT:1', 'OVERFLOW:1', 'UNDERFLOW:1', 'XOROUT:8']

def tb(c, i, seed):
    ops = valid_opmodes(c)
    optab = '\n'.join(f"        {j}: op = 9'd{o};" for j, o in enumerate(ops))
    alus = list(range(16)) if c['logic'] else [0, 1, 2, 3]
    alutab = '\n'.join(f"        {j}: al = 4'd{o};" for j, o in enumerate(alus))
    decl = '\n'.join(f'    wire [{int(w)-1}:0] {n}_g, {n}_x;' for n, w in (o.split(':') for o in OUTS))
    og = ', '.join(f'.{n}({n}_g)' for n, _ in (o.split(':') for o in OUTS))
    ox = ', '.join(f'.{n}({n}_x)' for n, _ in (o.split(':') for o in OUTS))
    chk = ' || '.join(f'!ok({n}_x, {n}_g)' for n, _ in (o.split(':') for o in OUTS))
    fmt = ' '.join(f'{n}=%h/%h' for n, _ in (o.split(':') for o in OUTS))
    fargs = ', '.join(f'{n}_x, {n}_g' for n, _ in (o.split(':') for o in OUTS))
    return f'''
module tb{i}(input clk_t, input go, output reg fail);
    // pre-invert so the active edge is always clk_t's posedge (and t=0 x->0 is never an active edge)
    wire clk = clk_t ^ 1'b{c['IS_CLK_INVERTED']};
    reg [29:0] A, ACIN; reg [17:0] B, BCIN; reg [47:0] C, PCIN; reg [26:0] D;
    reg [4:0] INMODE; reg [8:0] OPMODE; reg [3:0] ALUMODE; reg [2:0] CARRYINSEL;
    reg CARRYIN, CARRYCASCIN, MULTSIGNIN;
    reg CEA1, CEA2, CEAD, CEALUMODE, CEB1, CEB2, CEC, CECARRYIN, CECTRL, CED, CEINMODE, CEM, CEP;
    reg RSTA, RSTALLCARRYIN, RSTALUMODE, RSTB, RSTC, RSTCTRL, RSTD, RSTINMODE, RSTM, RSTP;
{decl}
    DSP48E2 #({vparams(c)}) g ({PORTS}, {og});
    DSP48E2_XIL #({vparams(c)}) x ({PORTS}, {ox});

    integer seed = {seed}, cyc = 0, k;
    reg [8:0] op; reg [3:0] al;
    function bit_rand(input integer pct); bit_rand = ($unsigned($random(seed)) % 100) < pct; endfunction
    function [47:0] corner48(input integer dummy);
        case ($unsigned($random(seed)) % 6)
            0: corner48 = 48'd0; 1: corner48 = {{48{{1'b1}}}}; 2: corner48 = {{1'b0, {{47{{1'b1}}}}}}; 3: corner48 = {{1'b1, 47'd0}};
            default: corner48 = {{$random(seed), $random(seed)}};
        endcase
    endfunction
    task randomize; begin
        A = corner48(0); ACIN = corner48(0); B = corner48(0); BCIN = corner48(0);
        C = corner48(0); PCIN = corner48(0); D = corner48(0);
        k = $unsigned($random(seed)) % {len(ops)};
        case (k)
{optab}
        endcase
        k = $unsigned($random(seed)) % {len(alus)};
        case (k)
{alutab}
        endcase
        OPMODE = op ^ {c['IS_OPMODE_INVERTED']}; ALUMODE = al ^ {c['IS_ALUMODE_INVERTED']};
        INMODE = $random(seed); CARRYINSEL = $random(seed);
        CARRYIN = $random(seed); CARRYCASCIN = $random(seed); MULTSIGNIN = $random(seed);
        {{CEA1, CEA2, CEAD, CEALUMODE, CEB1, CEB2, CEC, CECARRYIN, CECTRL, CED, CEINMODE, CEM, CEP}} = {{13{{1'b1}}}};
        if (bit_rand(25)) {{CEA1, CEA2, CEAD, CEALUMODE, CEB1, CEB2, CEC, CECARRYIN, CECTRL, CED, CEINMODE, CEM, CEP}} = $random(seed);
        {{RSTA, RSTALLCARRYIN, RSTALUMODE, RSTB, RSTC, RSTCTRL, RSTD, RSTINMODE, RSTM, RSTP}} = 10'd0;
        if (bit_rand(5)) {{RSTA, RSTALLCARRYIN, RSTALUMODE, RSTB, RSTC, RSTCTRL, RSTD, RSTINMODE, RSTM, RSTP}} = $random(seed) & $random(seed);
        {{RSTA, RSTALLCARRYIN, RSTALUMODE, RSTB, RSTC, RSTCTRL, RSTD, RSTINMODE, RSTM, RSTP}} =
            {{RSTA, RSTALLCARRYIN, RSTALUMODE, RSTB, RSTC, RSTCTRL, RSTD, RSTINMODE, RSTM, RSTP}} ^
            {{1'b{c['IS_RSTA_INVERTED']}, 1'b{c['IS_RSTALLCARRYIN_INVERTED']}, 1'b{c['IS_RSTALUMODE_INVERTED']}, 1'b{c['IS_RSTB_INVERTED']},
              1'b{c['IS_RSTC_INVERTED']}, 1'b{c['IS_RSTCTRL_INVERTED']}, 1'b{c['IS_RSTD_INVERTED']}, 1'b{c['IS_RSTINMODE_INVERTED']},
              1'b{c['IS_RSTM_INVERTED']}, 1'b{c['IS_RSTP_INVERTED']}}};
    end endtask

    // compare bits where the reference is defined
    function ok(input [47:0] rv, input [47:0] got);
        integer b; begin ok = 1;
            for (b = 0; b < 48; b = b + 1) if (rv[b] !== 1'bx && rv[b] !== 1'bz && rv[b] !== got[b]) ok = 0;
        end
    endfunction

    initial begin fail = 0; randomize; end
    always @(negedge clk_t) if (go) begin
        cyc = cyc + 1;
        if (cyc > 3) begin
            if ({chk}) begin
                if (!fail) $display("tb{i} FAIL cyc=%0d {fmt}", cyc, {fargs});
                fail = 1;
            end
        end
        randomize;
    end
endmodule
'''

r = random.Random(args.seed)
cfgs = [gen_cfg(r) for i in range(args.n)]
seeds = [r.randrange(1, 1 << 30) for i in range(args.n)]
todo = [args.only] if args.only >= 0 else list(range(args.n))
total_fail = 0
for b0 in range(0, len(todo), args.batch):
    idx = todo[b0:b0 + args.batch]
    src = '`timescale 1ns/1ps\n' + ''.join(tb(cfgs[i], i, seeds[i]) for i in idx)
    inst = '\n'.join(f'    wire f{i}; tb{i} t{i}(.clk_t(clk), .go(go), .fail(f{i}));' for i in idx)
    rep = '\n'.join(f'        if (f{i}) $display("CFG {i} FAIL");' for i in idx)
    # wait out the glbl GSR pulse (100 ns) before comparing
    src += f'''
module top; reg clk = 0, go = 0;
{inst}
    initial begin #105 go = 1; repeat ({args.cycles}) begin #5 clk = 1; #5 clk = 0; end
{rep}
        $display("DONE"); $finish; end
endmodule
'''
    fn = f'{args.out}/b{b0}.v'
    open(fn, 'w').write(src)
    exe = f'{args.out}/b{b0}'
    cp = subprocess.run(['iverilog', '-g2012', '-s', 'top', '-s', 'glbl', '-o', exe, args.cells, xil, args.glbl, fn],
                        capture_output=True, text=True)
    if cp.returncode:
        print(cp.stderr[:3000]); sys.exit(1)
    out = subprocess.run(['vvp', '-n', exe], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if 'FAIL' in line: print(line)
    nf = out.count('CFG ')
    total_fail += nf
    if 'DONE' not in out: print(out[-2000:]); sys.exit(1)
print(f'{len(todo) - total_fail}/{len(todo)} configs agree')
json.dump(cfgs, open(f'{args.out}/cfgs.json', 'w'))
