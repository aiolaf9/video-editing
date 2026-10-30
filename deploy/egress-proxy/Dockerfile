# Proxy keluar (squid dari paket Debian resmi) untuk worker unduhan. Berjalan sebagai user non-root "proxy".
FROM debian:stable-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends squid \
    && rm -rf /var/lib/apt/lists/*
COPY squid.conf /etc/squid/squid.conf
USER proxy
EXPOSE 3128
CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]
