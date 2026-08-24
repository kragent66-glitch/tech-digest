#!/usr/bin/env python3
"""Validate every hidden test template in eval_harness.py against known-good
reference implementations. If a reference passes, the hidden tests are fair.
This is the reproducibility guard for the blog's eval evidence."""
import importlib.util, sys

spec = importlib.util.spec_from_file_location("harness", "eval_harness.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

REFS = {
 "two_sum": "def two_sum(nums,target):\n    d={}\n    for i,x in enumerate(nums):\n        if target-x in d: return [d[target-x],i]\n        d[x]=i\n    return []\n",
 "lru_cache": "from collections import OrderedDict\nclass LRUCache:\n    def __init__(self,c): self.c=c; self.o=OrderedDict()\n    def get(self,k):\n        if k not in self.o: return -1\n        self.o.move_to_end(k); return self.o[k]\n    def put(self,k,v):\n        if k in self.o: self.o.move_to_end(k)\n        self.o[k]=v\n        if len(self.o)>self.c: self.o.popitem(last=False)\n",
 "num_islands": "def num_islands(grid):\n    if not grid: return 0\n    n,m=len(grid),len(grid[0]); g=[r[:] for r in grid]; cnt=0\n    def dfs(i,j):\n        if not(0<=i<n and 0<=j<m) or g[i][j]!='1': return\n        g[i][j]='0'\n        for di,dj in ((1,0),(-1,0),(0,1),(0,-1)): dfs(i+di,j+dj)\n    for i in range(n):\n        for j in range(m):\n            if g[i][j]=='1': cnt+=1; dfs(i,j)\n    return cnt\n",
 "median_two_arrays": "def find_median_sorted_arrays(a,b):\n    if len(a)>len(b): a,b=b,a\n    n,m=len(a),len(b); lo,hi=0,n\n    while lo<=hi:\n        i=(lo+hi)//2; j=(n+m+1)//2-i\n        if i<n and b[j-1]>a[i]: lo=i+1\n        elif i>0 and a[i-1]>b[j]: hi=i-1\n        else:\n            ml=max(a[i-1] if i>0 else float('-inf'), b[j-1] if j>0 else float('-inf'))\n            if (n+m)%2: return float(ml)\n            mr=min(a[i] if i<n else float('inf'), b[j] if j<m else float('inf'))\n            return (ml+mr)/2.0\n",
 "reverse_words": "def reverse_words(s):\n    return ' '.join(s.split()[::-1])\n",
 "json_escape": "def json_escape(s):\n    out=[]\n    for ch in s:\n        o=ord(ch)\n        if ch=='\"': out.append('\\\\\"')\n        elif ch=='\\\\': out.append('\\\\\\\\')\n        elif ch=='\\n': out.append('\\\\n')\n        elif ch=='\\t': out.append('\\\\t')\n        elif ch=='\\r': out.append('\\\\r')\n        elif ch=='\\b': out.append('\\\\b')\n        elif ch=='\\f': out.append('\\\\f')\n        elif o<0x20: out.append('\\\\u%04x'%o)\n        else: out.append(ch)\n    return ''.join(out)\n",
 "multi_key_sort": "def sort_events(events):\n    return sorted(events, key=lambda e:(-e['priority'], e['start_time'], e['name']))\n",
}

ok = 0
bad = []
for tid, code in REFS.items():
    t = next(x for x in h.CODING_TASKS if x["id"] == tid)
    good, detail = h.run_python(code, t["hidden_tests"], tid)
    last = detail.strip().splitlines()[-1] if good else detail
    print(f"{'OK ' if good else 'BAD'} {tid}  {last}")
    ok += good
    if not good:
        bad.append(tid)

print(f"\nSelf-check: {ok}/{len(REFS)} reference implementations passed hidden tests.")
if bad:
    print("FAILING templates:", bad)
    sys.exit(1)
print("All hidden-test templates verified fair.")