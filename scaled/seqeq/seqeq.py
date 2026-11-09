#!/usr/bin/env python3
"""Sequential equivalence of RTL vs synth_xilinx netlists with eqy (SymbiYosys).

For each case, the RTL (gold) and the mapped netlist (gate, with the Xilinx
primitive models from cells_sim.v) are compared on their ports only, so
pipelined state that synthesis moved into DSP/FF primitives sits inside one
partition and is proven with k-induction (unbounded) by the SMT engine.

Works directly on scaled/dspfuzz run directories (caseNNNN.v + caseNNNN.gate.v).

X propagation (eqy's default) is tried first; an induction failure is retried
with xprop off. TIMEOUT means inconclusive (typically wide multipliers, which
SAT/SMT solvers prove slowly), not a failure.

Usage:
  seqeq.py DIR [--cases 0-99] [--cells techlibs/xilinx/cells_sim.v] [-j 8]
           [--depth 8] [--timeout 600] [--engine "smtbmc bitwuzla"]
Requires eqy/sby/bitwuzla on PATH (e.g. oss-cad-suite/bin).
"""
import argparse, glob, os, re, signal, subprocess, sys, json
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
ap = argparse.ArgumentParser()
ap.add_argument('dir')
ap.add_argument('--cases', default='')
ap.add_argument('--cells', default=os.path.join(ROOT, 'techlibs', 'xilinx', 'cells_sim.v'))
ap.add_argument('-j', type=int, default=os.cpu_count())
ap.add_argument('--depth', type=int, default=8)
ap.add_argument('--timeout', type=int, default=600)
ap.add_argument('--engine', default='smtbmc bitwuzla')
ap.add_argument('--out', default=None, help='work directory (default DIR/seqeq)')
a = ap.parse_args()

cases = sorted(glob.glob(os.path.join(a.dir, 'case[0-9][0-9][0-9][0-9].v')))
if a.cases:
    lo, _, hi = a.cases.partition('-')
    keep = set(range(int(lo), int(hi or lo) + 1))
    cases = [c for c in cases if int(re.search(r'case(\d+)', c).group(1)) in keep]
work = os.path.abspath(a.out or os.path.join(a.dir, 'seqeq'))
os.makedirs(work, exist_ok=True)

def run_eqy(cmd, timeout):
    """Run eqy; return its stdout, or None on timeout.

    eqy forks make -> sby -> yosys-smtbmc -> solver.  subprocess.run's timeout
    kills only eqy, orphaning that whole tree (observed: hundreds of bitwuzla
    processes grinding for hours after their case had already been scored
    TIMEOUT).  So start eqy in its own process group and kill the group."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        return out
    except subprocess.TimeoutExpired:
        pass
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try: os.killpg(p.pid, sig)
        except ProcessLookupError: break
        try:
            p.communicate(timeout=5)
            break
        except subprocess.TimeoutExpired:
            continue   # sby ignores SIGTERM; escalate to SIGKILL
    return None

def one(rtl, xprop=True):
    name = os.path.basename(rtl)[:-2]
    gate = rtl[:-2] + '.gate.v'
    if not os.path.exists(gate):
        return name, 'NO_GATE'
    top = re.search(r'^module\s+(\w+)', open(gate).read(), re.M).group(1)
    cfg = f'''[gold]
read_verilog {os.path.abspath(rtl)}
prep -top top
# Xilinx registers power up to 0 (GSR) unless INIT says otherwise; give
# uninitialized RTL registers that value instead of an arbitrary one
setundef -init -zero

[gate]
logger -nowarn limited.support.for.tri-state.*
read_verilog -nowb -noblackbox {a.cells}
read_verilog {os.path.abspath(gate)}
prep -top {top}
rename {top} top
flatten
opt -noff -keepdc -fast

[script]
# compare on ports only: internal state is not expected to correspond
rename -hide w:* i:* o:* %u %d

[strategy sby]
use sby
xprop {'on' if xprop else 'off'}
depth {a.depth}
engine {a.engine}
'''
    if not xprop: name += '.noxprop'
    fn = os.path.join(work, name + '.eqy')
    open(fn, 'w').write(cfg)
    out = run_eqy(['eqy', '-f', '-j', '1', '-d', os.path.join(work, name), fn], a.timeout)
    if out is None: return name, 'TIMEOUT'
    if 'DONE (PASS' in out: return name, 'PROVED'
    if re.search(r'Failed to prove|DONE \(FAIL', out):
        # distinguish a real counterexample (basecase) from inconclusive induction
        logs = glob.glob(os.path.join(work, name, 'strategies', '*', 'sby', '*', 'logfile.txt'))
        txt = ''.join(open(l).read() for l in logs)
        if 'returned FAIL for basecase' in txt: return name, 'COUNTEREXAMPLE'
        if 'induction' in txt and 'FAIL' in txt:
            # with xprop, X in unobservable state bits (e.g. the top of a
            # 48-bit P register) can leak through word-level adders; retry
            # treating X as an arbitrary value
            if xprop:
                n2, r2 = one(rtl, xprop=False)
                return name, r2
            return name, 'INDUCTION_FAILED'
        return name, 'FAIL'
    return name, 'ERROR'

with ThreadPoolExecutor(a.j) as ex:
    res = list(ex.map(one, cases))
summary = {}
for n, r in res:
    summary[r] = summary.get(r, 0) + 1
    if r != 'PROVED': print(f'{n}: {r}')
json.dump(dict(res), open(os.path.join(work, 'results.json'), 'w'), indent=1)
print('SUMMARY', json.dumps(summary))
