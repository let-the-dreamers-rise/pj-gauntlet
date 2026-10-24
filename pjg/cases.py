"""Test cases: one honest control or one spec violation each.

Every case cites the BIP-78 rule it exercises. `expect` is what a conforming
sender must do: "accept" (sign and broadcast the payjoin), "reject" (never
broadcast this proposal; fall back to the original) or "info" (the spec does
not force either answer; the result is recorded, not judged).
"""
import math
import time

from .psbt import (TxOut, I_WITNESS_UTXO, I_NON_WITNESS_UTXO, I_PARTIAL_SIG, I_BIP32, I_FINAL_SCRIPTWITNESS,
                   O_BIP32, G_XPUB)
from .keys import Key

CASES = []

S_INPUTS = "Sender checklist: for each input in the proposal"
S_OUTPUTS = "Sender checklist: for each output in the proposal"


class Case:
    def __init__(self, cid, title, expect, rule, fn, pjos_off=False, needs=(), timeout=None, transport=None):
        self.id, self.title, self.expect, self.rule, self.fn = cid, title, expect, rule, fn
        self.transport = transport
        self.pjos_off = pjos_off
        self.needs = needs
        self.timeout = timeout

    def missing(self, ctx):
        if "fee_output" in self.needs and ctx.fee_index is None:
            return "sender sent no fee output parameters"
        if "change" in self.needs and ctx.change_index() is None:
            return "original has no change output"
        if "spare" in self.needs and not getattr(ctx.r, "spare_utxo", None):
            return "adapter has no spare utxo"
        return None

    def respond(self, ctx):
        why = self.missing(ctx)
        if why:
            ctx.expect = "n/a"
            ctx.notes.append(why)
            ctx.honest()
            ctx.sign()
            return None
        r = self.fn(ctx)
        if r is not None:
            return r
        if ctx.psbt is None:
            ctx.sign()
        return None


def case(cid, title, expect, rule, **kw):
    def deco(fn):
        CASES.append(Case(cid, title, expect, rule, fn, **kw))
        return fn
    return deco


def fresh_script():
    return Key().p2wpkh_script()


# ---------------------------------------------------------------- controls
@case("C01", "Honest payjoin: one receiver input, fee bump from the fee output", "accept",
      "Protocol: the valid baseline every other case is a mutation of")
def c01(ctx):
    ctx.honest()


@case("C02", "Receiver input inserted at index 0", "accept",
      "MUST NOT shuffle; additional inputs must be inserted at a random index")
def c02(ctx):
    ctx.honest(index=0)


@case("C03", "Payment output substituted (no pjos=0)", "accept",
      "MAY add or replace the outputs belonging to the receiver unless substitution is disabled")
def c03(ctx):
    ctx.honest()
    pi = ctx.out_index(ctx.payment_script)
    ctx.tx.vout[pi].script = fresh_script()


@case("C04", "Receiver adds no input, only swaps its payment script", "accept",
      "Note: the sender should allow the receiver to not add any inputs")
def c04(ctx):
    pi = ctx.out_index(ctx.payment_script)
    ctx.tx.vout[pi].script = fresh_script()


@case("C05", "Receiver batches an extra output paid from its own input", "accept",
      "Note: the method of checking the fee allows the receiver to batch payments")
def c05(ctx):
    value = ctx.honest()
    pi = ctx.out_index(ctx.payment_script)
    extra = value // 3
    ctx.tx.vout[pi].value -= extra + math.ceil(ctx.orig_rate * 31)
    ctx.tx.vout.insert(pi + 1, TxOut(extra, fresh_script()))


@case("C06", "Receiver input is taproot while sender is p2wpkh (mixed types)", "accept",
      "Mixed input types SHOULD NOT be used only until September 2024")
def c06(ctx):
    ctx.honest(kind="bech32m")


@case("C07", "Receiver takes exactly the advertised maxadditionalfeecontribution for one input", "info",
      "Fee output: contribution <= maxadditionalfeecontribution and <= originalFeeRate * vsize * added inputs",
      needs=("fee_output",))
