#!/bin/sh
# Installs everything pj-gauntlet needs on a fresh Ubuntu 24.04 box (or a GitHub Actions runner)
# and builds the five sender adapters against pinned upstream commits.
#
#   ./setup.sh            # all adapters
#   ./setup.sh pdk btcpay # only some
#
# Toolchains go to /opt (Bitcoin Core, .NET 8, JDK 25, JoinMarket's venv and libsecp256k1).
# Upstream sources are cloned to ./upstream at the commits recorded below.
set -eu
cd "$(dirname "$0")"
ROOT=$(pwd)
SUDO=""; [ "$(id -u)" = 0 ] || SUDO=sudo
ADAPTERS=${*:-"bluewallet joinmarket btcpay sparrow pdk"}

BITCOIN_VERSION=30.3
BLUEWALLET_COMMIT=9ab299f32770e51d7839ff9bd5f25f97bba68053
JOINMARKET_COMMIT=c2dd2534afa59ddcb7f7d9a67e4b57cefcf9e4a4
SPARROW_COMMIT=b9d3bbbf64600de14dab9f18850362bec7f87af9
DRONGO_COMMIT=d31b0249542d4616187e01466a81f98f1d70c935
RUST_PAYJOIN_COMMIT=19294430ed3a34829cebe50d3bff0473e0dcceac

mkdir -p upstream
clone() { # repo dir commit
    [ -d "upstream/$2/.git" ] || git clone -q "https://github.com/$1" "upstream/$2"
    git -C "upstream/$2" fetch -q --depth 1 origin "$3" 2>/dev/null || true
    git -C "upstream/$2" checkout -q "$3"
}

# Bitcoin Core (regtest node for every run)
if [ ! -x /opt/btc/bitcoin-$BITCOIN_VERSION/bin/bitcoind ]; then
    $SUDO mkdir -p /opt/btc && $SUDO chown "$(id -u)" /opt/btc
    (cd /opt/btc && curl -sSfO https://bitcoincore.org/bin/bitcoin-core-$BITCOIN_VERSION/bitcoin-$BITCOIN_VERSION-x86_64-linux-gnu.tar.gz \
        && curl -sSfO https://bitcoincore.org/bin/bitcoin-core-$BITCOIN_VERSION/SHA256SUMS \
        && sha256sum --ignore-missing -c SHA256SUMS && tar xzf bitcoin-$BITCOIN_VERSION-x86_64-linux-gnu.tar.gz)
fi

# The T04 case needs a non-local hostname that resolves to the harness
grep -q "pjg.test" /etc/hosts || echo "127.0.0.1 pjg.test" | $SUDO tee -a /etc/hosts >/dev/null

for a in $ADAPTERS; do
    echo "== $a"
    case $a in
    bluewallet)
        # The vendored payjoin-client in adapters/bluewallet/vendor is BlueWallet's, unmodified.
        clone BlueWallet/BlueWallet bluewallet $BLUEWALLET_COMMIT
        for f in payjoin-client uint8array-extras; do
            diff -r "upstream/bluewallet/blue_modules/$f" "adapters/bluewallet/vendor/$f" >/dev/null || { echo "vendor/$f differs from BlueWallet"; exit 1; }
        done
        (cd adapters/bluewallet && npm ci --silent)
        ;;
    joinmarket)
        clone JoinMarket-Org/joinmarket-clientserver joinmarket $JOINMARKET_COMMIT
        if [ ! -e /opt/jm-secp/lib/libsecp256k1.so ]; then
            $SUDO apt-get install -y -qq autoconf libtool build-essential >/dev/null
            rm -rf /tmp/secp && git clone -q --depth 1 --branch v0.5.0 https://github.com/bitcoin-core/secp256k1 /tmp/secp
            (cd /tmp/secp && ./autogen.sh >/dev/null && ./configure -q --prefix=/opt/jm-secp --enable-module-recovery \
                --enable-experimental --enable-module-ecdh --enable-benchmark=no && make -s -j"$(nproc)" && $SUDO make -s install)
        fi
        [ -x /opt/jmvenv/bin/python ] || { $SUDO python3 -m venv /opt/jmvenv; $SUDO chown -R "$(id -u)" /opt/jmvenv; }
        /opt/jmvenv/bin/pip install -q -e "upstream/joinmarket[services]"
        ln -sfn "$ROOT/upstream/joinmarket" adapters/joinmarket/joinmarket-clientserver
        ;;
    btcpay)
        if [ ! -x /opt/dotnet/dotnet ]; then
            curl -sSfL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh
            $SUDO bash /tmp/dotnet-install.sh --channel 8.0 --install-dir /opt/dotnet >/dev/null
        fi
        (cd adapters/btcpay && DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 /opt/dotnet/dotnet build -c Release -o out -v q)
        ;;
    sparrow)
        if [ ! -x /opt/jdk25/bin/java ]; then
            $SUDO mkdir -p /opt/jdk25
            curl -sSfL https://corretto.aws/downloads/latest/amazon-corretto-25-x64-linux-jdk.tar.gz | $SUDO tar xz -C /opt/jdk25 --strip-components=1
        fi
        clone sparrowwallet/sparrow sparrow $SPARROW_COMMIT
        clone sparrowwallet/drongo drongo $DRONGO_COMMIT
        diff -r upstream/sparrow/src/main/java/com/sparrowwallet/sparrow/payjoin \
            adapters/sparrow/vendor/com/sparrowwallet/sparrow/payjoin >/dev/null || { echo "vendor/payjoin differs from Sparrow"; exit 1; }
        export JAVA_HOME=/opt/jdk25 PATH=/opt/jdk25/bin:$PATH
        mkdir -p adapters/sparrow/libs
        # drongo from source, with Sparrow's gradle wrapper (verification metadata dropped: we pin by commit)
        cp -r adapters/sparrow/gradle adapters/sparrow/gradlew upstream/drongo/
        (cd upstream/drongo && ./gradlew jar -q --no-daemon && cp build/libs/drongo-*.jar "$ROOT/adapters/sparrow/libs/drongo.jar")
        M=https://maven-central.storage-download.googleapis.com/maven2
        curl -sSf -o adapters/sparrow/libs/tern-1.0.6.jar $M/com/sparrowwallet/tern/1.0.6/tern-1.0.6.jar || \
            curl -sSf -o adapters/sparrow/libs/tern-1.0.6.jar https://repo.maven.apache.org/maven2/com/sparrowwallet/tern/1.0.6/tern-1.0.6.jar
        for m in base graphics; do
            curl -sSf -o adapters/sparrow/libs/javafx-$m-25-linux.jar $M/org/openjfx/javafx-$m/25/javafx-$m-25-linux.jar
        done
        (cd adapters/sparrow && ./gradlew installDist -q --no-daemon)
        ;;
    pdk)
        command -v cargo >/dev/null || { curl -sSf https://sh.rustup.rs | sh -s -- -y -q; . "$HOME/.cargo/env"; }
        clone payjoin/rust-payjoin rust-payjoin $RUST_PAYJOIN_COMMIT
        (cd upstream/rust-payjoin && cp Cargo-recent.lock Cargo.lock \
            && cargo build -q --release -p payjoin-cli --no-default-features --features v1,_manual-tls --locked)
        ln -sfn "$ROOT/upstream/rust-payjoin/target/release/payjoin-cli" adapters/pdk/payjoin-cli
        ;;
    *) echo "unknown adapter $a"; exit 1 ;;
    esac
done
echo "setup done"
