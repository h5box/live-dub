import re, sys
from collections import Counter
rows = [l.rstrip('\n').split('\t') for l in open(sys.argv[1], encoding='utf-8')]
head, rows = rows[0], rows[1:]
arms = head[5:]
# English term -> regex that extracts the Chinese rendering we care about
TERMS = {
    "triple lock": r"三重[一-鿿]{1,4}",
    "Burnham": r"[一-鿿]{0,2}伯[一-鿿]{1,2}|[一-鿿]*纳姆",
    "Colapinto|Colopinto": r"科[一-鿿]{2,4}|弗朗科",
    "Snedeker|Snedeka|Sneker|Seneker": r"斯内德[一-鿿]{1,2}|塞内克",
    "earnings": r"收入|收益|盈利|薪资|工资|利润",
}
for term, rx in TERMS.items():
    hits = [r for r in rows if re.search(term, r[3], re.I)]
    print(f"== {term}: {len(hits)} lines")
    for k, arm in enumerate(['live'] + arms):
        col = 4 + k
        c = Counter(m for r in hits for m in re.findall(rx, r[col]))
        print(f"  {arm:5s} {len(c)} variants: {dict(c.most_common(6))}")
