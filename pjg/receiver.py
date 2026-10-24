"""Hostile BIP-78 receiver.

For every original PSBT it builds an honest payjoin proposal (one receiver
input, the fee bump taken from the sender's fee output within the limits the
sender asked for), then lets the active test case rewrite it. Every request and
proposal is recorded so the oracle can judge what the sender did with it.
"""
import json
import os
import math
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .psbt import (PSBT, Tx, TxIn, TxOut, I_WITNESS_UTXO, I_NON_WITNESS_UTXO, I_FINAL_SCRIPTSIG,
                   I_FINAL_SCRIPTWITNESS, read_bytes, read_varint)
import io

P2WPKH_INPUT_VSIZE = 68  # what senders assume for one extra p2wpkh input
# Taproot key-path input: 230 WU. PDK bounds the contribution by originalFeeRate (per WU) times the
# receiver input's own weight, which is stricter than BIP-78's vsize(sender_input_type); the honest
# taproot proposal stays within both readings.
P2TR_INPUT_WEIGHT = 230


def finalized_tx(psbt):
    """Network transaction of a finalized PSBT (for vsize and txid)."""
    tx = psbt.tx
    wits = []
    for i, m in enumerate(psbt.inputs):
        ss = m.get(I_FINAL_SCRIPTSIG)
        if ss:
            tx.vin[i].script_sig = ss
        fw = m.get(I_FINAL_SCRIPTWITNESS)
        if fw:
            s = io.BytesIO(fw)
            wits.append([read_bytes(s) for _ in range(read_varint(s))])
        else:
            wits.append([])
    tx.witnesses = wits
    return tx


def weight(tx):
    return len(tx.serialize(with_witness=False)) * 3 + len(tx.serialize(with_witness=True))


def vsize(tx):
    return math.ceil(weight(tx) / 4)


class Ctx:
    """Everything a test case may read or rewrite for one payjoin request."""

    def __init__(self, receiver, original_b64, params):
        self.r = receiver
        self.params = params
        self.original = PSBT.from_b64(original_b64)
        self.orig_tx = self.original.tx
        self.orig_signed = finalized_tx(self.original)
        self.orig_fee = self.original.fee()
        self.orig_vsize = vsize(self.orig_signed)
        self.orig_weight = weight(self.orig_signed)
        self.orig_rate = self.orig_fee / self.orig_vsize
        self.payment_script = receiver.payment_script
        self.payment_index = next(
            (i for i, o in enumerate(self.orig_tx.vout) if o.script == self.payment_script), None)
        fi = params.get("additionalfeeoutputindex")
        mc = params.get("maxadditionalfeecontribution")
        self.fee_index = None
        self.max_contrib = 0
        if fi is not None and mc is not None:
            fi, mc = int(fi), int(float(mc))
            if 0 <= fi < len(self.orig_tx.vout) and fi != self.payment_index and mc >= 0:
                self.fee_index, self.max_contrib = fi, mc
        self.minfeerate = float(params["minfeerate"]) if "minfeerate" in params else None
        self.pjos_off = receiver.pjos_off
        self.substitution_allowed = not self.pjos_off and params.get("disableoutputsubstitution",
                                                                      "false").lower() != "true"
        # proposal under construction
        self.tx = self.orig_tx.copy()
        self.utxos = {}  # outpoint -> TxOut, for every input we know
        for i, txin in enumerate(self.orig_tx.vin):
            self.utxos[txin.outpoint] = self.original.input_utxo(i)
        self.receiver_outpoints = []
        self.contribution = 0
        self.notes = []
        self.expect = None  # case may override "reject"/"accept" at runtime
        self.psbt = None

    # helpers for cases -------------------------------------------------
    def sender_input_indices(self):
        ours = {i.outpoint for i in self.orig_tx.vin}
        return [i for i, t in enumerate(self.tx.vin) if t.outpoint in ours]

    def receiver_input_indices(self):
        return [i for i, t in enumerate(self.tx.vin) if t.outpoint in set(self.receiver_outpoints)]

    def change_index(self):
        """Index in the proposal of the sender's first non-payment output."""
        for o in self.orig_tx.vout:
            if o.script != self.payment_script:
                return self.out_index(o.script)
        return None

    def out_index(self, script):
        return next((i for i, o in enumerate(self.tx.vout) if o.script == script), None)

    def add_receiver_input(self, kind="bech32", index=None):
        u = self.r.take_utxo(kind)
        txin = TxIn(bytes.fromhex(u["txid"])[::-1], u["vout"], b"", self.orig_tx.vin[0].sequence)
        if index is None:
            self.tx.vin.append(txin)
        else:
            self.tx.vin.insert(index, txin)
        self.utxos[txin.outpoint] = TxOut(round(u["amount"] * 1e8), bytes.fromhex(u["scriptPubKey"]))
        self.receiver_outpoints.append(txin.outpoint)
        return self.utxos[txin.outpoint].value

    def honest(self, kind="bech32", index=None, bump_inputs=1):
        """Standard proposal: one receiver input, fee bump split as BIP-78 suggests."""
        value = self.add_receiver_input(kind, index)
        if kind == "bech32m":
            per_input = self.orig_fee * P2TR_INPUT_WEIGHT / self.orig_weight
        else:
            per_input = self.orig_rate * P2WPKH_INPUT_VSIZE
        need = math.ceil(per_input * bump_inputs)
        contrib = 0
        if self.fee_index is not None:
            # a careful receiver stays under originalFeeRate * vsize, rounded down
            contrib = min(self.max_contrib, math.floor(per_input * bump_inputs))
            self.tx.vout[self.fee_index].value -= contrib
        self.contribution = contrib
        pi = self.out_index(self.payment_script)
        self.tx.vout[pi].value += value - (need - contrib)
        return value

    # signing ----------------------------------------------------------
    def sign(self):
        p = PSBT.from_tx(self.tx)
        for i, txin in enumerate(self.tx.vin):
            u = self.utxos.get(txin.outpoint)
            if u is not None:
                p.inputs[i].set(I_WITNESS_UTXO, u.serialize())
        res = self.r.wallet.walletprocesspsbt(p.b64(), True, "ALL", False, True)
        signed = PSBT.from_b64(res["psbt"])
        out = PSBT.from_tx(self.tx)
        recv = set(self.receiver_outpoints)
        for i, txin in enumerate(self.tx.vin):
            if txin.outpoint in recv:
                m = signed.inputs[i]
                for k in (I_WITNESS_UTXO, I_FINAL_SCRIPTSIG, I_FINAL_SCRIPTWITNESS):
                    v = m.get(k)
                    if v is not None:
                        out.inputs[i].set(k, v)
        self.psbt = out
        return out


