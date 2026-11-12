"""由使用者 UPN 查出所屬 Entra 群組的 vlan 延伸屬性。

用法：
    python phase3_webhook/vlan_lookup.py user@example.com

權杖來源見 auth.py（GRAPH_TOKEN、應用程式憑證或已登入的 Azure CLI）。
結束碼：0 成功、1 找不到 VLAN、2 多個群組的 VLAN 互相衝突。
"""

from __future__ import annotations

import os
import sys
import urllib.parse

import requests

from auth import AuthError, get_token

GRAPH = "https://graph.microsoft.com/v1.0"
# 群組的目錄延伸屬性名稱，格式：extension_<appId 去連字號>_vlan
VLAN_ATTR = os.environ.get("VLAN_ATTR", "")


class VlanLookupError(Exception):
    """查不到 VLAN 或結果不唯一。"""

    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


def graph_get(url: str, token: str) -> dict:
    try:
        response = requests.get(url, timeout=15, headers={
            "Authorization": f"Bearer {token}",
            # 對 transitiveMemberOf 做型別轉換屬於進階查詢，需要這個標頭與 $count
            "ConsistencyLevel": "eventual",
        })
    except requests.RequestException as error:
        raise VlanLookupError(f"無法連線 Graph API：{error}", 1) from error
    if not response.ok:
        try:
            detail = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            detail = response.text
        raise VlanLookupError(f"Graph API {response.status_code}：{detail}", 1)
    return response.json()


def groups_with_vlan(upn: str, token: str) -> list[dict]:
    """回傳使用者（含巢狀）所屬群組中，有設定 vlan 的那些。"""
    query = urllib.parse.urlencode({
        "$select": f"id,displayName,{VLAN_ATTR}",
        "$count": "true",
    })
    url = (f"{GRAPH}/users/{urllib.parse.quote(upn)}"
           f"/transitiveMemberOf/microsoft.graph.group?{query}")
    groups: list[dict] = []
    while url:
        page = graph_get(url, token)
        groups += [g for g in page["value"] if g.get(VLAN_ATTR) is not None]
        url = page.get("@odata.nextLink")
    return groups


def resolve_vlan(upn: str, token: str | None = None) -> int:
    if not VLAN_ATTR:
        raise VlanLookupError("未設定 VLAN_ATTR（群組的 vlan 延伸屬性名稱）", 1)
    groups = groups_with_vlan(upn, token or get_token())
    vlans = {g[VLAN_ATTR] for g in groups}
    if not vlans:
        raise VlanLookupError(f"{upn} 不屬於任何設有 vlan 的群組", 1)
    if len(vlans) > 1:
        names = ", ".join(f"{g['displayName']}={g[VLAN_ATTR]}" for g in groups)
        raise VlanLookupError(f"{upn} 的 VLAN 衝突：{names}", 2)
    return vlans.pop()


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 64
    upn = sys.argv[1]
    try:
        vlan = resolve_vlan(upn)
    except VlanLookupError as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return error.exit_code
    except AuthError as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1
    print(f"CN={upn},OU=VLAN{vlan}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
