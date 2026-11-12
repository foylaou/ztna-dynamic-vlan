"""Intune 第三方 CA 的 SCEP 驗證 API 用戶端。

依微軟官方範例實作（microsoft/Intune-Resource-Access，src/CsrValidation）：
- 以 Graph 查詢 Intune 服務主體的端點，找出 ScepRequestValidationFEService
- POST {端點}/ScepActions/validateRequest 驗證 CSR 內的動態挑戰碼
- 簽發後以 successNotification / failureNotification 回報

應用程式需要 Intune 的 scep_challenge_provider 與 Graph 的 Application.Read.All
應用程式權限。尚未以真實 Intune 裝置測試。
"""

from __future__ import annotations

import threading
import time
import uuid

import requests

from auth import GRAPH_SCOPE, INTUNE_SCOPE, get_token

INTUNE_APP_ID = "0000000a-0000-0000-c000-000000000000"
SERVICE_NAME = "ScepRequestValidationFEService"
API_VERSION = "2018-02-20"
CALLER_INFO = "ztna-dynamic-vlan-webhook/0.1"
ENDPOINT_TTL = 3600


class IntuneError(Exception):
    """Intune 拒絕請求，或無法完成驗證。"""


class IntuneScepClient:
    def __init__(self) -> None:
        self._endpoint: str | None = None
        self._endpoint_expires = 0.0
        self._lock = threading.Lock()

    def _service_endpoint(self) -> str:
        with self._lock:
            if self._endpoint and time.time() < self._endpoint_expires:
                return self._endpoint
            response = requests.get(
                f"https://graph.microsoft.com/v1.0/servicePrincipals/appId={INTUNE_APP_ID}/endpoints",
                headers={"Authorization": f"Bearer {get_token(GRAPH_SCOPE)}",
                         "client-request-id": str(uuid.uuid4())},
                timeout=15,
            )
            if not response.ok:
                raise IntuneError(f"查詢 Intune 服務端點失敗（{response.status_code}）：{response.text}")
            for item in response.json().get("value", []):
                if item.get("providerName", "").lower() == SERVICE_NAME.lower():
                    self._endpoint = item["uri"].rstrip("/")
                    self._endpoint_expires = time.time() + ENDPOINT_TTL
                    return self._endpoint
            raise IntuneError(f"找不到 {SERVICE_NAME} 端點，租用戶可能沒有 Intune")

    def _post(self, action: str, wrapper: str, payload: dict) -> None:
        payload = {**payload, "callerInfo": CALLER_INFO}
        try:
            response = requests.post(
                f"{self._service_endpoint()}/ScepActions/{action}",
                json={wrapper: payload},
                headers={
                    "Authorization": f"Bearer {get_token(INTUNE_SCOPE)}",
                    "client-request-id": str(uuid.uuid4()),
                    "api-version": API_VERSION,
                    "UserAgent": CALLER_INFO,
                },
                timeout=20,
            )
        except requests.RequestException as error:
            raise IntuneError(f"無法連線 Intune：{error}") from error
        if not response.ok:
            raise IntuneError(f"Intune 回應 {response.status_code}：{response.text}")
        result = response.json()
        if result.get("code") != "Success":
            raise IntuneError(f"{result.get('code')}：{result.get('errorDescription')}")

    def validate(self, transaction_id: str, csr_b64: str) -> None:
        """驗證 CSR（含動態挑戰碼）。不通過時丟出 IntuneError。"""
        self._post("validateRequest", "request",
                   {"transactionId": transaction_id, "certificateRequest": csr_b64})

    def notify_success(self, transaction_id: str, csr_b64: str, thumbprint: str,
                       serial: str, expires_utc: str, issuer: str) -> None:
        self._post("successNotification", "notification", {
            "transactionId": transaction_id,
            "certificateRequest": csr_b64,
            "certificateThumbprint": thumbprint,
            "certificateSerialNumber": serial,
            "certificateExpirationDateUtc": expires_utc,
            "issuingCertificateAuthority": issuer,
        })

    def notify_failure(self, transaction_id: str, csr_b64: str, hresult: int,
                       description: str) -> None:
        self._post("failureNotification", "notification", {
            "transactionId": transaction_id,
            "certificateRequest": csr_b64,
            "hResult": hresult,
            "errorDescription": description,
        })
