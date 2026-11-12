# EAP-TLS 動態 VLAN 架構：POC 驗證報告

- 日期：2026-11-12
- 測試租用戶：一個 Microsoft 365 測試租用戶
- 測試環境：macOS 上的 Docker；未使用任何 Fortinet 設備、Synology 或已註冊 Intune 的裝置

## 結論

**伺服器端的整條鏈已實測可行**：Entra 群組的 `vlan` 屬性 → Webhook → Step-CA 以 SCEP
簽出 `OU=VLANxx` 憑證 → FreeRADIUS 以 EAP-TLS 驗證並回傳動態 VLAN 屬性。

**設備端與 Intune 端尚未驗證**：Fortinet 設備收到屬性後是否切 VLAN、真實裝置的
802.1X 連線、Intune 派發憑證與動態挑戰碼驗證，都需要實機，留待後續測試。

原規劃書有三處與實測或查證結果不符，已改用可行的做法（見「與原規劃書的差異」）。

## 驗證範圍總覽

| 環節 | 狀態 | 依據 |
| --- | --- | --- |
| Entra 群組可設自訂 `vlan` 屬性 | 已實測 | Graph API 寫入並讀回 |
| 由使用者帳號查出所屬群組的 VLAN | 已實測 | 對真實租用戶查詢 |
| RADIUS 以 EAP-TLS 驗證憑證 | 已實測 | `eapol_test` 對 FreeRADIUS 3.2.10 |
| RADIUS 依憑證 OU 回傳 VLAN 屬性 | 已實測 | Access-Accept 封包內容 |
| RADIUS 只回應已登錄的網路設備 | 已實測 | 密鑰錯誤、來源 IP 未登錄皆不回應 |
| Step-CA 以 SCEP 簽發憑證 | 已實測 | `scepclient` 對 Step-CA 0.30.2 |
| Webhook 決定憑證 OU（忽略 CSR 自填值） | 已實測 | CSR 填 `OU=VLAN99`，憑證為群組的值 |
| Webhook 向 Intune 驗證動態挑戰碼 | **未驗證** | 程式已寫，需已註冊裝置 |
| Webhook 以應用程式憑證登入 | **未驗證** | 測試使用 Azure CLI 的使用者權杖 |
| Intune 派發根憑證、SCEP、網路設定檔 | **未驗證** | 租用戶 0 台裝置、0 個設定檔 |
| FortiAP / FortiSwitch 依屬性切 VLAN | **未驗證** | 需實機 |
| Windows / macOS / 手機實際連線 | **未驗證** | 需實機 |
| 部署到 Synology | **未驗證** | 僅在 macOS Docker 執行過 |

## 已驗證的結果

三個階段各有一支可重跑的自動化腳本，會自行建立與清除測試容器。

### Phase 1：憑證 → RADIUS → VLAN

`python phase1_poc/verify.py`。請求由模擬 FortiGate 的容器從獨立 IP 送出。

| 情境 | 結果 | VLAN |
| --- | --- | --- |
| `OU=VLAN11` 憑證 | Accept | 11 |
| `OU=VLAN20` 憑證 | Accept | 20 |
| 受信任 CA 簽發但無 VLAN OU | Reject | - |
| 不受信任 CA 簽發 | Reject | - |
| 共用密鑰錯誤 | 不回應 | - |
| 來源 IP 未登錄 | 不回應 | - |

Accept 封包帶有 `Tunnel-Type=VLAN(13)`、`Tunnel-Medium-Type=IEEE-802(6)`、
`Tunnel-Private-Group-Id`。

### Phase 2：Step-CA（SCEP）→ RADIUS

`python phase2_stepca/verify.py`。RADIUS 只信任 Step-CA 的根與中繼憑證。

| 情境 | 結果 |
| --- | --- |
| SCEP 申請 `OU=VLAN20` → RADIUS | Accept，VLAN 20 |
| SCEP 申請 `OU=VLAN11` → RADIUS | Accept，VLAN 11 |
| SCEP 挑戰密碼錯誤 | 拒絕簽發 |
| 非 Step-CA 簽發的憑證 → RADIUS | Reject |

### Phase 3：Entra 群組 → Webhook → 憑證 OU

`python phase3_webhook/verify.py`。查詢真實租用戶（唯讀）；挑戰碼以固定密碼代替 Intune。

| 情境 | 結果 |
| --- | --- |
| 使用者 A（Vlan 20 群組），CSR 填 `OU=VLAN99` | 憑證 `OU=VLAN20` → RADIUS Accept，VLAN 20 |
| 使用者 B（Vlan 10 群組），CSR 填 `OU=VLAN99` | 憑證 `OU=VLAN10` → RADIUS Accept，VLAN 10 |
| 使用者 C（不在任何 VLAN 群組） | 拒絕簽發 |
| 挑戰碼錯誤 | 拒絕簽發 |

