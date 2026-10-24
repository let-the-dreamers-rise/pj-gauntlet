| case | expected | pdk | bluewallet | btcpay | sparrow | joinmarket |
|---|---|---|---|---|---|---|
| C01 Honest payjoin: one receiver input, fee bump from the fee output | accept | pass | pass | pass | pass | pass |
| C02 Receiver input inserted at index 0 | accept | pass | pass | pass | pass | pass |
| C03 Payment output substituted (no pjos=0) | accept | pass | pass | pass | pass | pass |
| C04 Receiver adds no input, only swaps its payment script | accept | pass | pass | pass | pass | pass |
| C05 Receiver batches an extra output paid from its own input | accept | pass | pass | pass | pass | pass |
| C06 Receiver input is taproot while sender is p2wpkh (mixed types) | accept | pass | **FAIL** | pass | **FAIL** | **FAIL** |
| C07 Receiver takes exactly the advertised maxadditionalfeecontribution for one input | info | info | info | info | info | info |
| A01 Absolute fee decreased (receiver pockets 500 sat of the sender's fee) | reject | pass | pass | pass | pass | pass |
| A02 Transaction version changed | reject | pass | pass | pass | pass | pass |
| A03 nLockTime changed | reject | pass | pass | pass | pass | pass |
| A04 Sequence of every input changed (still uniform) | reject | pass | pass | pass | pass | pass |
| A05 Receiver input uses a different sequence (mixed sequences) | reject | pass | pass | pass | pass | pass |
| A06 One of the sender's inputs dropped | reject | pass | pass | pass | pass | pass |
| A07 Sender inputs swapped (proposal shuffles the sender's inputs) | info | info | info | info | info | info |
| A08 Sender's outputs swapped (proposal shuffles outputs) | info | info | info | info | info | info |
| A10 Receiver finalized one of the sender's inputs | reject | pass | pass | pass | pass | pass |
| A11 Partial signature added to a sender input | reject | pass | pass | pass | pass | pass |
| A12 BIP32 keypath added to an input | reject | pass | pass | pass | pass | **FAIL** |
| A13 BIP32 keypath added to an output | reject | pass | pass | pass | pass | **FAIL** |
| A14 Global xpub included | reject | **FAIL** | pass | pass | pass | pass |
| A15 Receiver input left unfinalized | reject | pass | pass | pass | pass | pass |
| A16 Receiver input without witness_utxo/non_witness_utxo | reject | pass | pass | pass | pass | pass |
| A20 Fee output reduced by more than maxadditionalfeecontribution | reject | pass | pass | pass | pass | pass |
| A21 Fee output contribution redirected to the receiver instead of fees | reject | pass | pass | pass | pass | pass |
| A22 Fee output reduced although the receiver added no input | reject | pass | pass | pass | pass | **FAIL** |
| A23 Fee output pays for more than the receiver's inputs justify | reject | pass | pass | **FAIL** | pass | **FAIL** |
| A24 Sender's change output removed | reject | pass | pass | pass | pass | pass |
| A25 Sender's change redirected to a receiver script (same value) | reject | pass | pass | pass | pass | pass |
| A26 Sender's change decreased with no fee output allowed | reject | pass | pass | pass | pass | pass |
| A30 pjos=0 but payment output substituted | reject | pass | pass | pass | pass | pass |
| A31 pjos=0 but payment output value decreased | reject | pass | pass | pass | pass | pass |
| A32 Fee rate below the sender's minfeerate | reject | pass | pass | pass | pass | pass |
| A42 Receiver adds another of the sender's own UTXOs, unfinalized | reject | pass | pass | pass | pass | pass |
| T01 Receiver never answers | reject |  |  |  |  |  |
| T02 Receiver answers 200 with garbage | reject | pass | pass | pass | pass | pass |
| T03 Receiver error with unknown code and a phishing message | reject | pass | pass | pass | pass | pass |
| T04 pj endpoint is plain http on a non-local host | reject | pass | **FAIL** | **FAIL** | pass | **FAIL** |

- pdk: 36 cases, 32 pass, 1 fail
- bluewallet: 36 cases, 31 pass, 2 fail
- btcpay: 36 cases, 31 pass, 2 fail
- sparrow: 36 cases, 32 pass, 1 fail
- joinmarket: 36 cases, 27 pass, 6 fail
