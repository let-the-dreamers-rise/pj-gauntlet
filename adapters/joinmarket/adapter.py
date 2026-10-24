"""pj-gauntlet adapter for JoinMarket's BIP-78 sender (jmclient.payjoin).

Runs the same path as `sendpayment.py <bip21-with-pj>`: a native segwit JoinMarket
wallet synced from bitcoind, direct_send() builds the original, send_payjoin()
talks to the receiver through the in-process daemon, and JoinMarket's own checks,
signing, broadcast and timeout fallback decide what happens. Nothing in jmclient
is patched.

JoinMarket picks its own coins, so the adapter funds a fresh wallet itself from
the harness miner wallet (two 0.6 BTC coins in mixdepth 0 for the payment and a
spare coin in mixdepth 1 that the receiver may try to slip in), and tells the
receiver about the spare through job["spare_file"].
"""
import json
import os
import shutil
import sys
import tempfile
import urllib.parse

job = json.load(sys.stdin)
JM_SRC = os.environ.get("JM_SRC", os.path.join(os.path.dirname(__file__), "joinmarket-clientserver"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from pjg.rpc import RPC  # noqa: E402

rpc = RPC(job["rpc"])
u = urllib.parse.urlparse(job["rpc"])
wallet_rpc_name = "jm-" + os.urandom(4).hex()
# watch-only descriptor wallet for JoinMarket's imports; RegtestBitcoinCoreInterface also calls
# getnewaddress on it, so give it one active public descriptor borrowed from the miner wallet
rpc.createwallet(wallet_rpc_name, True, True, "", False, True)
pub = next(d for d in rpc.w("pjg-miner").listdescriptors()["descriptors"]
           if d["desc"].startswith("wpkh(") and not d.get("internal"))
rpc.w(wallet_rpc_name).importdescriptors([{"desc": pub["desc"], "active": True, "range": [0, 10],
                                           "timestamp": "now", "internal": False}])

datadir = tempfile.mkdtemp(prefix="jm-")
cfg = open(os.path.join(JM_SRC, "test", "regtest_joinmarket.cfg")).read()
port = 27000 + int.from_bytes(os.urandom(2), "big") % 20000
cfg = (cfg.replace("rpc_user = bitcoinrpc", "rpc_user = " + u.username)
          .replace("rpc_password = 123456abcdef", "rpc_password = " + u.password)
          .replace("rpc_port = 18443", "rpc_port = %d" % u.port)
          .replace("rpc_wallet_file = jm-test-wallet", "rpc_wallet_file = " + wallet_rpc_name)
          .replace("daemon_port = 27183", "daemon_port = %d" % port))
cfg += "\n[POLICY]\nnative = true\ntx_fees = %d\ntx_fees_factor = 0\nmerge_algorithm = default\n" % (job["feerate"] * 1000)
open(os.path.join(datadir, "joinmarket.cfg"), "w").write(cfg)

from twisted.internet import reactor  # noqa: E402
from jmclient import (load_program_config, jm_single, open_test_wallet_maybe, SegwitWallet,  # noqa: E402
                      WalletService, start_reactor)
from jmclient.payjoin import parse_payjoin_setup, send_payjoin  # noqa: E402

load_program_config(config_path=datadir)
seed = os.urandom(16).hex()
wallet = open_test_wallet_maybe(seed, seed, 4, test_wallet_cls=SegwitWallet)
ws = WalletService(wallet)
miner = rpc.w("pjg-miner")
a1, a2 = ws.get_new_addr(0, 0), ws.get_new_addr(0, 0)
spare_addr = ws.get_new_addr(1, 0)
txid = miner.sendmany("", {a1: 0.6, a2: 0.6, spare_addr: 0.6})
miner.generatetoaddress(1, miner.getnewaddress())
while not ws.synced:
    ws.sync_wallet(fast=False)
ws.startService()
tx = rpc.getrawtransaction(txid, True)
for o in tx["vout"]:
    if o["scriptPubKey"].get("address") == spare_addr:
        spare = {"txid": txid, "vout": o["n"], "value": round(o["value"] * 1e8), "script": o["scriptPubKey"]["hex"]}
if job.get("spare_file"):
    json.dump(spare, open(job["spare_file"], "w"))

manager = parse_payjoin_setup(job["bip21"], ws, 0)
result = {}


def accept(*a, **k):
    return True


reactor.callWhenRunning(send_payjoin, manager, accept_callback=accept, info_callback=lambda *a: None)
try:
    start_reactor("localhost", port, bip78=True, jm_coinjoin=False, daemon=True)
except SystemExit:
    pass
state = manager.pj_state
names = {v: k for k, v in vars(type(manager)).items() if k.startswith("JM_PJ_")}
st = names.get(state, str(state))
if st == "JM_PJ_PAYJOIN_BROADCAST":
    result = {"result": "payjoin", "txid": manager.final_psbt.extract_transaction().GetTxid()[::-1].hex()}
elif st == "JM_PJ_PAYJOIN_BROADCAST_FAILED":
    result = {"result": "fallback", "state": st}
else:
    result = {"result": "error", "state": st}
shutil.rmtree(datadir, ignore_errors=True)
try:
    rpc.unloadwallet(wallet_rpc_name)
except Exception:
    pass
print(json.dumps(result))
