"""Drive one sender adapter through every case against the hostile receiver.

    python3 -m pjg.runner --adapter adapters/bluewallet/run.sh [--cases C01,A01] [--out results/x.json]

An adapter is any executable that reads one job (JSON) on stdin, builds and
signs the original transaction with the wallet code under test, runs that
wallet's payjoin sender against `bip21`, and prints one JSON line:
{"result": "payjoin"|"fallback"|"error", "txid": ..., "error": ...}.
The oracle never trusts that line: it reads the mempool.
"""
import argparse
import json
import os
import subprocess
import sys
import time

from .cases import CASES
from .keys import Key
from .receiver import Receiver
from .rpc import RPC, RPCError

TLS = False
FEERATE = 10  # sat/vB for the sender's original transaction
AMOUNT = 100_000_000  # 1 BTC payment
UTXO = 60_000_000  # two of these per run, so the original has 2 inputs and change


def ensure_wallets(rpc):
    loaded = rpc.listwallets()
    for name in ("pjg-miner", "pjg-receiver"):
        if name not in loaded:
            try:
                rpc.loadwallet(name)
            except RPCError:
                rpc.createwallet(name)
    miner = rpc.w("pjg-miner")
    if rpc.getblockcount() < 110:
        miner.generatetoaddress(110, miner.getnewaddress())
    recv = rpc.w("pjg-receiver")
    have = recv.listunspent(1)
    kinds = {"bech32": 0, "bech32m": 0}
    for u in have:
        spk = u["scriptPubKey"]
        if spk.startswith("0014"):
            kinds["bech32"] += 1
        elif spk.startswith("5120"):
            kinds["bech32m"] += 1
    outs = {}
    for kind, n in kinds.items():
        for _ in range(max(0, 40 - n)):
            outs[recv.getnewaddress("", kind)] = 0.3
    if outs:
        miner.sendmany("", outs)
        miner.generatetoaddress(1, miner.getnewaddress())
    return miner, recv


def fund(miner, rpc, n):
    """n fresh p2wpkh keys holding UTXO sats each, confirmed."""
    keys = [Key() for _ in range(n)]
    outs = {k.p2wpkh_address(): UTXO / 1e8 for k in keys}
    txid = miner.sendmany("", outs)
    miner.generatetoaddress(1, miner.getnewaddress())
    tx = rpc.getrawtransaction(txid, True)
    res = []
    for k in keys:
        spk = k.p2wpkh_script().hex()
        vout = next(o["n"] for o in tx["vout"] if o["scriptPubKey"]["hex"] == spk)
        res.append({"txid": txid, "vout": vout, "value": UTXO, "script": spk, "wif": k.wif(),
                    "address": k.p2wpkh_address(), "pubkey": k.pub.hex()})
    return res


def in_mempool(rpc, txid):
    try:
        return rpc.getmempoolentry(txid)
    except RPCError:
        return None


def judge(case, expect, got_payjoin, got_original, adapter_out):
    if expect == "n/a":
        return "N/A"
    if expect == "info":
        return "INFO"
    if expect == "accept":
        return "PASS" if got_payjoin else "FAIL"
    if got_payjoin:
        return "FAIL"
    if not got_original:
        return "WARN"  # proposal refused, but the payment was never made either
    return "PASS"


