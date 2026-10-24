#!/bin/sh
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
exec /opt/dotnet/dotnet "$(dirname "$0")/out/btcpay-adapter.dll"
