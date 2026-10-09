import json, sys, statistics as st
recs = [json.loads(l) for l in open(sys.argv[1] + '/timing.jsonl', encoding='utf-8') if l.strip()]
subs = [r for r in recs if r.get('lag_s') is not None]
lead = -st.median([r['lag_s'] for r in subs])
by = {}
for r in subs:
    by.setdefault(int(r['pt0'] // 120), []).append(r)
for k in sorted(by):
    rs = by[k]
    lags = [r['lag_s'] + lead for r in rs]
    # how many units were emitted together (same committed second): big batches mean one ASR step committed a lot
    batch = {}
    for r in rs:
        batch[round(r['committed'])] = batch.get(round(r['committed']), 0) + 1
    print(f"pt {k*120:5d}-{k*120+120:5d}  n {len(rs):3d}  lag med {st.median(lags):5.1f} max {max(lags):5.1f}  max batch {max(batch.values())}")