def run_case(case, adapter, rpc, miner, recv_wallet, receiver, extra_env, timeout):
    pay_key = Key()
    utxos = fund(miner, rpc, 3)
    spare = utxos.pop()
    change = Key()
    receiver.spare_utxo = spare
    spare_file = "/tmp/pjg-spare-%s.json" % case.id
    if os.path.exists(spare_file):
        os.remove(spare_file)
    receiver.spare_file = spare_file
    receiver.arm(case, pay_key.p2wpkh_script(), case.pjos_off)
    if case.transport == "http-remote":
        pj = "http://pjg.test:%d/pj" % receiver.port
    elif TLS and receiver.tls_port:
        pj = "https://localhost:%d/pj" % receiver.tls_port
    else:
        pj = "http://127.0.0.1:%d/pj" % receiver.port
    bip21 = "bitcoin:%s?amount=1&pj=%s" % (pay_key.p2wpkh_address(), pj)
    if case.pjos_off:
        bip21 += "&pjos=0"
    job = {"rpc": rpc.url, "network": "regtest", "bip21": bip21, "amount_sat": AMOUNT,
           "address": pay_key.p2wpkh_address(), "utxos": utxos, "spare": spare,
           "change": {"wif": change.wif(), "address": change.p2wpkh_address(),
                      "script": change.p2wpkh_script().hex()},
           "feerate": FEERATE, "case": case.id, "spare_file": spare_file}
    t0 = time.time()
    try:
        p = subprocess.run([adapter], input=json.dumps(job), capture_output=True, text=True,
                           timeout=case.timeout or timeout, env={**os.environ, **extra_env})
        out, err, code = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        out, err, code = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "timeout", -1
    elapsed = time.time() - t0
    adapter_out = None
    for line in reversed((out or "").strip().splitlines()):
        try:
            adapter_out = json.loads(line)
            break
        except ValueError:
            continue
    time.sleep(0.5)
    entry = receiver.log[0] if receiver.log else None
    ctx = entry.get("ctx") if entry else None
    expect = (ctx.expect if ctx and ctx.expect else case.expect)
    prop_txid = entry.get("proposal_txid") if entry else None
    orig_txid = ctx.orig_tx.txid() if ctx else None
    got_payjoin = bool(prop_txid and in_mempool(rpc, prop_txid))
    got_original = bool(orig_txid and in_mempool(rpc, orig_txid))
    verdict = judge(case, expect, got_payjoin, got_original, adapter_out)
    if not entry:
        # refusing a plain-http endpoint without contacting it is the conforming answer to T04
        # (Sparrow then pays without payjoin; payjoin-cli refuses the URI outright)
        verdict = "PASS" if case.transport == "http-remote" else "NO-REQUEST"
    rec = {"case": case.id, "title": case.title, "rule": case.rule, "expect": expect, "verdict": verdict,
           "payjoin_broadcast": got_payjoin, "original_broadcast": got_original,
           "adapter": adapter_out, "adapter_exit": code, "elapsed_s": round(elapsed, 1),
           "notes": list(ctx.notes) if ctx else [], "params": entry["params"] if entry else None,
           "proposal_txid": prop_txid, "original_txid": orig_txid}
    if got_payjoin and ctx:
        me = in_mempool(rpc, prop_txid)
        real_fee = round(me["fees"]["base"] * 1e8)
        tx_ = ctx.psbt.tx
        claimed_in = 0
        for i, t in enumerate(tx_.vin):
            u = ctx.psbt.input_utxo(i) or ctx.utxos[t.outpoint]
            claimed_in += u.value
        claimed_fee = claimed_in - sum(o.value for o in tx_.vout)
        # sender = whoever owns the original's inputs and its non-payment outputs
        orig = ctx.orig_tx
        sender_out_scripts = {o.script for o in orig.vout if o.script != ctx.payment_script}
        orig_in = sum(ctx.utxos[t.outpoint].value for t in orig.vin)
        orig_cost = orig_in - sum(o.value for o in orig.vout if o.script in sender_out_scripts)
        tx = ctx.psbt.tx
        sender_back = sum(o.value for o in tx.vout if o.script in sender_out_scripts)
        sender_in = sum(ctx.utxos[t.outpoint].value for t in tx.vin if t.outpoint in {i.outpoint for i in orig.vin})
        rec.update({"real_fee": real_fee, "claimed_fee": claimed_fee, "original_fee": ctx.orig_fee,
                    "real_feerate": round(real_fee / me["vsize"], 2), "original_feerate": round(ctx.orig_rate, 2),
                    "sender_extra_cost": (sender_in - sender_back) - orig_cost,
                    "case_value": getattr(ctx, "case_value", 0)})
    if verdict in ("FAIL", "WARN", "NO-REQUEST"):
        rec["stderr_tail"] = (err or "")[-1500:]
    miner.generatetoaddress(1, miner.getnewaddress())
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--cases", default="")
    ap.add_argument("--skip", default="")
    ap.add_argument("--out")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--name")
    ap.add_argument("--tls", action="store_true", help="serve the pj endpoint over https (test CA in tls/)")
    a = ap.parse_args()
    global TLS
    TLS = a.tls
    rpc = RPC()
    miner, recv = ensure_wallets(rpc)
    receiver = Receiver(rpc)
    sel = [c for c in CASES if (not a.cases or c.id in a.cases.split(",")) and c.id not in a.skip.split(",")]
    results = []
    for c in sel:
        r = run_case(c, os.path.abspath(a.adapter), rpc, miner, recv, receiver, {}, a.timeout)
        results.append(r)
        extra = ""
        if r.get("sender_extra_cost") is not None:
            extra = " real fee %s (claimed %s), sender extra cost %s" % (r["real_fee"], r["claimed_fee"], r["sender_extra_cost"])
        err = (r.get("adapter") or {}).get("error") or ""
        print("%-4s %-10s expect=%-6s pj=%d orig=%d %s%s  %s" % (c.id, r["verdict"], r["expect"], r["payjoin_broadcast"],
              r["original_broadcast"], c.title[:60], extra, err[:100]), flush=True)
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        json.dump({"adapter": a.name or a.adapter, "results": results}, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