def c07(ctx):
    value = ctx.add_receiver_input()
    need = math.ceil(ctx.orig_rate * 68)
    c = ctx.max_contrib
    ctx.tx.vout[ctx.fee_index].value -= c
    ctx.contribution = c
    ctx.tx.vout[ctx.out_index(ctx.payment_script)].value += value - max(0, need - c)
    ctx.notes.append("contribution %d; originalFeeRate*68 = %.2f" % (c, ctx.orig_rate * 68))


# ---------------------------------------------------------- transaction
@case("A01", "Absolute fee decreased (receiver pockets 500 sat of the sender's fee)", "reject",
      "Verify that the absolute fee of the payjoin proposal is equal or higher than the original")
def a01(ctx):
    ctx.honest()
    pi = ctx.out_index(ctx.payment_script)
    ctx.tx.vout[pi].value += ctx.contribution + math.ceil(ctx.orig_rate * 68) + 500


@case("A02", "Transaction version changed", "reject", "Verify that the transaction version is unchanged")
def a02(ctx):
    ctx.honest()
    ctx.tx.version = 1 if ctx.tx.version == 2 else 2


@case("A03", "nLockTime changed", "reject", "Verify that the nLockTime is unchanged")
def a03(ctx):
    ctx.honest()
    ctx.tx.locktime = ctx.tx.locktime + 1


@case("A04", "Sequence of every input changed (still uniform)", "reject",
      "Check that the sender's inputs' sequence numbers are unchanged")
def a04(ctx):
    ctx.honest()
    for t in ctx.tx.vin:
        t.sequence = 0xFFFFFFFE if t.sequence != 0xFFFFFFFE else 0xFFFFFFFD


@case("A05", "Receiver input uses a different sequence (mixed sequences)", "reject",
      "Verify that the payjoin proposal inputs all specify the same sequence value")
def a05(ctx):
    ctx.honest()
    r = ctx.receiver_input_indices()[0]
    ctx.tx.vin[r].sequence = 0xFFFFFFFE if ctx.tx.vin[r].sequence != 0xFFFFFFFE else 0xFFFFFFFD


@case("A06", "One of the sender's inputs dropped", "reject",
      "Verify that all of sender's inputs from the original PSBT are in the proposal")
def a06(ctx):
    ctx.honest()
    s = ctx.sender_input_indices()
    dropped = ctx.tx.vin.pop(s[-1])
    # the receiver keeps the money the dropped input would have brought
    ctx.tx.vout[ctx.out_index(ctx.payment_script)].value -= ctx.utxos[dropped.outpoint].value


@case("A07", "Sender inputs swapped (proposal shuffles the sender's inputs)", "info",
      "The proposal MUST NOT shuffle the order of inputs or outputs")
def a07(ctx):
    ctx.honest()
    s = ctx.sender_input_indices()
    if len(s) >= 2:
        ctx.tx.vin[s[0]], ctx.tx.vin[s[1]] = ctx.tx.vin[s[1]], ctx.tx.vin[s[0]]


@case("A08", "Sender's outputs swapped (proposal shuffles outputs)", "info",
      "The proposal MUST NOT shuffle the order of inputs or outputs")
def a08(ctx):
    ctx.honest()
    ctx.tx.vout.reverse()


# ---------------------------------------------------------- psbt fields
def _post(ctx, fn):
    ctx.honest()
    p = ctx.sign()
    fn(p)


@case("A10", "Receiver finalized one of the sender's inputs", "reject",
      "If it is one of the sender's inputs: verify the PSBT input is not finalized")
def a10(ctx):
    _post(ctx, lambda p: p.inputs[ctx.sender_input_indices()[0]].set(
        I_FINAL_SCRIPTWITNESS, bytes.fromhex("02") + bytes([71]) + b"\x30" * 71 + bytes([33]) + b"\x02" * 33))


@case("A11", "Partial signature added to a sender input", "reject",
      "Verify that no partial signature has been filled")
def a11(ctx):
    k = Key()
    _post(ctx, lambda p: p.inputs[ctx.sender_input_indices()[0]].add(I_PARTIAL_SIG, b"\x30" * 71 + b"\x01", k.pub))


@case("A12", "BIP32 keypath added to an input", "reject", "Verify that no keypaths are in the PSBT input")
def a12(ctx):
    k = Key()
    _post(ctx, lambda p: p.inputs[ctx.sender_input_indices()[0]].add(I_BIP32, b"\xde\xad\xbe\xef" + b"\x00" * 4, k.pub))


