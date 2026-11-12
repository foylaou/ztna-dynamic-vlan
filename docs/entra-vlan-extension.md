# Entra 群組 VLAN 目錄延伸屬性

2026-11-12 於測試租用戶建立並驗證。以下 ID 已替換為預留位置；真實值請自行記錄在不進版控的地方。

| 項目 | 值 |
| --- | --- |
| 租用戶 ID | `<tenant-id>` |
| 應用程式註冊 | `ztna-vlan-attributes` |
| appId | `<app-id>` |
| 應用程式物件 ID | `<app-object-id>` |
| 服務主體 ID | `<service-principal-id>` |
| 屬性名稱 | `extension_<appId 去連字號>_vlan`（Integer，目標 Group） |

| 群組 | 物件 ID | vlan |
| --- | --- | --- |
| Vlan 10 | `<group-id-vlan10>` | 10 |
| Vlan 20 | `<group-id-vlan20>` | 20 |

## 建立步驟（`az login` 後）

```bash
# 1. 應用程式註冊
az rest --method POST --url https://graph.microsoft.com/v1.0/applications \
  --body '{"displayName":"ztna-vlan-attributes","signInAudience":"AzureADMyOrg"}'

# 2. 定義延伸屬性（{OID} 為應用程式物件 ID）
az rest --method POST --url https://graph.microsoft.com/v1.0/applications/{OID}/extensionProperties \
  --body '{"name":"vlan","dataType":"Integer","targetObjects":["Group"]}'

# 3. 建立服務主體 —— 沒有這步，寫入群組會回 "extension properties are not available"
az rest --method POST --url https://graph.microsoft.com/v1.0/servicePrincipals \
  --body '{"appId":"{APPID}"}'

# 4. 寫入群組
az rest --method PATCH --url https://graph.microsoft.com/v1.0/groups/{GROUP_ID} \
  --body '{"extension_{APPID去連字號}_vlan":20}'

# 5. 讀回（必須用 $select 明確指定，預設回應不含延伸屬性）
az rest --method GET \
  --url 'https://graph.microsoft.com/v1.0/groups?$select=displayName,id,extension_{APPID去連字號}_vlan'
```

## 注意

- Entra / Intune 入口網站看不到也改不了這個屬性，只能透過 Graph API。
- 刪除 `ztna-vlan-attributes` 應用程式註冊會連同屬性定義與所有群組上的值一起消失。
