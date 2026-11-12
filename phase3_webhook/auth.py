"""取得 Microsoft 存取權杖。

來源優先順序：
1. GRAPH_TOKEN 環境變數（僅限 Graph，測試用）
2. AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET（應用程式憑證，正式環境）
3. 本機已登入的 Azure CLI（僅限 Graph，開發用）
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

import requests

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
# 與微軟官方範例一致：資源網址後接 "/.default"，因此有兩個斜線
INTUNE_SCOPE = "https://api.manage.microsoft.com//.default"



def _load_local_env() -> None:
    """載入專案根目錄的 .env（若存在）。已設定的環境變數優先。"""
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.split(" #")[0].strip())


_load_local_env()

_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


class AuthError(Exception):
    pass


def _client_credentials(scope: str) -> str:
    tenant = os.environ["AZURE_TENANT_ID"]
    with _lock:
        token, expires = _cache.get(scope, ("", 0.0))
        if token and time.time() < expires - 300:
            return token
        try:
            response = requests.post(
                f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": os.environ["AZURE_CLIENT_ID"],
                    "client_secret": os.environ["AZURE_CLIENT_SECRET"],
                    "scope": scope,
                },
                timeout=15,
            )
        except requests.RequestException as error:
            raise AuthError(f"無法連線登入端點：{error}") from error
        body = response.json() if response.content else {}
        if not response.ok:
            raise AuthError(f"取得權杖失敗：{body.get('error_description', response.text)}")
        _cache[scope] = (body["access_token"], time.time() + int(body["expires_in"]))
        return body["access_token"]


def has_client_credentials() -> bool:
    return all(os.environ.get(k) for k in
               ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"))


def get_token(scope: str = GRAPH_SCOPE) -> str:
    if scope == GRAPH_SCOPE and os.environ.get("GRAPH_TOKEN"):
        return os.environ["GRAPH_TOKEN"]
    if has_client_credentials():
        return _client_credentials(scope)
    if scope != GRAPH_SCOPE:
        raise AuthError("需要設定 AZURE_TENANT_ID、AZURE_CLIENT_ID、AZURE_CLIENT_SECRET")
    try:
        result = subprocess.run(
            ["az", "account", "get-access-token", "--resource-type", "ms-graph",
             "--query", "accessToken", "-o", "tsv"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise AuthError(f"沒有可用的憑證，且無法向 Azure CLI 取得權杖：{error}") from error
    return result.stdout.strip()
