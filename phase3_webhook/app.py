"""Step-CA 的 SCEP Webhook：驗證挑戰碼，並由 Entra 群組決定憑證的 VLAN。

端點（<token> 為 WEBHOOK_TOKEN，Step-CA 設定檔內的網址要帶同一個值）：
    POST /hook/<token>/challenge   Step-CA 的 SCEPCHALLENGE webhook
    POST /hook/<token>/notify      Step-CA 的 NOTIFYING webhook（回報 Intune）
    GET  /healthz

環境變數：
    WEBHOOK_TOKEN      必填，網址中的密鑰
    CHALLENGE_MODE     intune（預設）或 static
    STATIC_CHALLENGE   CHALLENGE_MODE=static 時比對的固定挑戰密碼（僅限測試）
    WEBHOOK_SECRET     選填；透過 Step-CA 管理 API 建立 webhook 時的 base64 簽章密鑰
    其餘見 auth.py 與 vlan_lookup.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, HTTPException, Request

from auth import AuthError
from intune import IntuneError, IntuneScepClient
from vlan_lookup import VlanLookupError, resolve_vlan

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("webhook")

TOKEN = os.environ["WEBHOOK_TOKEN"]
MODE = os.environ.get("CHALLENGE_MODE", "intune")
STATIC_CHALLENGE = os.environ.get("STATIC_CHALLENGE", "")
SIGNING_SECRET = os.environ.get("WEBHOOK_SECRET", "")
E_FAIL = 0x80004005

if MODE not in ("intune", "static"):
    raise RuntimeError(f"CHALLENGE_MODE 必須是 intune 或 static，目前是 {MODE!r}")
if MODE == "static" and len(STATIC_CHALLENGE) < 16:
    raise RuntimeError("CHALLENGE_MODE=static 需要至少 16 字元的 STATIC_CHALLENGE")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
intune = IntuneScepClient()


def deny(code: str, message: str) -> dict:
    log.warning("拒絕：%s（%s）", message, code)
    return {"allow": False, "error": {"code": code, "message": message}}


async def read_body(request: Request, token: str) -> dict:
    if not hmac.compare_digest(token, TOKEN):
        raise HTTPException(status_code=404)
    raw = await request.body()
    if SIGNING_SECRET:
        expected = hmac.new(base64.b64decode(SIGNING_SECRET), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get("X-Smallstep-Signature", "")):
            raise HTTPException(status_code=401)
    return await request.json()


def csr_of(body: dict) -> tuple[str, x509.CertificateSigningRequest]:
    csr_b64 = body["x509CertificateRequest"]["raw"]
    return csr_b64, x509.load_der_x509_csr(base64.b64decode(csr_b64))


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "mode": MODE}


@app.post("/hook/{token}/challenge")
async def challenge(token: str, request: Request) -> dict:
    body = await read_body(request, token)
    transaction_id = body.get("scepTransactionID", "")
    try:
        csr_b64, csr = csr_of(body)
        names = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    except (KeyError, ValueError):
        return deny("bad-request", "請求中沒有可解析的 CSR")
    if len(names) != 1 or "@" not in str(names[0].value):
        return deny("bad-subject", "CSR 的 CN 必須是使用者的 UPN")
    upn = str(names[0].value)

    # 1. 驗證挑戰碼
    if MODE == "static":
        if not hmac.compare_digest(body.get("scepChallenge", ""), STATIC_CHALLENGE):
            return deny("bad-challenge", f"{upn} 的挑戰密碼不正確")
    else:
        try:
            intune.validate(transaction_id, csr_b64)
        except (IntuneError, AuthError) as error:
            return deny("intune-rejected", f"{upn} 未通過 Intune 驗證：{error}")

    # 2. 由 Entra 群組決定 VLAN（CSR 自帶的 OU 一律忽略）
    try:
        vlan = resolve_vlan(upn)
    except (VlanLookupError, AuthError) as error:
        return deny("no-vlan", str(error))

    log.info("允許：%s → VLAN %s（transaction %s）", upn, vlan, transaction_id)
    return {"allow": True, "data": {"vlan": vlan, "upn": upn}}


@app.post("/hook/{token}/notify")
async def notify(token: str, request: Request) -> dict:
    """簽發結果通知。回報 Intune 失敗只記錄，不影響已完成的簽發。"""
    body = await read_body(request, token)
    transaction_id = body.get("scepTransactionID", "")
    issued = body.get("x509Certificate")
    if issued:
        log.info("已簽發（transaction %s）", transaction_id)
    else:
        log.warning("簽發失敗（transaction %s）：%s", transaction_id,
                    body.get("scepErrorDescription"))
    if MODE != "intune":
        return {"allow": True}
    try:
        csr_b64, _ = csr_of(body)
        if issued:
            cert = x509.load_der_x509_certificate(base64.b64decode(issued["raw"]))
            intune.notify_success(
                transaction_id, csr_b64,
                thumbprint=cert.fingerprint(hashes.SHA1()).hex().upper(),
                serial=format(cert.serial_number, "X"),
                expires_utc=cert.not_valid_after_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                issuer=cert.issuer.rfc4514_string(),
            )
        else:
            intune.notify_failure(transaction_id, csr_b64, E_FAIL,
                                  body.get("scepErrorDescription") or "SCEP request failed")
    except (IntuneError, AuthError, KeyError, ValueError) as error:
        log.error("回報 Intune 失敗（transaction %s）：%s", transaction_id, error)
    return {"allow": True}