## 實際建成的架構

```
Entra 群組（vlan 延伸屬性）
        ▲ Graph API 查詢
        │
    Webhook ──► Intune 驗證 API（驗證動態挑戰碼，未實測）
        ▲
        │ SCEPCHALLENGE webhook（compose 內部網路，HTTPS）
        │
裝置 ──SCEP──► Step-CA ──簽發 CN=<UPN>, OU=VLANxx──► 裝置
                                                      │
                                                      │ 802.1X（EAP-TLS）
                                                      ▼
                       FortiAP / FortiSwitch ──RADIUS──► FreeRADIUS
                              ▲                              │
                              └── Tunnel-Private-Group-Id ───┘
```

| 元件 | 目錄 | 說明 |
| --- | --- | --- |
| FreeRADIUS 容器 | `radius/` | EAP-TLS；post-auth 規則由 OU 擷取 VLAN；無 OU 則拒絕 |
| Step-CA 容器 | `stepca/` | RSA 根與中繼 CA；SCEP 佈建器；Webhook 與靜態兩種模式 |
| Webhook 服務 | `phase3_webhook/` | 驗證挑戰碼、查群組 vlan、回報 Intune |
| 測試工具 | `phase1_poc/`、`phase2_stepca/` | 測試憑證產生、端到端驗證腳本 |

## 與原規劃書的差異

### 1. RADIUS 的 `users` 檔規則無效

規劃書的 `DEFAULT TLS-Client-Cert-Subject =~ "OU=VLAN11"` 實測會驗證成功但
**不回傳任何 VLAN 屬性**：`users` 檔在 TLS 交握完成前執行，當下沒有憑證資訊。
照原案上線，所有有效憑證都會落入連接埠的預設 VLAN。

改用 post-auth 階段的單一規則，以 regex 擷取 VLAN 編號。新增 VLAN 不需改 RADIUS。
原寫法另有子字串誤判的問題（`VLAN110` 會符合 `VLAN11`），新規則沒有。

### 2. 接 Intune 沒有「零程式碼」選項

規劃書的 Phase 2 是「多份 SCEP 設定檔、不寫程式」。但 Intune 給每台裝置的是一次性
動態挑戰碼，SCEP 伺服器必須呼叫 Intune 驗證 API；開源版 Step-CA 只能透過
`SCEPCHALLENGE` Webhook 做到。因此 Webhook 是接 Intune 的必要條件，
原本的 Phase 2 與 Phase 3 實際上合併為一步。

好處是 Intune 只需一份 SCEP 設定檔（主體 `CN={{UserPrincipalName}}`），VLAN 由 CA 決定。

### 3. 連到 SCEP 伺服器的是裝置，不是 Intune

規劃書寫「FortiGate 只允許 Intune IP 區段連入 Step-CA」。實際流程是 Intune 把 SCEP
網址與挑戰碼派給裝置，由**裝置**連到 Step-CA；Step-CA 的 Webhook 再**對外**連 Intune。
照原案設定防火牆，裝置會申請不到憑證。SCEP 網址必須讓尚未取得憑證的裝置連得到。

### 4. 其他調整

- **Entra 群組屬性**：入口網站無法設定，改用目錄延伸屬性（Graph API）。
  需替應用程式註冊建立服務主體，否則寫入會失敗。詳見 `entra-vlan-extension.md`。
  **日常調整員工 VLAN 只要改群組成員**，入口網站即可操作；新增 VLAN 群組時才需用 API 設屬性。
- **RADIUS 改用容器**：不使用 Synology RADIUS Server 套件，避免手改設定被套件更新覆寫。

## 測試過程對環境的變更

| 位置 | 變更 | 備註 |
| --- | --- | --- |
| 租用戶 | 應用程式註冊 `ztna-vlan-attributes` 與其服務主體 | 無 API 權限、無密碼；**刪除會連同所有群組的 vlan 值一起消失** |
| 租用戶 | 群組 `Vlan 10`、`Vlan 20` 各新增 `vlan` 值（10、20） | 成員未變動 |
| 本機 | 安裝 Azure CLI 2.92.0，並以管理員帳號登入 | 測試結束後可 `az logout` |
| 本機 | Docker 映像檔：FreeRADIUS、Step-CA、Go、Python 及本專案建置的映像檔 | 測試容器與磁碟區皆已清除 |

Intune 內沒有建立任何設定檔，也沒有變更任何群組成員。

## 待測項目

### A. Fortinet 實機（不需要 Intune，建議先做）

目的：確認設備收到 `Tunnel-Private-Group-Id` 後，用戶端取得對應 VLAN 的 IP。

