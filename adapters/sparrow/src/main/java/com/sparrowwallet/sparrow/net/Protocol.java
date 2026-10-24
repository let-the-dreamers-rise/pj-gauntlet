package com.sparrowwallet.sparrow.net;

import java.util.Locale;

/** Only the method Payjoin.java uses, with the same body as Sparrow's Protocol.isOnionHost. */
public enum Protocol {
    TCP;

    public static boolean isOnionHost(String host) {
        return host != null && host.toLowerCase(Locale.ROOT).endsWith(".onion");
    }
}
