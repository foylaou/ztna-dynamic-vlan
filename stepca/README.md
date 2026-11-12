# Step-CA 容器（SCEP 憑證機構）

以 SCEP 簽發用戶端憑證，供 `../radius/` 做 EAP-TLS 驗證。
本目錄可整個複製到 Synology 部署；本機端到端測試見 `../phase2_stepca/verify.py`。

| 檔案 | 用途 |
| --- | --- |
| `docker-compose.yml` | `stepca` 服務與一次性的 `init` 服務 |
| `init.sh` | 建立根 CA（RSA 4096）、中繼 CA（RSA 2048）與 `ca.json` |
| `templates/client-auth.tpl` | 靜態模式範本：沿用申請的 Subject，用途限定用戶端驗證 |
| `templates/webhook-vlan.tpl` | Webhook 模式範本：CN 取自 CSR，OU 由 Webhook 回傳的 vlan 決定 |
| `.env.example` | 設定值範本 |

版本：`smallstep/step-ca:0.30.2`。

## 本機驗證結果（2026-11-12）

| 情境 | 結果 |
| --- | --- |
| SCEP 申請 `OU=VLAN20` → RADIUS | Accept，VLAN 20 |
| SCEP 申請 `OU=VLAN11` → RADIUS | Accept，VLAN 11 |
| SCEP 挑戰密碼錯誤 | 拒絕簽發 |
| 非 Step-CA 簽發的憑證 → 只信任 Step-CA 的 RADIUS | Reject |

## 部署

```bash
cp .env.example .env                      # 填入設定，並選定 Webhook 或靜態模式
docker compose run --rm init              # 只做一次，會印出根憑證指紋
docker compose --profile webhook up -d    # Webhook 模式；靜態模式不加 --profile
```

模式在 `init` 時寫進 CA 設定，兩種模式的差異與 Webhook 說明見
[phase3_webhook/README.md](../phase3_webhook/README.md)。

- SCEP 網址：`http://<CA_DNS>:8080/scep/intune`（或 `https://<CA_DNS>:9000/scep/intune`）
- 根憑證：`docker exec ztna-stepca cat /home/step/certs/root_ca.crt`
- 給 RADIUS 的信任鏈（`radius/certs/ca.pem`）＝根憑證 + 中繼憑證串接：

```bash
docker exec ztna-stepca sh -c 'cat /home/step/certs/root_ca.crt /home/step/certs/intermediate_ca.crt' > ../radius/certs/ca.pem
```

資料存在具名磁碟區 `stepca-data`。**`docker compose down -v` 會刪掉整個 CA**，
所有已簽發憑證都會失效。

## 與規劃書不同之處（實測與查證結果）

### 1. 接 Intune 一定要寫 Webhook，沒有「零程式碼」選項

Intune 給每台裝置的是一次性的動態挑戰碼，SCEP 伺服器必須呼叫 Intune 的驗證 API
確認，無法用固定密碼比對。Step-CA 開源版的做法是 `SCEPCHALLENGE` Webhook：
Step-CA 把 CSR 與挑戰碼 POST 給我們的服務，由它向 Intune 驗證後回覆 allow/deny。

已從 Step-CA 原始碼確認的行為：

- Webhook 網址必須是 HTTPS。
- Webhook 密鑰不能寫在 `ca.json`，要啟用遠端管理後用
  `step ca provisioner webhook add` 建立（密鑰存在 CA 資料庫）。
- 請求帶 `X-Smallstep-Signature`（HMAC-SHA256）與 `X-Smallstep-Webhook-ID` 標頭。
- 回應的 `data` 會進入範本，可用 `.Webhooks.<名稱>` 取用——
  Phase 3 就是用這個把 Entra 群組的 vlan 值寫進 OU。

Intune 端需要一個 Entra 應用程式註冊，具備 Intune 的 `scep_challenge_provider`
與 Graph 的 `Application.Read.All` 應用程式權限。

### 2. 連到 SCEP 伺服器的是裝置，不是 Intune

規劃書寫「FortiGate 只允許 Intune IP 區段連入」，這樣裝置會申請不到憑證。
實際流程是 Intune 把 SCEP 網址與挑戰碼派給裝置，**裝置自己**連到 Step-CA 送 CSR；
Step-CA（Webhook）再**對外**連 Intune 驗證。因此：

- SCEP 網址必須讓尚未取得憑證的裝置連得到（從網際網路，或從隔離的註冊用網段）。
- 對外公開時只開 SCEP 路徑，不要把 9000 的管理 API 一起公開。

### 3. 靜態挑戰密碼只適合測試

目前的 `SCEP_CHALLENGE` 是共用密碼，知道的人可以申請任意 Subject 的憑證
（包括任意 `OU=VLANxx`）。接上 Intune 驗證後，Intune 會核對 CSR 的 Subject
是否與它派發的設定檔一致，這個問題才會消失。**在那之前不要讓正式 RADIUS 信任這個 CA。**

## Intune 端設定（未實測）

1. 受信任憑證設定檔：上傳 Step-CA 根憑證，指派給所有相關群組。
2. SCEP 憑證設定檔：
   - 主體名稱格式：`CN={{UserPrincipalName}}`（Webhook 模式下 OU 由 CA 決定，一份設定檔即可）
   - 金鑰用法：數位簽章、金鑰編密；金鑰大小 2048；延伸金鑰使用方法：用戶端驗證
   - 根憑證：選步驟 1 的設定檔
   - SCEP 伺服器 URL：`https://<對外主機名稱>/scep/intune`
3. Wi-Fi 與有線網路設定檔：EAP-TLS，用戶端憑證選步驟 2 的設定檔，
   伺服器信任選 RADIUS 伺服器憑證的根憑證。

## 尚未處理

- 根 CA 私鑰目前與中繼 CA 一起放在 NAS 的磁碟區。正式環境應在初始化後把
  `secrets/root_ca_key` 移到離線保存。
- 憑證撤銷：Step-CA 可撤銷，但 RADIUS 端還沒設定 CRL/OCSP 檢查。
- RADIUS 伺服器憑證仍是 Phase 1 的測試憑證，尚未改由 Step-CA 簽發。
