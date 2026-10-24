#!/bin/sh
# Full matrix. Senders that refuse plain-http pj URLs (as BIP-78 asks) run with --tls.
# T01 (receiver never answers) takes 150 s per sender; add it back with --skip "".
cd "$(dirname "$0")"
python3 -m pjg.runner --adapter adapters/pdk/run.sh --tls --skip T01 --out results/pdk.json --name pdk
python3 -m pjg.runner --adapter adapters/bluewallet/run.sh --skip T01 --out results/bluewallet.json --name bluewallet
python3 -m pjg.runner --adapter adapters/btcpay/run.sh --skip T01 --out results/btcpay.json --name btcpay
python3 -m pjg.runner --adapter adapters/sparrow/run.sh --tls --skip T01 --out results/sparrow.json --name sparrow
python3 -m pjg.runner --adapter adapters/joinmarket/run.sh --skip T01 --out results/joinmarket.json --name joinmarket
python3 -m pjg.table results/pdk.json results/bluewallet.json results/btcpay.json results/sparrow.json results/joinmarket.json > RESULTS.md
