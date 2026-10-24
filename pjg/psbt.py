"""Minimal PSBT (BIP-174, version 0) codec that keeps every key-value pair.

The harness needs to produce PSBTs that are deliberately wrong, so this codec
never validates: it parses into ordered (key, value) lists and writes them back
byte-for-byte, with helpers for the fields BIP-78 talks about.
"""
import base64
import hashlib
import io
import struct

PSBT_MAGIC = b"psbt\xff"

# global
G_UNSIGNED_TX = 0x00
G_XPUB = 0x01
# input
I_NON_WITNESS_UTXO = 0x00
I_WITNESS_UTXO = 0x01
I_PARTIAL_SIG = 0x02
I_SIGHASH = 0x03
I_REDEEM_SCRIPT = 0x04
I_WITNESS_SCRIPT = 0x05
I_BIP32 = 0x06
I_FINAL_SCRIPTSIG = 0x07
I_FINAL_SCRIPTWITNESS = 0x08
I_TAP_KEY_SIG = 0x13
I_TAP_BIP32 = 0x16
I_TAP_INTERNAL_KEY = 0x17
# output
O_REDEEM_SCRIPT = 0x00
O_WITNESS_SCRIPT = 0x01
O_BIP32 = 0x02


def sha256d(b):
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def read_varint(s):
    n = s.read(1)[0]
    if n < 0xFD:
        return n
    if n == 0xFD:
        return struct.unpack("<H", s.read(2))[0]
    if n == 0xFE:
        return struct.unpack("<I", s.read(4))[0]
    return struct.unpack("<Q", s.read(8))[0]


def varint(n):
    if n < 0xFD:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def read_bytes(s):
    return s.read(read_varint(s))


def ser_bytes(b):
    return varint(len(b)) + b


class TxIn:
    def __init__(self, txid, vout, script_sig=b"", sequence=0xFFFFFFFD):
        self.txid = txid  # internal byte order (as serialized)
        self.vout = vout
        self.script_sig = script_sig
        self.sequence = sequence

    @property
    def outpoint(self):
        return (self.txid, self.vout)

    def txid_hex(self):
        return self.txid[::-1].hex()


class TxOut:
    def __init__(self, value, script):
        self.value = value
        self.script = script

    def serialize(self):
        return struct.pack("<q", self.value) + ser_bytes(self.script)

    @classmethod
    def parse(cls, b):
        s = io.BytesIO(b)
        value = struct.unpack("<q", s.read(8))[0]
        return cls(value, read_bytes(s))


class Tx:
    def __init__(self, version=2, vin=None, vout=None, locktime=0, witnesses=None):
        self.version = version
        self.vin = vin or []
        self.vout = vout or []
        self.locktime = locktime
        self.witnesses = witnesses  # list of list of bytes, or None

    @classmethod
    def parse(cls, b):
        s = io.BytesIO(b)
        version = struct.unpack("<i", s.read(4))[0]
        n = read_varint(s)
        segwit = False
        if n == 0:
            flag = s.read(1)
            assert flag == b"\x01"
            segwit = True
            n = read_varint(s)
        vin = []
        for _ in range(n):
            txid = s.read(32)
            vout = struct.unpack("<I", s.read(4))[0]
            ss = read_bytes(s)
            seq = struct.unpack("<I", s.read(4))[0]
            vin.append(TxIn(txid, vout, ss, seq))
        vout = []
        for _ in range(read_varint(s)):
            value = struct.unpack("<q", s.read(8))[0]
            vout.append(TxOut(value, read_bytes(s)))
        witnesses = None
        if segwit:
            witnesses = []
            for _ in vin:
                witnesses.append([read_bytes(s) for _ in range(read_varint(s))])
        locktime = struct.unpack("<I", s.read(4))[0]
        return cls(version, vin, vout, locktime, witnesses)

    def serialize(self, with_witness=True):
        segwit = with_witness and self.witnesses and any(len(w) for w in self.witnesses)
        out = struct.pack("<i", self.version)
        if segwit:
            out += b"\x00\x01"
        out += varint(len(self.vin))
        for i in self.vin:
            out += i.txid + struct.pack("<I", i.vout) + ser_bytes(i.script_sig) + struct.pack("<I", i.sequence)
        out += varint(len(self.vout))
        for o in self.vout:
            out += o.serialize()
        if segwit:
            for w in self.witnesses:
                out += varint(len(w)) + b"".join(ser_bytes(x) for x in w)
        out += struct.pack("<I", self.locktime)
        return out

    def txid(self):
        return sha256d(self.serialize(with_witness=False))[::-1].hex()

    def copy(self):
        return Tx.parse(self.serialize())


