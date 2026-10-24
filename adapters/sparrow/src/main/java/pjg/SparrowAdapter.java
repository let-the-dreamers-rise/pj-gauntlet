package pjg;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sparrowwallet.drongo.KeyPurpose;
import com.sparrowwallet.drongo.Network;
import com.sparrowwallet.drongo.Utils;
import com.sparrowwallet.drongo.address.Address;
import com.sparrowwallet.drongo.crypto.ChildNumber;
import com.sparrowwallet.drongo.policy.Policy;
import com.sparrowwallet.drongo.policy.PolicyType;
import com.sparrowwallet.drongo.protocol.ScriptType;
import com.sparrowwallet.drongo.protocol.Sha256Hash;
import com.sparrowwallet.drongo.protocol.Transaction;
import com.sparrowwallet.drongo.protocol.TransactionOutput;
import com.sparrowwallet.drongo.psbt.PSBT;
import com.sparrowwallet.drongo.uri.BitcoinURI;
import com.sparrowwallet.drongo.wallet.*;
import com.sparrowwallet.sparrow.payjoin.Payjoin;

import java.io.FileWriter;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.*;

/**
 * pj-gauntlet adapter for Sparrow Wallet's payjoin sender.
 *
 * Sparrow's com.sparrowwallet.sparrow.payjoin.Payjoin is compiled unmodified (vendor/), against
 * drongo built from source at vendor/DRONGO_COMMIT. The flow follows HeadersController:
 * a single-sig P2WPKH software wallet creates and signs the original with
 * createWalletTransaction(), Payjoin.requestPayjoinPSBT(true) fetches and checks the proposal,
 * then (as the user would after reviewing it) the wallet signs, finalises and broadcasts it.
 * If requestPayjoinPSBT throws, the user is shown the error and broadcasts the original.
 */
public class SparrowAdapter {
    static String rpcUrl;
    static String auth;
    static final HttpClient http = HttpClient.newBuilder().proxy(HttpClient.Builder.NO_PROXY).build();

    static JsonObject rpc(String wallet, String method, Object... params) throws Exception {
        URI u = URI.create(rpcUrl);
        String url = "http://" + u.getHost() + ":" + u.getPort() + (wallet == null ? "/" : "/wallet/" + wallet);
        JsonObject body = new JsonObject();
        body.addProperty("jsonrpc", "1.0");
        body.addProperty("id", "sparrow");
        body.addProperty("method", method);
        body.add("params", new Gson().toJsonTree(params));
        HttpRequest req = HttpRequest.newBuilder(URI.create(url)).header("Authorization", "Basic " + auth)
                .POST(HttpRequest.BodyPublishers.ofString(body.toString())).build();
        JsonObject res = JsonParser.parseString(http.send(req, HttpResponse.BodyHandlers.ofString()).body()).getAsJsonObject();
        if(res.has("error") && !res.get("error").isJsonNull()) {
            throw new Exception(res.getAsJsonObject("error").get("message").getAsString());
        }
        return res;
    }

