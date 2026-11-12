# FreeRADIUS：EAP-TLS + 依憑證 OU 回傳動態 VLAN。
# 內含 eapol_test，供本機 POC 與在 NAS 上自我檢測使用。
FROM freeradius/freeradius-server:latest-3.2

RUN apt-get update \
 && apt-get install -y --no-install-recommends eapoltest \
 && rm -rf /var/lib/apt/lists/*

COPY dynamic_vlan /etc/raddb/policy.d/dynamic_vlan
COPY clients.conf /etc/raddb/clients.conf

# 在 post-auth 區段最前面呼叫 dynamic_vlan
RUN sed -i '/^post-auth {/a\\tdynamic_vlan' /etc/raddb/sites-available/default

# 憑證由執行時掛載到 /etc/raddb/certs（需有 ca.pem 與 server.pem）