1. 依 `radius/README.md` 把 RADIUS 容器部署到 Synology，`certs/` 放 Phase 1 的測試憑證。
2. FortiGate 建立測試 VLAN（如 11、20、Guest 99）與 DHCP、防火牆政策。
3. FortiGate 新增 RADIUS 伺服器，指向 NAS，共用密鑰與 `.env` 一致。
4. 建立測試 SSID（WPA2/3-Enterprise，啟用動態 VLAN）；FortiSwitch 連接埠套用
   MAC-based 802.1X 安全政策，allowed VLANs 涵蓋所有動態 VLAN。
5. 把 `client-vlan11.pem` / `.key` 與 `ca.pem` 手動匯入一台測試電腦，設定 EAP-TLS 連線。

| 檢查項目 | 預期 |
| --- | --- |
| VLAN11 憑證連 Wi-Fi | 取得 VLAN 11 的 IP |
| VLAN20 憑證連 Wi-Fi | 取得 VLAN 20 的 IP |
| VLAN11 憑證接有線埠 | 取得 VLAN 11 的 IP |
| 無憑證或無效憑證 | 無法連線，或落入 Guest VLAN |
| 同一埠接兩台不同 VLAN 的裝置（經小型交換器） | 各自進入自己的 VLAN |
| 印表機等無憑證設備（MAB） | 進入指定的固定 VLAN |

可能遇到的問題：`FORTIGATE_IP` 填錯（FortiGate 用哪個介面送 RADIUS）、主機網路模式在
Synology 上的行為、Windows 對 RADIUS 伺服器憑證名稱的檢查、僅支援 TLS 1.2 的預設值。

### B. Intune 整合（需要至少一台可註冊的裝置）

1. 替應用程式註冊加上權限並由管理員同意：Graph 的 `User.Read.All`、
   `GroupMember.Read.All`、`Application.Read.All`，Intune 的 `scep_challenge_provider`；
   建立用戶端密碼。
2. 以 Webhook 模式部署 Step-CA（`CHALLENGE_MODE=intune`），並讓裝置連得到 SCEP 網址。
3. Intune 建立：受信任憑證設定檔（Step-CA 根憑證）、SCEP 憑證設定檔、
   Wi-Fi 與有線 802.1X 設定檔。步驟見 `stepca/README.md`。
4. 註冊一台裝置並指派設定檔。

| 檢查項目 | 預期 |
| --- | --- |
| Webhook 以應用程式憑證取得權杖 | 成功，記錄無驗證錯誤 |
| 裝置取得憑證 | Subject 為 `CN=<UPN>, OU=VLANxx`，與群組一致 |
| Intune 入口網站的憑證報表 | 顯示已簽發 |
| 不在 VLAN 群組的使用者 | 拒絕簽發，Intune 顯示失敗 |
| 把使用者移到另一個 VLAN 群組並更新憑證 | 新憑證帶新的 OU |
| 裝置以派發的設定檔自動連線 | 進入對應 VLAN |

## 已知風險與未處理事項

| 項目 | 說明 |
| --- | --- |
| 憑證撤銷 | RADIUS 尚未檢查 CRL/OCSP。員工離職或調動 VLAN 後，舊憑證在到期前（預設 90 天）仍有效 |
| VLAN 變更的生效時間 | 改群組成員不會讓既有憑證失效，要等憑證更新或重新簽發 |
| 根 CA 私鑰 | 目前與中繼 CA 一起放在容器磁碟區，正式環境應移至離線保存 |
| Step-CA 磁碟區 | `docker compose down -v` 會刪除整個 CA，所有已簽發憑證失效；需納入備份 |
| Webhook 保護方式 | 以網址中的隨機權杖加內部網路隔離保護，未使用 Step-CA 的簽章機制 |
| Webhook 的 TLS 憑證 | 有效期一年，到期需重簽，否則 Step-CA 無法呼叫 Webhook、所有簽發失敗 |
| RADIUS 伺服器憑證 | 仍是 Phase 1 的測試憑證，正式環境應改由 Step-CA 或公開 CA 簽發 |
| 靜態挑戰密碼模式 | 僅供測試；知道密碼者可申請任意 Subject，不可讓正式 RADIUS 信任該 CA |
| 測試私鑰 | `phase1_poc/certs/` 內的私鑰未加密，僅供測試，已排除於版控之外 |
| 單點故障 | RADIUS、Step-CA、Webhook 都在同一台 NAS；NAS 停機時新連線無法驗證 |

## 相關文件

- `entra-vlan-extension.md`：群組延伸屬性的 ID 與建立指令
- `../phase1_poc/README.md`：Phase 1 細節與 `users` 檔問題
- `../radius/README.md`：RADIUS 容器部署與除錯
- `../stepca/README.md`：Step-CA 部署與 Intune 設定步驟
- `../phase3_webhook/README.md`：Webhook 設計、所需權限、安全設計
