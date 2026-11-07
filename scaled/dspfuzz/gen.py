#!/usr/bin/env python3
"""Generate random multiply/MAC designs that stress xilinx_dsp packing.

Each case is a small Verilog module exercising one combination of:
operand widths and signedness, truncation of the product, pre-adder,
post-adder/accumulator, input/output registers with clock enables,
synchronous resets (zero and non-zero) and init values.
"""
import argparse
import json
import os
import random


def sdecl(signed):
    return "signed " if signed else ""


def gen_case(rng, idx):
    sa, sb = rng.choice([True, False]), rng.choice([True, False])
    # mixed signedness makes Verilog treat both as unsigned; keep it anyway
    wa = rng.randint(2, 27)
    wb = rng.randint(2, 20)
    wm = rng.choice([wa + wb, wa + wb, rng.randint(max(2, wa + wb - 12), wa + wb + 6)])
    pre = rng.random() < 0.2
    wd = rng.randint(2, wa) if pre else 0
    post = rng.choice(["none", "none", "addc", "acc"])
    wc = rng.randint(2, 48)
    wp = rng.choice([wm, rng.randint(max(2, wm - 8), min(48, wm + 10))])
    in_reg = rng.choice([0, 0, 1, 2])
    m_reg = rng.random() < 0.3
    out_reg = rng.choice(["none", "plain", "ce", "srst0", "srstN", "ce_srst0", "ce_srstN"]) if post != "acc" else rng.choice(["plain", "ce", "srst0", "ce_srst0", "srstN"])
    init_in = rng.random() < 0.15
    init_out = rng.random() < 0.15
    signed_m = sa and sb

    L = []
    ports = ["input clk", "input ce", "input rst",
             f"input {sdecl(sa)}[{wa-1}:0] a", f"input {sdecl(sb)}[{wb-1}:0] b"]
    if pre:
        ports.append(f"input {sdecl(sa)}[{wd-1}:0] d")
    if post == "addc":
        ports.append(f"input {sdecl(signed_m)}[{wc-1}:0] c")
    ports.append(f"output {sdecl(signed_m)}[{wp-1}:0] p")
    L.append(f"module top({', '.join(ports)});")

    def reg(name, w, s, src, init):
        iv = f" = {w}'d{rng.randint(1, (1 << min(w, 20)) - 1)}" if init else ""
        L.append(f"  reg {sdecl(s)}[{w-1}:0] {name}{iv};")
        L.append(f"  always @(posedge clk) {name} <= {src};")

    ca, cb, cd = "a", "b", "d"
    for i in range(in_reg):
        reg(f"ra{i}", wa, sa, ca, init_in and i == 0)
        reg(f"rb{i}", wb, sb, cb, init_in and i == 0)
        ca, cb = f"ra{i}", f"rb{i}"
        if pre:
            reg(f"rd{i}", wd, sa, cd, False)
            cd = f"rd{i}"
    if pre:
        L.append(f"  wire {sdecl(sa)}[{wa}:0] pa = {ca} + {cd};")
        ca = "pa"
    L.append(f"  wire {sdecl(signed_m)}[{wm-1}:0] m = {ca} * {cb};")
    cm = "m"
    if m_reg:
        reg("rm", wm, signed_m, "m", False)
        cm = "rm"

    nz = rng.randint(1, (1 << min(wp, 16)) - 1)
    if post == "acc":
        L.append(f"  reg {sdecl(signed_m)}[{wp-1}:0] q{' = ' + str(wp) + chr(39) + 'd5' if init_out else ''};")
        body = f"q <= q + {cm};"
        if "srst" in out_reg:
            rv = f"{wp}'d{nz}" if out_reg.endswith("N") else "0"
            body = f"if (rst) q <= {rv}; else {body}"
        if out_reg.startswith("ce"):
            body = f"if (ce) begin {body} end"
        L.append(f"  always @(posedge clk) {body}")
        L.append("  assign p = q;")
    else:
        expr = f"{cm} + c" if post == "addc" else cm
        if out_reg == "none":
            L.append(f"  assign p = {expr};")
        else:
            L.append(f"  reg {sdecl(signed_m)}[{wp-1}:0] q{' = ' + str(wp) + chr(39) + 'd3' if init_out else ''};")
            body = f"q <= {expr};"
            if "srst" in out_reg:
                rv = f"{wp}'d{nz}" if out_reg.endswith("N") else "0"
                body = f"if (rst) q <= {rv}; else {body}"
            if out_reg.startswith("ce"):
                body = f"if (ce) begin {body} end"
            L.append(f"  always @(posedge clk) {body}")
            L.append("  assign p = q;")
    L.append("endmodule")
    meta = dict(idx=idx, wa=wa, wb=wb, sa=sa, sb=sb, wm=wm, wp=wp, wc=wc, pre=pre, post=post,
                in_reg=in_reg, m_reg=m_reg, out_reg=out_reg, init_in=init_in, init_out=init_out)
    return "\n".join(L) + "\n", meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("-n", type=int, default=200)
    ap.add_argument("-s", "--seed", type=int, default=1)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    os.makedirs(args.outdir, exist_ok=True)
    metas = []
    for i in range(args.n):
        v, meta = gen_case(rng, i)
        with open(os.path.join(args.outdir, f"case{i:04d}.v"), "w") as f:
            f.write(f"// {json.dumps(meta)}\n" + v)
        metas.append(meta)
    with open(os.path.join(args.outdir, "cases.json"), "w") as f:
        json.dump(metas, f, indent=1)


if __name__ == "__main__":
    main()
