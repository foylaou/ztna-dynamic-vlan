# Phase 1 POC：EAP-TLS 憑證 → 動態 VLAN

在本機容器驗證「憑證 Subject 的 `OU=VLANxx` → RADIUS 回傳 VLAN 屬性」。
這一段不需要 Fortinet 設備；設備端的切換仍需實機驗證（見最後一節）。

## 執行

```bash
.venv/bin/python phase1_poc/make_certs.py   # 產生測試 CA 與憑證到 certs/
.venv/bin/python phase1_poc/verify.py       # 建置並啟動 FreeRADIUS、跑 eapol_test
```

## 結果（2026-11-12，FreeRADIUS 3.2.10）

| 情境 | 結果 | Tunnel-Private-Group-Id |
| --- | --- | --- |
| `OU=VLAN11` 憑證 | Access-Accept | `11` |
| `OU=VLAN20` 憑證 | Access-Accept | `20` |
| 受信任 CA 簽發但無 VLAN OU | Access-Reject | - |
| 不受信任 CA 簽發（`OU=VLAN11`） | Access-Reject | - |
| 共用密鑰錯誤 | 不回應 | - |
| 來源 IP 未登錄於 `clients.conf` | 不回應 | - |

請求由一個模擬 FortiGate 的容器從獨立 IP 送出，而非 RADIUS 容器自己。

Accept 封包同時帶有 `Tunnel-Type = VLAN(13)`、`Tunnel-Medium-Type = IEEE-802(6)`。

## 重要發現：規劃書的 `users` 檔寫法無效

規劃書建議在 `users` 檔加入：

```
DEFAULT TLS-Client-Cert-Subject =~ "OU=VLAN11"
    Tunnel-Type = VLAN, ...
```

實測結果：驗證成功（Access-Accept），但**完全沒有回傳 VLAN 屬性**。
`users` 檔在 authorize 階段執行，那時 TLS 交握還沒完成，憑證屬性不存在，
規則永遠不會命中。後果是任何有效憑證都會落入連接埠的預設 VLAN。

另外 `=~ "OU=VLAN11"` 是子字串比對，`OU=VLAN110` 也會被當成 VLAN 11。

## 採用的做法：post-auth 的 unlang 規則

見 [radius/dynamic_vlan](../radius/dynamic_vlan)，放在 `policy.d/`，並在
`sites-available/default` 的 `post-auth {` 第一行呼叫 `dynamic_vlan`。

- 一條規則涵蓋所有 VLAN（用 regex 擷取數字），新增 VLAN 不必改 RADIUS。
- 憑證有效但沒有 VLAN OU → 拒絕，不會落入預設 VLAN。

## 套用到 Synology

採用 Docker 容器部署，步驟見 [radius/README.md](../radius/README.md)。

## 尚未驗證（需要實機）

以下 FortiOS 設定為參考寫法，未在設備上測試，指令可能因版本而異：

- **FortiAP / SSID**：`config wireless-controller vap` 內
  `set security wpa2-only-enterprise`、`set auth radius`、
  `set radius-server <名稱>`、`set dynamic-vlan enable`，
  並在該 SSID 介面下建立對應 VLAN ID 的子介面。
- **FortiSwitch**：`config switch-controller security-policy 802-1X` 內
  `set security-mode 802.1X-mac-based`、指定 RADIUS 使用者群組、
  啟用 `guest-vlan` / `auth-fail-vlan`；連接埠的 allowed VLANs 要包含所有動態 VLAN。
- 要確認的現象：設備收到 `Tunnel-Private-Group-Id` 後，用戶端是否拿到該 VLAN 的 IP。

## 安全注意

- `certs/` 內的私鑰未加密，僅供測試。
- VLAN 完全取決於憑證 Subject，因此 CA 必須自行決定 OU，
  不能照抄用戶端 CSR 送來的 Subject（Phase 2 設定 Step-CA 時要確認）。
