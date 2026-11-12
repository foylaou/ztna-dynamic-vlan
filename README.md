# ztna-dynamic-vlan

實驗性專案：驗證「EAP-TLS 裝置憑證 + 動態 VLAN」架構。

## 驗證目標

以憑證 Subject 的 `OU=VLANxx` 取代帳號密碼，讓 RADIUS 回傳 VLAN 屬性，
FortiGate / FortiAP / FortiSwitch 依此動態切 VLAN；VLAN 歸屬只在 Entra ID 維護。

```
Entra ID 群組 → Intune (SCEP) → Step-CA 核發 CN=user, OU=VLAN11
  → 裝置 802.1X (EAP-TLS) → Synology RADIUS 解析 OU
  → Tunnel-Private-Group-Id="11" → FortiGate 切入 VLAN 11
```

## 目錄

| 目錄 | 用途 |
| --- | --- |
| `phase1_poc/` | 測試憑證產生（`make_certs.py`）與本機端到端驗證（`verify.py`） |
| `radius/` | 可部署的 FreeRADIUS 容器（EAP-TLS + 動態 VLAN），供 POC 與 Synology 使用 |
| `stepca/` | 可部署的 Step-CA 容器（SCEP 憑證機構） |
| `phase2_stepca/` | Step-CA → RADIUS 的本機端到端驗證（`verify.py`） |
| `phase3_webhook/` | Webhook 服務：驗證 SCEP 挑戰碼、查 Entra 群組 vlan 並注入憑證 OU |
| `tests/` | 測試 |
| `docs/` | 架構文件、設定筆記 |

## 環境

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.14，虛擬環境在 `.venv/`。

查詢真實租用戶的指令與 `phase3_webhook/verify.py` 需要專案根目錄的 `.env`，
範本見 `.env.example`。整體驗證結果與待測項目見 [docs/poc-report.md](docs/poc-report.md)。

## 注意

私鑰、憑證、`.env` 已列入 `.gitignore`，請勿提交任何金鑰或 Graph API 憑證。
