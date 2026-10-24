"""Throwaway regtest keys: secp256k1 point math, WIF, bech32/bech32m addresses.

Only used to hand each sender adapter fresh keys it can import. Not constant
time and not for real funds.
"""
import hashlib
import secrets

P = 2**256 - 2**32 - 977
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def _add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and (a[1] + b[1]) % P == 0:
        return None
    if a == b:
        lam = 3 * a[0] * a[0] * pow(2 * a[1], P - 2, P) % P
    else:
        lam = (b[1] - a[1]) * pow(b[0] - a[0], P - 2, P) % P
    x = (lam * lam - a[0] - b[0]) % P
    return (x, (lam * (a[0] - x) - a[1]) % P)


def point_mul(k, pt=G):
    r = None
    while k:
        if k & 1:
            r = _add(r, pt)
        pt = _add(pt, pt)
        k >>= 1
    return r


def hash160(b):
    return hashlib.new("ripemd160", hashlib.sha256(b).digest()).digest()


B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58check(b):
    b = b + hashlib.sha256(hashlib.sha256(b).digest()).digest()[:4]
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values):
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((b >> i) & 1) else 0
    return chk


def _convertbits(data, frm, to):
    acc = bits = 0
    ret = []
    for v in data:
        acc = (acc << frm) | v
        bits += frm
        while bits >= to:
            bits -= to
            ret.append((acc >> bits) & ((1 << to) - 1))
    if bits:
        ret.append((acc << (to - bits)) & ((1 << to) - 1))
    return ret


def segwit_address(hrp, witver, prog):
    data = [witver] + _convertbits(prog, 8, 5)
    const = 0x2BC830A3 if witver else 1
    hrp_exp = [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]
    pm = _polymod(hrp_exp + data + [0] * 6) ^ const
    chk = [(pm >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(CHARSET[d] for d in data + chk)


class Key:
    def __init__(self, secret=None):
        self.secret = secret or (secrets.randbelow(N - 1) + 1)
        x, y = point_mul(self.secret)
        self.pub = bytes([2 + (y & 1)]) + x.to_bytes(32, "big")

    def wif(self, testnet=True):
        return b58check(bytes([0xEF if testnet else 0x80]) + self.secret.to_bytes(32, "big") + b"\x01")

    def p2wpkh_script(self):
        return b"\x00\x14" + hash160(self.pub)

    def p2wpkh_address(self, hrp="bcrt"):
        return segwit_address(hrp, 0, hash160(self.pub))

    def p2sh_p2wpkh_script(self):
        return b"\xa9\x14" + hash160(b"\x00\x14" + hash160(self.pub)) + b"\x87"