@case("A13", "BIP32 keypath added to an output", "reject", "Verify that no keypaths are in the PSBT output")
def a13(ctx):
    k = Key()
    _post(ctx, lambda p: p.outputs[0].add(O_BIP32, b"\xde\xad\xbe\xef" + b"\x00" * 4, k.pub))


@case("A14", "Global xpub included", "reject", "GlobalXPubs must not be included (reference implementation)")
def a14(ctx):
    # tpub-style 78-byte xpub payload; contents are irrelevant to the check
    _post(ctx, lambda p: p.glob.add(G_XPUB, b"\xde\xad\xbe\xef\x00\x00\x00\x00", b"\x04\x35\x87\xcf" + b"\x00" * 9 + b"\x11" * 32 + Key().pub))


@case("A15", "Receiver input left unfinalized", "reject",
      "If it is one of the receiver's inputs: verify the PSBT input is finalized")
def a15(ctx):
    _post(ctx, lambda p: p.inputs[ctx.receiver_input_indices()[0]].remove(0x07, 0x08))


@case("A16", "Receiver input without witness_utxo/non_witness_utxo", "reject",
      "If it is one of the receiver's inputs: verify that non_witness_utxo or witness_utxo are filled in")
def a16(ctx):
    _post(ctx, lambda p: p.inputs[ctx.receiver_input_indices()[0]].remove(I_WITNESS_UTXO, I_NON_WITNESS_UTXO))


# ---------------------------------------------------------- fee output
def _need(ctx, n=1):
    return math.ceil(ctx.orig_rate * 68 * n)


@case("A20", "Fee output reduced by more than maxadditionalfeecontribution", "reject",
      "Fee output: subtracted amount <= maxadditionalfeecontribution", needs=("fee_output",))
def a20(ctx):
    ctx.honest()
    extra = ctx.max_contrib - ctx.contribution + 1000
    ctx.tx.vout[ctx.fee_index].value -= extra
    ctx.tx.vout[ctx.out_index(ctx.payment_script)].value += 0  # all of it goes to fee


@case("A21", "Fee output contribution redirected to the receiver instead of fees", "reject",
      "Fee output: actual contribution <= difference of absolute fee", needs=("fee_output",))
def a21(ctx):
    ctx.honest()
    # take the full contribution but pay the fee bump out of nothing: fee stays flat
    pi = ctx.out_index(ctx.payment_script)
    ctx.tx.vout[pi].value += ctx.contribution


@case("A22", "Fee output reduced although the receiver added no input", "reject",
      "Fee output: contribution only pays for fees incurred by additional inputs", needs=("fee_output",))
def a22(ctx):
    c = min(ctx.max_contrib, _need(ctx))
    ctx.tx.vout[ctx.fee_index].value -= c  # all of it to fees, but no input was added


@case("A23", "Fee output pays for more than the receiver's inputs justify", "reject",
      "Fee output: contribution <= originalFeeRate * vsize(sender_input_type) * added inputs",
      needs=("fee_output",))
def a23(ctx):
    ctx.honest()
    # take everything the sender allowed if that exceeds what one extra input costs
    allowed = math.floor(ctx.orig_rate * 68)
    if ctx.max_contrib <= allowed + 1:
        ctx.notes.append("sender's maxadditionalfeecontribution (%d) leaves no room above one input (%d): "
                         "case cannot violate the rule" % (ctx.max_contrib, allowed))
        ctx.expect = "accept"
        return
    extra = ctx.max_contrib - ctx.contribution
    ctx.tx.vout[ctx.fee_index].value -= extra  # extra goes to miners


@case("A24", "Sender's change output removed", "reject",
      "Verify that all sender's outputs (except the payment) are in the proposal", needs=("change",))
def a24(ctx):
    ctx.honest()
    ci = ctx.change_index()
    gone = ctx.tx.vout.pop(ci)
    ctx.tx.vout[ctx.out_index(ctx.payment_script)].value += gone.value


@case("A25", "Sender's change redirected to a receiver script (same value)", "reject",
      "Verify that all sender's outputs (except the payment) are in the proposal", needs=("change",))
