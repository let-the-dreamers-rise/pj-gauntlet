#!/bin/sh
export LD_LIBRARY_PATH=/opt/jm-secp/lib
exec /opt/jmvenv/bin/python "$(dirname "$0")/adapter.py"
