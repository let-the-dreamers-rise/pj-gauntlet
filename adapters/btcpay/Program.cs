// pj-gauntlet adapter for BTCPay Server's payjoin sender.
//
// Uses the BIP78.Sender 0.2.5 NuGet package that BTCPay Server ships, called the way
// UIWalletsController.PSBT.cs GetPayjoinProposedTX() calls it: MinimumFeeRate = the node's
// min relay fee, RequestPayjoin(bip21, PayjoinWallet, signed PSBT). The returned proposal is
// then signed with the hot wallet key and broadcast, as BTCPay does after the user confirms.
// On any exception the original is broadcast (BTCPay schedules that broadcast up front).
using System;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using BTCPayServer.BIP78.Sender;
using NBitcoin;
using NBitcoin.Payment;
using Newtonsoft.Json.Linq;

class HDWpkh : IHDScriptPubKey
{
    readonly ExtPubKey k;
    public HDWpkh(ExtPubKey k) { this.k = k; }
    public IHDScriptPubKey Derive(KeyPath keyPath) => new HDWpkh(k.Derive(keyPath));
    public bool CanDeriveHardenedPath() => false;
    public Script ScriptPubKey => k.PubKey.WitHash.ScriptPubKey;
}

// Same shape as BTCPayServer.Payments.PayJoin.Sender.PayjoinWallet for a single-sig p2wpkh store.
class Wallet : IPayjoinWallet
{
    readonly HDWpkh hd;
    public Wallet(ExtPubKey account, RootedKeyPath path) { hd = new HDWpkh(account); AccountKey = account; RootedKeyPath = path; }
    public IHDScriptPubKey Derive(KeyPath keyPath) => hd.Derive(keyPath);
    public bool CanDeriveHardenedPath() => false;
    public Script ScriptPubKey => hd.ScriptPubKey;
    public ScriptPubKeyType ScriptPubKeyType => ScriptPubKeyType.Segwit;
    public RootedKeyPath RootedKeyPath { get; }
    public IHDKey AccountKey { get; }
}

static class Program
{
    static Uri rpcUri;
    static string rpcAuth;
    static readonly HttpClient http = new HttpClient(new HttpClientHandler { UseProxy = false });

    static async Task<JToken> Rpc(string method, string wallet, params object[] p)
    {
        var url = new Uri(rpcUri, wallet == null ? "/" : "/wallet/" + wallet);
        var req = new HttpRequestMessage(HttpMethod.Post, url);
        req.Headers.Add("Authorization", "Basic " + rpcAuth);
        req.Content = new StringContent(new JObject { ["jsonrpc"] = "1.0", ["id"] = "c#", ["method"] = method, ["params"] = JArray.FromObject(p) }.ToString(), Encoding.UTF8, "application/json");
        var res = JObject.Parse(await (await http.SendAsync(req)).Content.ReadAsStringAsync());
        if (res["error"] != null && res["error"].Type != JTokenType.Null) throw new Exception(res["error"]["message"].ToString());
        return res["result"];
    }

    static async Task<int> Main()
    {
        var job = JObject.Parse(Console.In.ReadToEnd());
        var u = new Uri(job["rpc"].ToString());
        rpcUri = new Uri($"http://{u.Host}:{u.Port}/");
        rpcAuth = Convert.ToBase64String(Encoding.UTF8.GetBytes(Uri.UnescapeDataString(u.UserInfo)));
        var net = Network.RegTest;
        var result = new JObject();
        try
        {
            var master = new ExtKey();
            var accountPath = new KeyPath("84'/1'/0'");
            var account = master.Derive(accountPath);
            var rooted = new RootedKeyPath(master.GetPublicKey().GetHDFingerPrint(), accountPath);
            var wallet = new Wallet(account.Neuter(), rooted);
            Script Spk(string p) => wallet.Derive(new KeyPath(p)).ScriptPubKey;
            var a0 = Spk("0/0").GetDestinationAddress(net);
            var a1 = Spk("0/1").GetDestinationAddress(net);
            var spareAddr = Spk("0/2").GetDestinationAddress(net);
            var changeAddr = Spk("1/0").GetDestinationAddress(net);
            var outs = new JObject { [a0.ToString()] = 0.6m, [a1.ToString()] = 0.6m, [spareAddr.ToString()] = 0.6m };
            var txid = (await Rpc("sendmany", "pjg-miner", "", outs)).ToString();
            await Rpc("generatetoaddress", "pjg-miner", 1, (await Rpc("getnewaddress", "pjg-miner")).ToString());
            var fundTx = Transaction.Parse((await Rpc("getrawtransaction", null, txid)).ToString(), net);
            var coins = fundTx.Outputs.AsCoins().ToArray();
            var mine = coins.Where(c => c.ScriptPubKey == a0.ScriptPubKey || c.ScriptPubKey == a1.ScriptPubKey).ToArray();
            var spare = coins.Single(c => c.ScriptPubKey == spareAddr.ScriptPubKey);
            if (job["spare_file"] != null)
                File.WriteAllText(job["spare_file"].ToString(), new JObject { ["txid"] = txid, ["vout"] = (int)spare.Outpoint.N, ["value"] = spare.Amount.Satoshi, ["script"] = spare.ScriptPubKey.ToHex() }.ToString());

            var bip21 = new BitcoinUrlBuilder(job["bip21"].ToString(), net);
            var builder = net.CreateTransactionBuilder();
            builder.AddCoins(mine);
            builder.Send(bip21.Address, Money.Satoshis((long)job["amount_sat"]));
            builder.SetChange(changeAddr);
            builder.SendEstimatedFees(new FeeRate(Money.Satoshis((long)job["feerate"] * 1000)));
            var psbt = builder.BuildPSBT(false);
            psbt.AddKeyPath(master, new KeyPath("84'/1'/0'/0/0"), new KeyPath("84'/1'/0'/0/1"), new KeyPath("84'/1'/0'/1/0"));
            psbt.SignAll(ScriptPubKeyType.Segwit, account, rooted);
            var original = psbt.Clone().Finalize().ExtractTransaction();

            var client = new PayjoinClient();
            client.MinimumFeeRate = new FeeRate(1.0m); // regtest MinRelayTxFee
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            PSBT proposal;
            try
            {
                proposal = await client.RequestPayjoin(bip21, wallet, psbt, cts.Token);
            }
            catch (Exception e)
            {
                await Rpc("sendrawtransaction", null, original.ToHex());
                Console.WriteLine(new JObject { ["result"] = "fallback", ["error"] = e.GetType().Name + ": " + e.Message, ["txid"] = original.GetHash().ToString() }.ToString(Newtonsoft.Json.Formatting.None));
                return 0;
            }
            proposal.SignAll(ScriptPubKeyType.Segwit, account, rooted);
            proposal.Finalize();
            var tx = proposal.ExtractTransaction();
            try
            {
                await Rpc("sendrawtransaction", null, tx.ToHex());
                result["result"] = "payjoin";
                result["txid"] = tx.GetHash().ToString();
            }
            catch (Exception e)
            {
                await Rpc("sendrawtransaction", null, original.ToHex());
                result["result"] = "fallback";
                result["error"] = "payjoin broadcast failed: " + e.Message;
            }
        }
        catch (Exception e)
        {
            result["result"] = "error";
            result["error"] = e.ToString();
        }
        Console.WriteLine(result.ToString(Newtonsoft.Json.Formatting.None));
        return 0;
    }
}
