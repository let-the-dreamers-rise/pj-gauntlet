# pj-gauntlet

A hostile payjoin receiver that tests real wallet senders.

Payjoin (BIP-78, and BIP-77 "async payjoin") breaks the common-input-ownership heuristic that chain
surveillance depends on. It only works if the sender's wallet checks every proposal it gets back:
the receiver adds inputs and edits outputs, and the BIP-78 sender checklist is what stops a
malicious receiver from taking money, lowering the fee, or slipping in the sender's own coins.
Each wallet implements that checklist by itself (Rust, JavaScript, C#, Python, Java), and until now
nothing tested those implementations against each other or against a receiver that actually cheats.

pj-gauntlet runs a regtest Bitcoin Core node and a receiver that answers each payjoin request with
one spec violation (or one honest control). A small adapter drives the real sender code of each
wallet: the wallet builds and signs its original transaction, its own payjoin code talks to the
receiver over HTTP(S), and it decides to sign the proposal or fall back. The oracle never trusts
the adapter's report: it reads the mempool to see which transaction was actually broadcast, and
computes what the payment really cost.

## Senders tested

| sender | code under test | how it is driven |
|---|---|---|
| **PDK** (rust-payjoin, used by Bull Bitcoin, Cake Wallet, BDK-CLI) | `payjoin-cli` v1 sender, rust-payjoin `19294430` | CLI against a Bitcoin Core wallet |
| **BlueWallet** | `blue_modules/payjoin-client`, vendored unmodified from `9ab299f3` (after the PR #9015 rewrite) | TypeScript adapter mirroring `PayjoinTransaction` and the send screen |
| **BTCPay Server** | `BTCPayServer.BIP78.Sender` 0.2.5 (the NuGet package BTCPay ships) | C# adapter calling it the way `UIWalletsController.PSBT.cs` does |
| **Sparrow** | `com.sparrowwallet.sparrow.payjoin.Payjoin`, unmodified, on drongo built from source | Java adapter following `HeadersController` |
| **JoinMarket** | `jmclient.payjoin`, unmodified, `c2dd2534` | Python adapter running the `sendpayment.py` path in-process |

Wasabi removed its payjoin sender, so it is not in the matrix.

## Cases

37 public cases, each tied to a sentence of BIP-78 (`pjg/cases.py`; the rule is stored with every result).

- **Controls (C01-C07):** proposals a conforming sender must accept: one receiver input, receiver
  input first, output substitution, script swap without inputs, receiver batching an output, a
  taproot receiver input (mixed input types are allowed since the September 2024 amendment), and the
  full advertised fee contribution (recorded only).
- **Transaction (A01-A08):** lower absolute fee, changed version, locktime, sequences, mixed
  sequences, dropped sender input; shuffled inputs and outputs are recorded only.
- **PSBT fields (A10-A16):** sender input finalized, partial signature added, keypaths on inputs and
  outputs, global xpubs, receiver input left unfinalized or without UTXO data.
- **Fee output (A20-A26):** contribution above `maxadditionalfeecontribution`, redirected to the
  receiver, taken without adding an input, above the per-input bound; change removed, swapped or cut.
- **Payment output (A30-A32):** `pjos=0` with substitution or a lower value, fee rate below `minfeerate`.
- **UTXO honesty (A42, plus two embargoed cases):** the receiver adds another of the sender's own
  coins and hopes it gets signed.
- **Transport (T01-T04):** no answer, garbage, an error carrying a phishing message, and a plain-http
  endpoint on a non-local host.

Two further cases are held back while a cross-wallet issue they found is under coordinated
disclosure with the affected projects. They will be published here when the embargo ends.

## Results

See [RESULTS.md](RESULTS.md) for the full matrix and [FINDINGS.md](FINDINGS.md) for what each
failure means and where it was reported.

## Running it

```
./setup.sh                 # Bitcoin Core 30.3, toolchains, pinned upstream sources, adapter builds
./scripts/node.sh          # regtest bitcoind on 127.0.0.1:18443
python3 -m pjg.runner --adapter adapters/pdk/run.sh --tls --out results/pdk.json --name pdk
python3 -m pjg.table results/*.json > RESULTS.md
```

`--tls` serves the endpoint over HTTPS with the test CA in `tls/` (Sparrow and PDK refuse plain
http, as BIP-78 asks). `--cases` and `--skip` pick cases. CI runs the full matrix on every push and
weekly against the pinned commits (`.github/workflows/matrix.yml`).

### Adding a wallet

An adapter is any executable. It reads one job on stdin:

```
{"rpc": "http://pjg:pjg@127.0.0.1:18443", "bip21": "bitcoin:bcrt1...?amount=1&pj=https://...",
 "amount_sat": 100000000, "feerate": 10, "utxos": [...], "change": {...}, "spare_file": "/tmp/..."}
```

It funds or imports its wallet, builds and signs the original with the wallet's own code, runs the
wallet's payjoin sender, broadcasts whatever the wallet would broadcast, and prints one line:
`{"result": "payjoin" | "fallback" | "error", "txid": "...", "error": "..."}`. Wallets that pick
their own coins write a spare UTXO to `spare_file` so case A42 can try to sneak it in.

## Disclosure

Anything that can cost users money goes privately to the project first (security contacts from each
project's SECURITY.md), with a 90-day embargo or until a fix ships. Spec-level issues also get a
BIP PR with test vectors afterwards.

## License

MIT. Vendored upstream code keeps its own license (BlueWallet MIT, Sparrow Apache-2.0).
