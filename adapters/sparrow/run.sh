#!/bin/sh
# Sparrow refuses non-https payjoin URLs (BIP-78), so run with `runner --tls`; trust the test CA.
D="$(cd "$(dirname "$0")" && pwd)"
export JAVA_HOME=/opt/jdk25
export JAVA_OPTS="-Djavax.net.ssl.trustStore=$D/../../tls/truststore.jks -Djavax.net.ssl.trustStorePassword=changeit"
exec "$D/build/install/pjg-sparrow/bin/pjg-sparrow"
