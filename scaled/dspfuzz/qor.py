"""Compare per-case resource use (DSP/FF/LUT/CARRY) of two run.py result dirs."""
import json,sys,collections
base,new=sys.argv[1],sys.argv[2]
def cells(p):
    d=json.load(open(p)); m=list(d['modules'].values())[0]
    c=m.get('num_cells_by_type',{})
    return dict(DSP=sum(v for t,v in c.items() if 'DSP' in t), FF=sum(v for t,v in c.items() if t.startswith('FD')),
                LUT=sum(v for t,v in c.items() if t.startswith('LUT')), CARRY=sum(v for t,v in c.items() if 'CARRY' in t))
rb=json.load(open(base+"/results.json")); rp=json.load(open(new+"/results.json"))
tot=collections.Counter(); diff=[]
for k in rb:
    if rb[k][0]!='PASS' or rp[k][0]!='PASS': continue
    s=k.replace('.v','.stat.json'); B,P=cells(f"{base}/{s}"),cells(f"{new}/{s}")
    for x in B: tot[x+'_base']+=B[x]; tot[x+'_new']+=P[x]
    if P!=B: diff.append((k,B,P))
print(dict(tot)); print(len(diff),"cases differ (both passing)")
for w in diff[:20]: print(w)
