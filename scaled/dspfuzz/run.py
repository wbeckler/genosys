#!/usr/bin/env python3
"""Synthesize generated cases with synth_xilinx and co-simulate RTL vs netlist.

For each caseNNNN.v:
  1. yosys: synth_xilinx, rename top->gate, write_verilog.
  2. iverilog: gold RTL (renamed to gold) + gate netlist + Xilinx cells_sim.v,
     driven by a random testbench biased toward corner values; any cycle where
     the gold output is fully defined and differs from the gate output fails.

Usage: run.py CASEDIR [--yosys PATH] [--synth-opts "..."] [-j N]
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def ports_of(src):
    hdr = re.search(r"module top\((.*?)\);", src, re.S).group(1)
    out = []
    for p in hdr.split(","):
        m = re.match(r"\s*(input|output)\s+(signed\s+)?(?:\[(\d+):0\]\s*)?(\w+)", p)
        out.append((m.group(1), int(m.group(3) or 0) + 1, m.group(4)))
    return out


def testbench(ports, cycles, seed):
    ins = [p for p in ports if p[0] == "input" and p[2] != "clk"]
    outs = [p for p in ports if p[0] == "output"]
    L = ["`timescale 1ns/1ps", "module tb;", "  reg clk = 0;", "  always #5 clk = !clk;"]
    for _, w, n in ins:
        L.append(f"  reg [{w-1}:0] {n} = 0;")
    for _, w, n in outs:
        L.append(f"  wire [{w-1}:0] g_{n}, x_{n};")
    conn_g = ", ".join([".clk(clk)"] + [f".{n}({n})" for _, _, n in ins] + [f".{n}(g_{n})" for _, _, n in outs])
    conn_x = ", ".join([".clk(clk)"] + [f".{n}({n})" for _, _, n in ins] + [f".{n}(x_{n})" for _, _, n in outs])
    L.append(f"  gold G({conn_g});")
    L.append(f"  gate X({conn_x});")
    L.append(f"  integer seed = {seed}, i, errs = 0;")
    L.append("  function [63:0] pick(input integer w); integer r; begin")
    L.append("    r = $random(seed) & 7;")
    L.append("    case (r) 0: pick = 0; 1: pick = {64{1'b1}}; 2: pick = 64'h1 << (w-1); 3: pick = ~(64'h1 << (w-1));")
    L.append("    4: pick = 1; default: pick = {$random(seed), $random(seed)}; endcase end endfunction")
    L.append("  initial begin")
    L.append(f"    for (i = 0; i < {cycles}; i = i + 1) begin")
    L.append("      @(negedge clk);")
    for _, w, n in ins:
        if n == "rst":
            L.append("      rst = (($random(seed) & 15) == 0);")
        elif n == "ce":
            L.append("      ce = (($random(seed) & 3) != 0);")
        else:
            L.append(f"      {n} = pick({w});")
    L.append("      #1;")
    for _, w, n in outs:
        L.append(f"      if (^g_{n} !== 1'bx && g_{n} !== x_{n}) begin errs = errs + 1;")
        L.append(f"        if (errs <= 5) $display(\"MISMATCH cyc=%0d {n} gold=%h gate=%h\", i, g_{n}, x_{n}); end")
    L.append("    end")
    L.append("    if (errs) $display(\"FAIL errs=%0d\", errs); else $display(\"PASS\");")
    L.append("    $finish;")
    L.append("  end")
    L.append("endmodule")
    return "\n".join(L) + "\n"


def run_case(path, yosys, share, synth_opts, cycles):
    base = os.path.splitext(path)[0]
    src = open(path).read()
    gate_v = base + ".gate.v"
    ys = (f"read_verilog {path}; synth_xilinx -top top -noiopad -noclkbuf {synth_opts}; "
          f"tee -q -o {base}.stat.json stat -json; rename top gate; write_verilog -noattr {gate_v}")
    r = subprocess.run([yosys, "-q", "-p", ys], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()
        return path, "SYNTH_ERROR", "\n".join(msg[-3:])
    gold_v = base + ".gold.v"
    open(gold_v, "w").write(src.replace("module top(", "module gold("))
    tb_v = base + ".tb.v"
    open(tb_v, "w").write(testbench(ports_of(src), cycles, 12345))
    simx = base + ".vvp"
    r = subprocess.run(["iverilog", "-g2012", "-s", "tb", "-o", simx, tb_v, gold_v, gate_v,
                        os.path.join(share, "xilinx", "cells_sim.v")], capture_output=True, text=True)
    if r.returncode != 0:
        return path, "SIM_BUILD_ERROR", r.stderr.strip()[-500:]
    r = subprocess.run(["vvp", "-n", simx], capture_output=True, text=True, timeout=600)
    out = r.stdout
    if "PASS" in out:
        return path, "PASS", ""
    return path, "FAIL", "\n".join(l for l in out.splitlines() if "MISMATCH" in l or "FAIL" in l)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("casedir")
    ap.add_argument("--yosys", default=os.path.join(ROOT, "build-rel", "yosys"))
    ap.add_argument("--share", default=None)
    ap.add_argument("--synth-opts", default="")
    ap.add_argument("--cycles", type=int, default=400)
    ap.add_argument("-j", type=int, default=os.cpu_count())
    a = ap.parse_args()
    share = a.share or os.path.join(os.path.dirname(a.yosys), "share")
    cases = sorted(os.path.join(a.casedir, f) for f in os.listdir(a.casedir) if re.match(r"case\d+\.v$", f))
    res = {}
    with cf.ThreadPoolExecutor(a.j) as ex:
        for path, status, detail in ex.map(lambda p: run_case(p, a.yosys, share, a.synth_opts, a.cycles), cases):
            res[os.path.basename(path)] = (status, detail)
            if status != "PASS":
                print(f"{os.path.basename(path)}: {status}\n  " + detail.replace("\n", "\n  "))
    counts = {}
    for s, _ in res.values():
        counts[s] = counts.get(s, 0) + 1
    print("SUMMARY", json.dumps(counts))
    json.dump(res, open(os.path.join(a.casedir, "results.json"), "w"), indent=1)
    sys.exit(0 if counts.get("PASS", 0) == len(res) else 1)


if __name__ == "__main__":
    main()
