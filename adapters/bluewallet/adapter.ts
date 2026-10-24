/**
 * pj-gauntlet adapter for BlueWallet's payjoin sender.
 *
 * vendor/payjoin-client is BlueWallet's blue_modules/payjoin-client copied unmodified
 * (commit in vendor/BLUEWALLET_COMMIT). The wallet glue below mirrors
 * class/payjoin-transaction.ts and screen/send/Confirm.tsx at that commit: the same
 * getPsbt/signPsbt/broadcast logic, the same payjoinParameters() (minfeerate = the
 * chosen sat/vB, fee output = our change, max contribution = ceil(rate * 68)), and
 * pjos=0 handling from class/deeplink-schema-match.ts. Only React Native UI calls
 * (alerts, haptics) are left out.
 */
import { readFileSync } from 'fs';
import * as bitcoin from 'bitcoinjs-lib';
import { ECPairFactory } from 'ecpair';
import ecc from './vendor/noble_ecc';
import { getVirtualSize, PayjoinClient, PayjoinClientOptionalParameters, ScriptPubKeyType } from './vendor/payjoin-client';

const ECPair = ECPairFactory(ecc);
bitcoin.initEccLib(ecc);
const network = bitcoin.networks.regtest;

type Utxo = { txid: string; vout: number; value: number; script: string; wif: string; address: string };
type Job = {
  rpc: string;
  bip21: string;
  amount_sat: number;
  address: string;
  utxos: Utxo[];
  spare: Utxo;
  change: { wif: string; address: string; script: string };
  feerate: number;
};

async function rpc(url: string, method: string, params: unknown[]): Promise<any> {
  const u = new URL(url);
  const res = await fetch(`${u.protocol}//${u.host}/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: 'Basic ' + Buffer.from(`${decodeURIComponent(u.username)}:${decodeURIComponent(u.password)}`).toString('base64'),
    },
    body: JSON.stringify({ jsonrpc: '1.0', id: 'bw', method, params }),
  });
  const body = await res.json();
  if (body.error) throw new Error(body.error.message);
  return body.result;
}

function parseBip21(uri: string) {
  const rest = uri.slice(uri.indexOf(':') + 1);
  const q = rest.indexOf('?');
  const address = q === -1 ? rest : rest.slice(0, q);
  const query = q === -1 ? '' : rest.slice(q + 1);
  const options: Record<string, string> = {};
  new URLSearchParams(query).forEach((v, k) => (options[k] = v));
  return { address, options };
}

async function main() {
  const job: Job = JSON.parse(readFileSync(0, 'utf8'));
  const keys = new Map<string, string>(); // address -> wif, like HDSegwitBech32Wallet._getWifForAddress
  for (const u of [...job.utxos, job.spare]) keys.set(u.address, u.wif);
  keys.set(job.change.address, job.change.wif);
  const changeAddresses = new Set([job.change.address]);

  // wallet.createTransaction(): finalized psbt, fee = rate * vsize
  const build = (fee: number) => {
    const psbt = new bitcoin.Psbt({ network });
    let total = 0;
    for (const u of job.utxos) {
      psbt.addInput({
        hash: u.txid,
        index: u.vout,
        sequence: 0xfffffffd,
        witnessUtxo: { script: Buffer.from(u.script, 'hex'), value: BigInt(u.value) },
      });
      total += u.value;
    }
    psbt.addOutput({ address: job.address, value: BigInt(job.amount_sat) });
    psbt.addOutput({ address: job.change.address, value: BigInt(total - job.amount_sat - fee) });
    job.utxos.forEach((u, i) => psbt.signInput(i, ECPair.fromWIF(u.wif, network)));
    psbt.finalizeAllInputs();
    return psbt;
  };
  const vsize = build(1000).extractTransaction().virtualSize();
  // coinselect-style estimate: never under the chosen rate whatever the signature lengths
  const psbt = build(Math.ceil((vsize + job.utxos.length) * job.feerate));
  const tx = psbt.extractTransaction().toHex();

  const { address, options } = parseBip21(job.bip21);
  const payjoinUrl = options.pj;
  const payjoinDisableOutputSubstitution = String(options.pjos) === '0';
  const paymentScript = bitcoin.address.toOutputScript(address, network);

  let originalBroadcast = false;
  let payjoinPsbt: bitcoin.Psbt | false = false;
  let signedProposal: bitcoin.Psbt | false = false;
  const originalTxid = psbt.extractTransaction().getId();
  const broadcastTransaction = async (hex: string) => {
    await rpc(job.rpc, 'sendrawtransaction', [hex]);
    return true as const;
  };

  const wallet = {
    async getPsbt() {
      const unfinalized = psbt.clone();
      for (const [index, input] of unfinalized.data.inputs.entries()) {
        delete input.finalScriptWitness;
        const addr = bitcoin.address.fromOutputScript(input.witnessUtxo!.script, network);
        unfinalized.signInput(index, ECPair.fromWIF(keys.get(addr)!, network));
      }
      return unfinalized;
    },
    async signPsbt(proposal: bitcoin.Psbt) {
      for (const [index, input] of proposal.data.inputs.entries()) {
        if (input.finalScriptWitness || input.finalScriptSig) continue;
        if (!input.witnessUtxo) throw new Error('Internal error: input.witnessUtxo is not set');
        const addr = bitcoin.address.fromOutputScript(input.witnessUtxo.script, network);
        const wif = keys.get(addr);
        if (!wif) throw new Error('no key for ' + addr);
        proposal.signInput(index, ECPair.fromWIF(wif, network)).finalizeInput(index);
      }
      signedProposal = proposal;
      return proposal;
    },
    async broadcastTx(txHex: string) {
      try {
        const result = await broadcastTransaction(txHex);
        if (!result) throw new Error('Broadcast failed');
        if (bitcoin.Transaction.fromHex(txHex).getId() === originalTxid) originalBroadcast = true;
        else payjoinPsbt = signedProposal;
        return '';
      } catch (e: any) {
        return 'Error: ' + e.message;
      }
    },
    async scheduleBroadcastTx(_txHex: string, _ms: number) {
      // BlueWallet re-broadcasts the original after 2 minutes; it conflicts once the payjoin is out.
    },
  };

  // PayjoinTransaction.payjoinParameters()
  const feeRate = job.feerate;
  const params: PayjoinClientOptionalParameters = { minimumFeeRate: feeRate };
  const changeIndex = psbt.txOutputs.findIndex(o => !!o.address && changeAddresses.has(o.address));
  if (changeIndex !== -1) {
    params.additionalFeeOutputIndex = changeIndex;
    params.maxAdditionalFeeContribution = Math.ceil(feeRate * getVirtualSize(ScriptPubKeyType.Segwit));
  }
  const client = new PayjoinClient({
    paymentScript,
    wallet,
    payjoinUrl,
    payjoinParameters: { ...params, ...(payjoinDisableOutputSubstitution ? { disableOutputSubstitution: true } : {}) },
  });
  let result: any;
  try {
    await client.run();
    result = { result: payjoinPsbt ? 'payjoin' : 'error', txid: payjoinPsbt ? payjoinPsbt.extractTransaction().getId() : null };
  } catch (e: any) {
    result = { result: originalBroadcast ? 'fallback' : 'error', error: e.message, txid: originalBroadcast ? originalTxid : null };
  }
  void tx;
  console.log(JSON.stringify(result));
  process.exit(0);
}

main().catch(e => {
  console.log(JSON.stringify({ result: 'error', error: String(e && e.stack ? e.stack : e) }));
  process.exit(0);
});
