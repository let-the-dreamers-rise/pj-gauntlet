# Findings

First full run: 24 October 2026, Bitcoin Core 30.3 regtest, commits listed in the README. Every
row below links the case that shows it; `results/<sender>.json` has the transaction ids, the
sender's error message and the real fee for each run.

One more issue, found by two embargoed cases, affects all five senders and is being disclosed
privately to the projects. It is not described here until the embargo ends.

## JoinMarket

1. **Rejected proposals crash the fallback path.** `process_payjoin_proposal_from_server` calls
   `fallback_nonpayjoin_broadcast(manager, err=b"...")` in two places, but the function's signature
   is `(err, manager)`, so Python raises `TypeError: got multiple values for argument 'err'` and the
   original payment is not broadcast. It only goes out when the separate 60-second timeout fires.
   Every rejection in our runs took about 68 s, against about 9 s for a completed payjoin. The line that fires on every failed
   check is `fallback_nonpayjoin_broadcast(manager, err=b"Receiver PSBT checks failed.")`
   (src/jmclient/payjoin.py). Fix: pass the arguments in order. Severity: low (the timeout saves
   the payment), but it hides the reason for every rejection and delays every fallback.
2. **Keypaths are not checked** (A12, A13). BIP-78: "Verify that no keypaths are in the PSBT input"
   and the same for outputs. JoinMarket signed proposals with BIP32 derivations on an input and an
   output. Severity: low (a receiver can't steal with it), conformance.
3. **Fee contribution accepted without an added input** (A22), and **above the per-input bound**
   (A23). BIP-78 caps the contribution at `originalPSBTFeeRate * vsize(sender_input_type) * added_inputs`.
   JoinMarket advertises `maxadditionalfeecontribution = 1.2 * rate * vsize` and does not check the
   per-input bound, so a receiver can make the sender pay up to that cap in extra fee while adding
   nothing. The money goes to miners, not the receiver. Severity: low.
4. **Taproot receiver inputs are rejected** (C06): `receiver input does not match our script type.`
   Mixed input types became allowed in BIP-78 in September 2024 (after a grace period), so JoinMarket
   cannot payjoin with receivers whose wallets hold taproot coins. Interop.
5. **Plain-http endpoints are accepted** (T04). BIP-78: senders "SHOULD NOT accept a URL
   representing an unencrypted or unauthenticated connection" unless it is `.onion`. JoinMarket
   posted the original PSBT to `http://pjg.test/...`. Severity: low to medium (the original PSBT
   leaks to the network path, and a network attacker can answer as the receiver).

## BTCPay Server (BTCPayServer.BIP78.Sender 0.2.5)

1. **Contribution above the per-input bound accepted** (A23). The bound uses a fixed 110 vB per added
   input ("This assumes an additional input can be up to 110 bytes") instead of
   `originalFeeRate * vsize(sender_input_type)`, 68 vB for p2wpkh. Extra fee goes to miners.
   Severity: low.
2. **Plain-http endpoints are accepted** (T04). Same rule as above: the library posted to a clearnet
   http endpoint and signed the proposal.
3. Source note (not exercised by a case yet): `ApplyOptionalParameters` drops any query string
   already on the pj URL.

## BlueWallet (after the PR #9015 rewrite)

1. **Taproot receiver inputs are rejected** (C06): `Mixed input type detected in the proposal`.
   This check was added during the rewrite. It follows the pre-2024 rule; current BIP-78 allows mixed
   input types. Interop.
2. **Plain-http endpoints are accepted** (T04). Raised in the PR #9015 review and not adopted;
   the harness confirms the sender posts to and signs proposals from a clearnet http endpoint.
3. Shuffled sender inputs (A07, recorded only): BlueWallet signs each input with the UTXO data of
   the input that used to be at that index, so the signatures are invalid, the broadcast fails and it
   falls back. Safe, but it should detect the reorder and fall back before signing.

## Sparrow

1. **Cannot payjoin with a taproot receiver input** (C06): `PSBTParseException: Not all spent UTXOs
   are provided`. drongo verifies every signature while parsing the proposal, and a taproot
   signature commits to every input's amount, so verification needs the sender's own UTXO data.
   BIP-78 tells receivers to "only fill the witnessUTXO or nonWitnessUTXO for the additional
   inputs", so a conforming proposal never has it. Sparrow needs to merge its own UTXO data into the
   proposal before verifying. Interop.

Everything else passed, including the https/.onion requirement (T04).

## PDK (rust-payjoin, payjoin-cli v1 sender)

1. Global xpubs (A14) are removed from the proposal instead of rejected. Harmless; recorded for the
   matrix only.
2. PDK bounds the fee contribution by the receiver input's own weight, while BIP-78 uses
   `vsize(sender_input_type)`. A receiver that contributes for a p2wpkh-sized input while adding a
   (smaller) taproot input is valid under the BIP and rejected by PDK. Spec ambiguity worth settling
   in the BIP text; our honest taproot case uses the stricter reading so every sender can accept it.

Everything else passed, including the https/.onion requirement (T04).

## What we would report where

| finding | where | status |
|---|---|---|
| JoinMarket fallback argument order | public PR to JoinMarket-Org/joinmarket-clientserver | drafted |
| JoinMarket keypaths, contribution bounds, mixed inputs, http | one public issue | drafted |
| BTCPay BIP78.Sender per-input bound, http | public issue on btcpayserver/BTCPayServer.BIP78 | drafted |
| BlueWallet mixed inputs, http, shuffled inputs | public issue on BlueWallet/BlueWallet | drafted |
| Sparrow taproot receiver input | public issue on sparrowwallet/sparrow | drafted |
| BIP-78 text: contribution bound with mixed input types | bitcoin/bips discussion | after the embargo |
| embargoed issue (all senders) | private reports to each project | waiting to send |
