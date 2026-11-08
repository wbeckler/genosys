#!/usr/bin/env python3
"""QoR of parser-shaped variable-offset extraction kernels under synth_xilinx.

For each kernel configuration, run synth_xilinx (-family, default xcup) with one
or more Yosys binaries and report LUTs, MUXF7/F8/F9, CARRY8 and the logic depth
(ltp -noff, in cells: LUTs + wide-mux cells).

Usage: qor.py --yosys A=path/to/yosys --yosys B=path ... [--extra "-widemux 4"]
"""
import argparse, json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DESIGNS = os.path.join(HERE, 'designs.v')

CASES = [
    ('byte_extract', {'IN': 512, 'SELW': 6, 'W': 8}),
    ('byte_extract', {'IN': 512, 'SELW': 6, 'W': 32}),
    ('byte_extract', {'IN': 512, 'SELW': 6, 'W': 64}),
    ('byte_extract', {'IN': 128, 'SELW': 4, 'W': 32}),
    ('bit_extract', {'IN': 128, 'OFFW': 7, 'W': 16}),
    ('bit_extract', {'IN': 64, 'OFFW': 6, 'W': 32}),
    ('stride_select', {'N': 13, 'STRIDE': 34, 'W': 14}),
    ('stride_select', {'N': 13, 'STRIDE': 34, 'W': 40}),
    ('byte_shift', {'IN': 256, 'SELW': 5}),
    ('msgtype_field', {}),
    ('msgtype_case', {}),
    ('be_extract', {}),
    ('word_index', {}),
]

def run(ybin, top, params, family, extra):
    chp = ' '.join(f'-set {k} {v}' for k, v in params.items())
    script = f'read_verilog {DESIGNS}; hierarchy -top {top} {("-chparam " + " -chparam ".join(f"{k} {v}" for k, v in params.items())) if params else ""}; ' \
             f'synth_xilinx -family {family} -noiopad -noclkbuf {extra}; tee -o /dev/stdout stat -json; tee -o /dev/stdout ltp -noff'
    cp = subprocess.run([ybin, '-q', '-p', script], capture_output=True, text=True)
    if cp.returncode:
        return {'error': cp.stderr[-500:] or cp.stdout[-500:]}
    out = cp.stdout
    js = json.loads(out[out.index('{'):out.rindex('}') + 1])
    cells = js['design']['num_cells_by_type'] if 'design' in js else list(js['modules'].values())[0]['num_cells_by_type']
    m = re.search(r'length=(\d+)', out)
    lut = sum(v for k, v in cells.items() if re.fullmatch(r'LUT\d', k))
    return {'LUT': lut, 'F7': cells.get('MUXF7', 0), 'F8': cells.get('MUXF8', 0), 'F9': cells.get('MUXF9', 0),
            'CARRY': cells.get('CARRY8', 0) + cells.get('CARRY4', 0), 'DSP': sum(v for k, v in cells.items() if k.startswith('DSP')), 'depth': int(m.group(1)) if m else -1}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--yosys', action='append', required=True, help='NAME=path')
    ap.add_argument('--family', default='xcup')
    ap.add_argument('--extra', default='')
    ap.add_argument('--json')
    a = ap.parse_args()
    bins = [y.split('=', 1) for y in a.yosys]
    jobs = [(name, path, top, p) for top, p in CASES for name, path in bins]
    with ThreadPoolExecutor(os.cpu_count()) as ex:
        res = list(ex.map(lambda j: run(j[1], j[2], j[3], a.family, a.extra), jobs))
    table = {}
    for (name, _, top, p), r in zip(jobs, res):
        key = top + ''.join(f' {k}={v}' for k, v in p.items())
        table.setdefault(key, {})[name] = r
    hdr = f'{"case":44s}' + ''.join(f'| {n:^33s}' for n, _ in bins)
    print(hdr); print(f'{"":44s}' + '| LUT    F7   F8  F9  CY DSP depth ' * len(bins))
    for key, row in table.items():
        line = f'{key:44s}'
        for n, _ in bins:
            r = row[n]
            line += '| ' + ('ERROR'.ljust(33) if 'error' in r else f'{r["LUT"]:5d} {r["F7"]:4d} {r["F8"]:4d} {r["F9"]:3d} {r["CARRY"]:3d} {r["DSP"]:3d} {r["depth"]:4d} ')
        print(line)
    if a.json:
        json.dump(table, open(a.json, 'w'), indent=1)

main()
