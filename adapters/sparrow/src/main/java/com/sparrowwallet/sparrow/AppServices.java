package com.sparrowwallet.sparrow;

import com.sparrowwallet.drongo.protocol.Transaction;
import com.sparrowwallet.sparrow.net.HttpClientService;

/**
 * Stand-in for Sparrow's JavaFX AppServices singleton, exposing only what Payjoin.java calls.
 * Values match what the real class returns for a wallet connected to a node with default policy
 * and no Tor proxy configured.
 */
public class AppServices {
    private static final HttpClientService httpClientService = new HttpClientService(null);

    public static Double getMinimumRelayFeeRate() {
        return Transaction.DEFAULT_MIN_RELAY_FEE;
    }

    public static HttpClientService getHttpClientService() {
        return httpClientService;
    }
}