class KVMap:
    """Ordered list of (key bytes, value bytes) for one PSBT map."""

    def __init__(self, pairs=None):
        self.pairs = list(pairs or [])

    def get(self, ktype):
        for k, v in self.pairs:
            if k[0] == ktype:
                return v
        return None

    def get_all(self, ktype):
        return [(k, v) for k, v in self.pairs if k[0] == ktype]

    def has(self, ktype):
        return any(k[0] == ktype for k, _ in self.pairs)

    def set(self, ktype, value, keydata=b""):
        key = bytes([ktype]) + keydata
        for idx, (k, _) in enumerate(self.pairs):
            if k == key:
                self.pairs[idx] = (key, value)
                return
        self.pairs.append((key, value))

    def add(self, ktype, value, keydata=b""):
        self.pairs.append((bytes([ktype]) + keydata, value))

    def remove(self, *ktypes):
        self.pairs = [(k, v) for k, v in self.pairs if k[0] not in ktypes]

    def serialize(self):
        return b"".join(ser_bytes(k) + ser_bytes(v) for k, v in self.pairs) + b"\x00"


def _read_map(s):
    pairs = []
    while True:
        klen = read_varint(s)
        if klen == 0:
            return KVMap(pairs)
        key = s.read(klen)
        pairs.append((key, read_bytes(s)))


class PSBT:
    def __init__(self, glob, inputs, outputs):
        self.glob = glob
        self.inputs = inputs
        self.outputs = outputs

    @classmethod
    def parse(cls, b):
        s = io.BytesIO(b)
        assert s.read(5) == PSBT_MAGIC, "bad magic"
        glob = _read_map(s)
        tx = Tx.parse(glob.get(G_UNSIGNED_TX))
        inputs = [_read_map(s) for _ in tx.vin]
        outputs = [_read_map(s) for _ in tx.vout]
        return cls(glob, inputs, outputs)

    @classmethod
    def from_b64(cls, s):
        return cls.parse(base64.b64decode(s))

    @classmethod
    def from_tx(cls, tx):
        t = tx.copy()
        for i in t.vin:
            i.script_sig = b""
        t.witnesses = None
        g = KVMap([(bytes([G_UNSIGNED_TX]), t.serialize(with_witness=False))])
        return cls(g, [KVMap() for _ in t.vin], [KVMap() for _ in t.vout])

    def serialize(self):
        return PSBT_MAGIC + self.glob.serialize() + b"".join(m.serialize() for m in self.inputs) + b"".join(
            m.serialize() for m in self.outputs)

    def b64(self):
        return base64.b64encode(self.serialize()).decode()

    @property
    def tx(self):
        return Tx.parse(self.glob.get(G_UNSIGNED_TX))

    @tx.setter
    def tx(self, t):
        self.glob.set(G_UNSIGNED_TX, t.serialize(with_witness=False))

    def input_utxo(self, i):
        """TxOut spent by input i, from witness_utxo or non_witness_utxo."""
        w = self.inputs[i].get(I_WITNESS_UTXO)
        if w is not None:
            return TxOut.parse(w)
        nw = self.inputs[i].get(I_NON_WITNESS_UTXO)
        if nw is not None:
            prev = Tx.parse(nw)
            return prev.vout[self.tx.vin[i].vout]
        return None

    def fee(self):
        ins = [self.input_utxo(i) for i in range(len(self.inputs))]
        if any(u is None for u in ins):
            return None
        return sum(u.value for u in ins) - sum(o.value for o in self.tx.vout)

    def is_finalized(self, i):
        return self.inputs[i].has(I_FINAL_SCRIPTSIG) or self.inputs[i].has(I_FINAL_SCRIPTWITNESS)

    def copy(self):
        return PSBT.parse(self.serialize())
