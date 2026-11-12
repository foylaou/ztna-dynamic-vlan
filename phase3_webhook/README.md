# Webhook：Intune 挑戰碼驗證 + Entra 群組決定 VLAN

Step-CA 收到 SCEP 申請時呼叫這支服務。它做兩件事：

1. 驗證挑戰碼（`intune` 模式呼叫 Intune 驗證 API；`static` 模式比對固定密碼，僅限測試）。
2. 以 CSR 的 CN（使用者 UPN）查 Entra 群組的 `vlan` 延伸屬性，回傳給 Step-CA，
   由範本寫成憑證的 `OU=VLANxx`。**CSR 自帶的 OU 一律忽略。**

| 檔案 | 用途 |
| --- | --- |
| `app.py` | FastAPI 服務：`/hook/<token>/challenge`、`/hook/<token>/notify`、`/healthz` |
| `intune.py` | Intune SCEP 驗證 API 用戶端（依微軟官方範例實作） |
| `vlan_lookup.py` | UPN → 群組 vlan；也可單獨當指令用 |
| `auth.py` | 取得 Microsoft 權杖（應用程式憑證／Azure CLI） |
| `verify.py` | 本機端到端測試 |

服務由 `../stepca/docker-compose.yml` 的 `webhook` 服務啟動，不對外開放連接埠。

## 本機驗證結果（2026-11-12）

`python phase3_webhook/verify.py`，查詢的是真實測試租用戶（唯讀）：

| 情境 | 結果 |
| --- | --- |
| 使用者 A（Vlan 20 群組），CSR 填 `OU=VLAN99` | 憑證 `OU=VLAN20` → RADIUS Accept，VLAN 20 |
| 使用者 B（Vlan 10 群組），CSR 填 `OU=VLAN99` | 憑證 `OU=VLAN10` → RADIUS Accept，VLAN 10 |
| 使用者 C（不在任何 VLAN 群組） | 拒絕簽發 |
| 挑戰碼錯誤 | 拒絕簽發 |

## 尚未驗證

- **Intune 動態挑戰碼**（`intune.py`）：端點、欄位與標頭照微軟官方 Java 範例實作，
  但沒有對真實 Intune 跑過——需要一台已註冊的裝置才能取得真的挑戰碼。
- **應用程式憑證登入**：測試用的是 Azure CLI 的使用者權杖（`GRAPH_TOKEN`），
  `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` 這條路徑還沒實際走過。

## 正式啟用前要在 Entra 做的事

替一個應用程式註冊（可沿用 `ztna-vlan-attributes`）加上應用程式權限並由管理員同意：

| API | 權限 | 用途 |
| --- | --- | --- |
| Microsoft Graph | `User.Read.All`、`GroupMember.Read.All` | 查使用者所屬群組與 vlan 屬性 |
| Microsoft Graph | `Application.Read.All` | 找出 Intune 驗證服務的端點 |
| Intune | `scep_challenge_provider` | 驗證 SCEP 挑戰碼 |

再建立用戶端密碼，填入 `stepca/.env` 的 `AZURE_TENANT_ID`、`AZURE_CLIENT_ID`、
`AZURE_CLIENT_SECRET`，並把 `CHALLENGE_MODE` 設為 `intune`。

## 安全設計

- Step-CA 設定檔無法保存 Webhook 簽章密鑰，因此以網址中的隨機 `WEBHOOK_TOKEN`
  加上「只在 compose 內部網路可連」來保護；權杖錯誤一律回 404。
  若改用 Step-CA 管理 API 建立 webhook，可設定 `WEBHOOK_SECRET` 啟用 HMAC 簽章檢查。
- Webhook 的 TLS 憑證由 Step-CA 自己簽發（`stepca/init.sh`），有效期一年，到期需重簽。
- 使用者屬於多個 vlan 值不同的群組時拒絕簽發，不會任選其一。
- 查不到 VLAN、Graph 或 Intune 無法連線時一律拒絕簽發。