class Receiver:
    def __init__(self, rpc, wallet_name="pjg-receiver", port=0):
        self.rpc = rpc
        self.wallet = rpc.w(wallet_name)
        self.case = None
        self.payment_script = None
        self.pjos_off = False
        self.log = []
        self.lock = threading.Lock()
        self.used = set()
        handler = self._handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        # same endpoint over TLS, for senders that (correctly) refuse plain http
        import ssl
        tls_dir = os.path.join(os.path.dirname(__file__), "..", "tls")
        self.tls_port = None
        if os.path.exists(os.path.join(tls_dir, "server.pem")):
            self.httpsd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            sctx.load_cert_chain(os.path.join(tls_dir, "server.pem"), os.path.join(tls_dir, "server.key"))
            self.httpsd.socket = sctx.wrap_socket(self.httpsd.socket, server_side=True)
            self.tls_port = self.httpsd.server_address[1]
            threading.Thread(target=self.httpsd.serve_forever, daemon=True).start()

    def take_utxo(self, kind):
        addr_type = {"bech32": "witness_v0_keyhash", "bech32m": "witness_v1_taproot",
                     "p2sh-segwit": "scripthash"}[kind]
        for u in self.wallet.listunspent(1):
            key = (u["txid"], u["vout"])
            if key in self.used:
                continue
            spk = bytes.fromhex(u["scriptPubKey"])
            t = {"witness_v0_keyhash": spk[:2] == b"\x00\x14", "witness_v1_taproot": spk[:2] == b"\x51\x20",
                 "scripthash": spk[:1] == b"\xa9"}[addr_type]
            if t:
                self.used.add(key)
                return u
        raise RuntimeError("receiver has no %s utxo left" % kind)

    def arm(self, case, payment_script, pjos_off):
        with self.lock:
            self.case = case
            self.payment_script = payment_script
            self.pjos_off = pjos_off
            self.log = []

    def _handler(self):
        recv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body, ctype="text/plain"):
                b = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                u = urllib.parse.urlparse(self.path)
                params = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode().strip()
                entry = {"path": u.path, "params": params, "original": body,
                         "content_type": self.headers.get("Content-Type")}
                recv.log.append(entry)
                case = recv.case
                sf = getattr(recv, "spare_file", None)
                if sf and os.path.exists(sf):
                    recv.spare_utxo = json.load(open(sf))
                try:
                    ctx = Ctx(recv, body, params)
                    entry["ctx"] = ctx
                    action = case.respond(ctx)
                except Exception as e:  # harness bug: answer like a broken receiver
                    entry["error"] = repr(e)
                    import traceback
                    traceback.print_exc()
                    self._reply(500, json.dumps({"errorCode": "unavailable", "message": "harness error"}))
                    return
                if action is None:
                    entry["proposal"] = ctx.psbt.b64()
                    entry["proposal_txid"] = ctx.psbt.tx.txid()
                    self._reply(200, entry["proposal"])
                else:
                    code, payload = action
                    entry["http"] = code
                    self._reply(code, payload)

        return H
