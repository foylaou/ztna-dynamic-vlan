# RADIUS 容器（FreeRADIUS：EAP-TLS + 動態 VLAN）

依用戶端憑證 Subject 的 `OU=VLANxx` 回傳 `Tunnel-Private-Group-Id`。
本目錄可整個複製到 Synology 部署；本機測試見 `../phase1_poc/verify.py`。

| 檔案 | 用途 |
| --- | --- |
| `Dockerfile` | 官方 FreeRADIUS 3.2 映像檔 + 下列設定 + `eapol_test` |
| `dynamic_vlan` | post-auth 規則：擷取 OU 的 VLAN 編號，沒有就拒絕 |
| `clients.conf` | 只允許 FortiGate（與容器內 localhost）送驗證請求 |
| `docker-compose.yml` | Synology 部署用，主機網路模式 |
| `.env.example` | 設定值範本 |

## 部署到 Synology

1. **停用 Synology RADIUS Server 套件**（若有安裝），否則 UDP 1812 會衝突。
2. 把整個 `radius/` 目錄複製到 NAS，例如 `/volume1/docker/ztna-radius/`。
3. 在該目錄建立 `certs/`，放入：
   - `ca.pem`：信任的用戶端憑證 CA。POC 階段用 `phase1_poc/certs/ca.pem`，
     Phase 2 之後換成 Step-CA 的根憑證。
   - `server.pem`：RADIUS 伺服器憑證與私鑰合併檔。POC 階段用 `phase1_poc/certs/server.pem`。
4. 複製 `.env.example` 為 `.env`，填入 `FORTIGATE_IP` 與 `RADIUS_SECRET`。
5. 啟動：Container Manager →「專案」→ 新增 → 選該目錄的 `docker-compose.yml`；
   或 SSH 進 NAS 在該目錄執行 `docker compose up -d --build`。
6. 若 NAS 有啟用防火牆，允許 FortiGate 的 IP 連入 UDP 1812。

## 在 NAS 上自我檢測

把一張測試用戶端憑證（如 `client-vlan11.pem` / `.key`）暫時放進 `certs/`，然後：

```bash
docker exec -it ztna-radius sh -c 'printf "network={\n key_mgmt=IEEE8021X\n eap=TLS\n identity=\"selftest\"\n ca_cert=\"/etc/raddb/certs/ca.pem\"\n client_cert=\"/etc/raddb/certs/client-vlan11.pem\"\n private_key=\"/etc/raddb/certs/client-vlan11.key\"\n}\n" > /tmp/t.conf && eapol_test -c /tmp/t.conf -a 127.0.0.1 -s "$RADIUS_SECRET" | tail -1'
```

輸出 `SUCCESS` 表示容器本身正常。測完把測試用戶端憑證與私鑰從 NAS 移除。

## 除錯

需要看逐步驗證過程時，暫時以除錯模式啟動（會印出大量記錄）：

```bash
docker compose run --rm radius freeradius -X
```

FortiGate 送來的請求若完全沒有出現在記錄裡，通常是來源 IP 與 `FORTIGATE_IP`
不符（FortiGate 可能用另一個介面的 IP 送出），或共用密鑰不一致。

## 尚未處理

- 憑證撤銷（CRL/OCSP）：目前員工離職只能等憑證到期，Phase 2 需補上。
- TLS 版本沿用映像檔預設（僅 TLS 1.2）。