def a25(ctx):
    ctx.honest()
    ctx.tx.vout[ctx.change_index()].script = fresh_script()


@case("A26", "Sender's change decreased with no fee output allowed", "reject",
      "Else: make sure the output's value did not decrease", needs=("change",))
def a26(ctx):
    ctx.honest()
    ci = ctx.change_index()
    ctx.tx.vout[ci].value -= 1000
    ctx.tx.vout[ctx.out_index(ctx.payment_script)].value += 1000
    if ctx.fee_index == ci:
        ctx.notes.append("change is the fee output, so this overlaps A21")


# ---------------------------------------------------------- payment output
@case("A30", "pjos=0 but payment output substituted", "reject",
      "If the receiver's BIP21 signalled pjos=0, disable payment output substitution", pjos_off=True)
def a30(ctx):
    ctx.honest()
    ctx.tx.vout[ctx.out_index(ctx.payment_script)].script = fresh_script()


@case("A31", "pjos=0 but payment output value decreased", "reject",
      "Payment output substitution disabled: the receiver's outputs must not be decreased", pjos_off=True)
def a31(ctx):
    pi = ctx.out_index(ctx.payment_script)
    ctx.tx.vout[pi].value -= 5000
    ctx.tx.vout.append(TxOut(5000 - 200, fresh_script()))  # keeps fee higher


@case("A32", "Fee rate below the sender's minfeerate", "reject",
      "If minfeerate was specified, check the fee rate of the payjoin transaction is not less")
def a32(ctx):
    # receiver adds an input but pays no fee bump at all
    value = ctx.add_receiver_input()
    ctx.tx.vout[ctx.out_index(ctx.payment_script)].value += value
    new_rate = ctx.orig_fee / (ctx.orig_vsize + 68)
    if ctx.minfeerate is None or new_rate >= ctx.minfeerate:
        ctx.notes.append("sender minfeerate=%s, proposal rate %.2f: no violation, accept is correct"
                         % (ctx.minfeerate, new_rate))
        ctx.expect = "accept"


# ---------------------------------------------------------- utxo honesty
@case("A42", "Receiver adds another of the sender's own UTXOs, unfinalized", "reject",
      "The sender must be careful to only sign the inputs that were present in the original PSBT",
      needs=("spare",))
def a42(ctx):
    ctx.honest()
    spare = ctx.r.spare_utxo
    from .psbt import TxIn
    txin = TxIn(bytes.fromhex(spare["txid"])[::-1], spare["vout"], b"", ctx.orig_tx.vin[0].sequence)
    ctx.tx.vin.append(txin)
    ctx.utxos[txin.outpoint] = TxOut(spare["value"], bytes.fromhex(spare["script"]))
    ctx.tx.vout[ctx.out_index(ctx.payment_script)].value += spare["value"] - _need(ctx)
    p = ctx.sign()
    p.inputs[len(ctx.tx.vin) - 1].set(I_WITNESS_UTXO, ctx.utxos[txin.outpoint].serialize())


# ---------------------------------------------------------- transport
@case("T01", "Receiver never answers", "reject",
      "Sender must not hang: the original is broadcast after a timeout", timeout=150)
def t01(ctx):
    time.sleep(140)
    return (503, "")


@case("T02", "Receiver answers 200 with garbage", "reject", "Sender must fall back to the original")
def t02(ctx):
    return (200, "not a psbt")


@case("T03", "Receiver error with unknown code and a phishing message", "reject",
      "Unknown error codes/messages must not reach the UI (only the fallback happens)")
def t03(ctx):
    import json
    return (400, json.dumps({"errorCode": "send-to-bc1qattacker",
                             "message": "Payjoin failed. Resend the payment to bc1qattacker to complete it."}))


@case("T04", "pj endpoint is plain http on a non-local host", "reject",
      "Senders SHOULD NOT accept a URL representing an unencrypted or unauthenticated connection",
      transport="http-remote")
def t04(ctx):
    ctx.honest()


# Cases for findings still under coordinated disclosure live in cases_embargoed.py, which is
# kept out of the public repository until the affected projects have shipped fixes.
try:
    from . import cases_embargoed  # noqa: F401,E402
except ImportError:
    pass


def by_id(cid):
    return next(c for c in CASES if c.id == cid)
