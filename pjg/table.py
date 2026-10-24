"""Render results/*.json as one Markdown table (rows = cases, columns = senders).

    python3 -m pjg.table results/*.json > RESULTS.md
"""
import json
import sys

from .cases import CASES

MARK = {"PASS": "pass", "FAIL": "**FAIL**", "WARN": "warn", "INFO": "info", "N/A": "n/a", "NO-REQUEST": "no request"}


def main(paths):
    runs = [json.load(open(p)) for p in paths]
    cols = [r["adapter"] for r in runs]
    by = [{x["case"]: x for x in r["results"]} for r in runs]
    print("| case | expected | " + " | ".join(cols) + " |")
    print("|---|---|" + "---|" * len(cols))
    for c in CASES:
        cells = []
        for b in by:
            x = b.get(c.id)
            if not x:
                cells.append("")
                continue
            cell = MARK.get(x["verdict"], x["verdict"])
            if x["verdict"] == "FAIL" and x.get("real_fee") is not None and x.get("claimed_fee") != x.get("real_fee"):
                cell += " (fee %d, shown %d)" % (x["real_fee"], x["claimed_fee"])
            cells.append(cell)
        print("| %s %s | %s | %s |" % (c.id, c.title, c.expect, " | ".join(cells)))
    print()
    for r, b in zip(runs, by):
        v = [x["verdict"] for x in b.values()]
        print("- %s: %d cases, %d pass, %d fail" % (r["adapter"], len(v), v.count("PASS"), v.count("FAIL")))


if __name__ == "__main__":
    main(sys.argv[1:])
