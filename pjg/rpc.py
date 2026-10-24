"""Tiny bitcoind JSON-RPC client (stdlib only)."""
import base64
import json
import os
import urllib.request


class RPCError(Exception):
    def __init__(self, err):
        super().__init__(err.get("message"))
        self.code = err.get("code")


class RPC:
    def __init__(self, url=None, wallet=None):
        self.url = url or os.environ.get("PJG_RPC", "http://pjg:pjg@127.0.0.1:18443")
        self.wallet = wallet
        rest = self.url.split("://", 1)[1]
        creds, self.hostport = rest.split("@", 1)
        self.auth = base64.b64encode(creds.encode()).decode()

    def w(self, name):
        return RPC(self.url, name)

    def call(self, method, *params):
        path = "/wallet/" + self.wallet if self.wallet else "/"
        req = urllib.request.Request(
            "http://" + self.hostport + path,
            data=json.dumps({"jsonrpc": "1.0", "id": "pjg", "method": method, "params": list(params)}).encode(),
            headers={"Authorization": "Basic " + self.auth, "Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=120) as r:
                body = json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = json.loads(e.read())
        if body.get("error"):
            raise RPCError(body["error"])
        return body["result"]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *p: self.call(name, *p)
