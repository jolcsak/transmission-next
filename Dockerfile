# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM ubuntu:24.04 AS build
ARG TARGETARCH
ARG BUILD_JOBS=2
RUN test "$TARGETARCH" = amd64
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build pkg-config ca-certificates python3-pip \
    libcurl4-openssl-dev libssl-dev libevent-dev libdeflate-dev libpsl-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY CMakeLists.txt CMakePresets.json COPYING AUTHORS update-version-h.sh ./
COPY cmake/ cmake/
COPY daemon/ daemon/
COPY libtransmission/ libtransmission/
COPY libtransmission-app/ libtransmission-app/
COPY third-party/ third-party/
COPY licenses/ licenses/
COPY extras/vpn/rpc-relay.c extras/vpn/rpc-relay.c
RUN cmake --preset lean -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=ON \
    -DCMAKE_C_FLAGS_RELEASE='-O3 -DNDEBUG -march=x86-64 -mtune=generic' \
    -DCMAKE_CXX_FLAGS_RELEASE='-O3 -DNDEBUG -march=x86-64 -mtune=generic' \
    -DWITH_SYSTEMD=OFF -DWITH_CRYPTO=openssl \
    && cmake --build build/lean --parallel "$BUILD_JOBS" \
    && cmake --install build/lean --prefix /opt/transmission --strip \
    && cc -O3 -flto -march=x86-64 -mtune=generic -D_FORTIFY_SOURCE=3 \
       -fstack-protector-strong -Wl,-z,relro,-z,now \
       extras/vpn/rpc-relay.c -o /opt/transmission/rpc-relay \
    && strip /opt/transmission/rpc-relay
# Ship the exact corresponding source, including local modifications and build recipe.
RUN pip3 install --no-cache-dir --target /opt/python paho-mqtt==2.1.0
COPY . /source-tree/
RUN mkdir -p /source && tar -C /source-tree -czf /source/transmission-source.tar.gz .

FROM ubuntu:24.04 AS runtime
LABEL org.opencontainers.image.title="Transmission optimized with managed VPN" \
      org.opencontainers.image.description="AMD64 Transmission with disk profiles, PureVPN, HTTPS, metrics and security logs" \
      org.opencontainers.image.licenses="(GPL-2.0-only OR GPL-3.0-only) AND MIT"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/python \
    TRANSMISSION_WEB_HOME=/opt/transmission/share/transmission/public_html \
    TRANSMISSION_CONFIG_DIR=/config
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcurl4t64 libssl3t64 libevent-core-2.1-7t64 libevent-extra-2.1-7t64 \
    libdeflate0 libpsl5t64 libstdc++6 ca-certificates \
    python3 openvpn iproute2 nftables util-linux nginx-light openssl tini \
    && rm -rf /var/lib/apt/lists/* /var/log/apt/* \
    && userdel -r ubuntu && groupadd -g 1000 transmission \
    && useradd -u 1000 -g transmission -d /config -s /usr/sbin/nologin transmission \
    && mkdir -p /config /downloads /etc/netns /etc/transmission-rpc-tls \
    && chown transmission:transmission /config /downloads
COPY --from=build /opt/transmission/ /opt/transmission/
COPY --from=web /web/public_html/ /opt/transmission/share/transmission/public_html/
COPY --from=build /opt/python/ /opt/python/
COPY --from=build /source/ /usr/share/transmission/source/
COPY COPYING /usr/share/doc/transmission/COPYING
COPY licenses/ /usr/share/doc/transmission/licenses/
COPY extras/vpn/*.py /opt/transmission/extras/vpn/
COPY extras/vpn/providers/ /opt/transmission/extras/vpn/providers/
COPY extras/mqtt/*.py /opt/transmission/mqtt/
COPY extras/auto/settings.json /opt/transmission/extras/auto/settings.json
RUN mv /opt/transmission/rpc-relay /opt/transmission/extras/vpn/rpc-relay
COPY docker/entrypoint.py docker/healthcheck.py /opt/transmission/docker/
COPY docker/nginx.conf /etc/transmission-rpc-tls/nginx.conf
EXPOSE 9091/tcp
VOLUME ["/config", "/downloads"]
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python3", "/opt/transmission/docker/healthcheck.py"]
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/opt/transmission/docker/entrypoint.py"]
