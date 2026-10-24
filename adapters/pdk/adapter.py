"""pj-gauntlet adapter for the Payjoin Dev Kit reference sender (payjoin-cli, BIP-78 v1).

Runs the unmodified `payjoin-cli send` (rust-payjoin, built with --features v1) against a
bitcoind wallet holding the job's keys, as the PDK docs set it up. payjoin-cli does not
broadcast the fallback itself (it prints the command), so on failure this adapter broadcasts
the original, as a wallet integrating PDK is told to.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from pjg.rpc import RPC  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.environ.get("PAYJOIN_CLI", os.path.join(HERE, "payjoin-cli"))
job = json.load(sys.stdin)
rpc = RPC(job["rpc"])
name = "pdk-" + os.urandom(4).hex()
rpc.createwallet(name)
w = rpc.w(name)
descs = []
for u in job["utxos"] + [job["spare"]]:
    d = "wpkh(%s)" % u["wif"]
    d = d + "#" + w.getdescriptorinfo(d)["checksum"]
    descs.append({"desc": d, "timestamp": 0})
res = w.importdescriptors(descs)
assert all(r["success"] for r in res), res
# the spare coin stays in the wallet (and signable) but out of coin selection
sp = job["spare"]
w.lockunspent(False, [{"txid": sp["txid"], "vout": sp["vout"]}])
if job.get("spare_file"):
    json.dump({"txid": sp["txid"], "vout": sp["vout"], "value": sp["value"], "script": sp["script"]},
              open(job["spare_file"], "w"))

u = rpc.url.split("://", 1)[1]
creds, hostport = u.split("@")
user, pw = creds.split(":")
db = tempfile.mkdtemp(prefix="pdk-")
cmd = [CLI, "--rpchost", "http://%s/wallet/%s" % (hostport, name), "--rpcuser", user, "--rpcpassword", pw,
       "--db-path", os.path.join(db, "db"), "--root-certificate", os.path.join(HERE, "..", "..", "tls", "ca.der"),
       "send", job["bip21"], "--fee-rate", str(job["feerate"])]
p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
out = p.stdout + p.stderr
m = re.search(r"Payjoin sent\. TXID: ([0-9a-f]{64})", out)
if m:
    print(json.dumps({"result": "payjoin", "txid": m.group(1)}))
else:
    fb = re.search(r"sendrawtransaction ([0-9a-f]+)", out)
    result = {"result": "error", "error": out.strip().splitlines()[-1][:300] if out.strip() else "no output"}
    errline = [l for l in out.splitlines() if "Error processing response" in l or "HTTP request failed" in l]
    if errline:
        result["error"] = errline[0][-300:]
    if fb:
        rpc.sendrawtransaction(fb.group(1))
        result["result"] = "fallback"
    print(json.dumps(result))
try:
    rpc.unloadwallet(name)
except Exception:
    pass
