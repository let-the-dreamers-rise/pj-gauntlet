package com.sparrowwallet.sparrow.net;

import com.google.common.net.HostAndPort;

/**
 * Sparrow's HttpClientService minus the Tor proxy plumbing: it is the real tern HttpClientService
 * (com.sparrowwallet:tern:1.0.6, the version Sparrow ships) with no proxy.
 */
public class HttpClientService extends com.sparrowwallet.tern.http.client.HttpClientService {
    public HttpClientService(HostAndPort torProxy) {
        super(new com.sparrowwallet.tern.http.client.IHttpProxySupplier() {
            public java.util.Optional<com.sparrowwallet.tern.http.client.HttpProxy> getHttpProxy(com.sparrowwallet.tern.http.client.HttpUsage usage) {
                return java.util.Optional.empty();
            }

            public void changeIdentity() {
            }
        });
    }

    public HostAndPort getTorProxy() {
        return null;
    }
}