    public static void main(String[] args) throws Exception {
        JsonObject job = JsonParser.parseString(new String(System.in.readAllBytes(), StandardCharsets.UTF_8)).getAsJsonObject();
        rpcUrl = job.get("rpc").getAsString();
        auth = Base64.getEncoder().encodeToString(URI.create(rpcUrl).getUserInfo().getBytes(StandardCharsets.UTF_8));
        JsonObject result = new JsonObject();
        try {
            Network.set(Network.REGTEST);
            Wallet wallet = new Wallet("pjg");
            wallet.setPolicyType(PolicyType.SINGLE_HD);
            wallet.setScriptType(ScriptType.P2WPKH);
            byte[] entropy = new byte[16];
            new SecureRandom().nextBytes(entropy);
            Keystore keystore = Keystore.fromSeed(new DeterministicSeed(entropy, "", 0), PolicyType.SINGLE_HD, ScriptType.P2WPKH.getDefaultDerivation());
            keystore.setWalletModel(WalletModel.SPARROW);
            wallet.getKeystores().add(keystore);
            wallet.setDefaultPolicy(Policy.getPolicy(PolicyType.SINGLE_HD, ScriptType.P2WPKH, wallet.getKeystores(), null));
            wallet.getNode(KeyPurpose.RECEIVE).fillToIndex(5);
            wallet.getNode(KeyPurpose.CHANGE).fillToIndex(5);

            List<WalletNode> recv = new ArrayList<>(wallet.getNode(KeyPurpose.RECEIVE).getChildren());
            WalletNode n0 = recv.get(0), n1 = recv.get(1), nSpare = recv.get(2);
            JsonObject outs = new JsonObject();
            for(WalletNode n : List.of(n0, n1, nSpare)) {
                outs.addProperty(wallet.getAddress(n).toString(), 0.6);
            }
            String txid = rpc("pjg-miner", "sendmany", "", outs).get("result").getAsString();
            String mineTo = rpc("pjg-miner", "getnewaddress").get("result").getAsString();
            rpc("pjg-miner", "generatetoaddress", 1, mineTo);
            int height = rpc(null, "getblockcount").get("result").getAsInt();
            Transaction fund = new Transaction(Utils.hexToBytes(rpc(null, "getrawtransaction", txid).get("result").getAsString()));
            Sha256Hash fundId = fund.getTxId();
            wallet.updateTransactions(Map.of(fundId, new BlockTransaction(fundId, height, new Date(), 0L, fund)));
            JsonObject spare = null;
            for(int i = 0; i < fund.getOutputs().size(); i++) {
                TransactionOutput o = fund.getOutputs().get(i);
                for(WalletNode n : List.of(n0, n1, nSpare)) {
                    if(o.getScript().equals(wallet.getOutputScript(n))) {
                        n.getTransactionOutputs().add(new BlockTransactionHashIndex(fundId, height, new Date(), 0L, i, o.getValue()));
                        if(n == nSpare) {
                            spare = new JsonObject();
                            spare.addProperty("txid", txid);
                            spare.addProperty("vout", i);
                            spare.addProperty("value", o.getValue());
                            spare.addProperty("script", Utils.bytesToHex(o.getScript().getProgram()));
                        }
                    }
                }
            }
            wallet.setStoredBlockHeight(height);
            if(job.has("spare_file") && spare != null) {
                try(FileWriter w = new FileWriter(job.get("spare_file").getAsString())) {
                    w.write(spare.toString());
                }
            }

            BitcoinURI uri = new BitcoinURI(job.get("bip21").getAsString());
            long amount = job.get("amount_sat").getAsLong();
            // spend the two payment coins, keep the spare out of coin selection
            Set<BlockTransactionHashIndex> chosen = new HashSet<>();
            chosen.addAll(n0.getTransactionOutputs());
            chosen.addAll(n1.getTransactionOutputs());
            TransactionParameters params = new TransactionParameters(List.of(new PresetUtxoSelector(chosen)), Collections.emptyList(),
                    List.of(new Payment(uri.getAddress(), "pjg", amount, false)), Collections.emptyList(), Collections.emptySet(),
                    job.get("feerate").getAsDouble(), 1.0d, 1.0d, null, height, false, false, true);
            WalletTransaction wtx = wallet.createWalletTransaction(params);
            PSBT psbt = wtx.createPSBT();
            wallet.sign(psbt);
            wallet.finalise(psbt);
            Transaction original = psbt.extractTransaction();

            PSBT proposal;
            try {
                Payjoin payjoin = new Payjoin(uri, wallet, psbt);
                proposal = payjoin.requestPayjoinPSBT(true);
            } catch(Exception e) {
                rpc(null, "sendrawtransaction", Utils.bytesToHex(original.bitcoinSerialize()));
                result.addProperty("result", "fallback");
                result.addProperty("error", e.getMessage() + (e.getCause() != null ? " / " + e.getCause() : ""));
                result.addProperty("txid", original.getTxId().toString());
                System.out.println(result);
                System.exit(0);
                return;
            }
            wallet.sign(proposal);
            wallet.finalise(proposal);
            Transaction tx = proposal.extractTransaction();
            try {
                rpc(null, "sendrawtransaction", Utils.bytesToHex(tx.bitcoinSerialize()));
                result.addProperty("result", "payjoin");
                result.addProperty("txid", tx.getTxId().toString());
            } catch(Exception e) {
                rpc(null, "sendrawtransaction", Utils.bytesToHex(original.bitcoinSerialize()));
                result.addProperty("result", "fallback");
                result.addProperty("error", "payjoin broadcast failed: " + e.getMessage());
            }
        } catch(Throwable e) {
            result.addProperty("result", "error");
            java.io.StringWriter sw = new java.io.StringWriter();
            e.printStackTrace(new java.io.PrintWriter(sw));
            result.addProperty("error", sw.toString());
        }
        System.out.println(result);
        System.exit(0);
    }
}
