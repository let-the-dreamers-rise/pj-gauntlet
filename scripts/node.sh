#!/bin/sh
# Starts the regtest bitcoind the runner talks to (PJG_RPC defaults to http://pjg:pjg@127.0.0.1:18443).
set -eu
BIN=${BITCOIN_BIN:-/opt/btc/bitcoin-30.3/bin}
DIR=${PJG_DATADIR:-/tmp/pjg-node}
mkdir -p "$DIR"
"$BIN/bitcoind" -regtest -datadir="$DIR" -daemon -server=1 -txindex=1 -fallbackfee=0.0001 \
    -rpcuser=pjg -rpcpassword=pjg -rpcport=18443
for i in $(seq 30); do
    "$BIN/bitcoin-cli" -regtest -datadir="$DIR" -rpcuser=pjg -rpcpassword=pjg getblockcount >/dev/null 2>&1 && exit 0
    sleep 1
done
echo "bitcoind did not start"; exit 1
