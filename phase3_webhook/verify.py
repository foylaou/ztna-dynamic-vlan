"""在本機實測 Webhook：Entra 群組的 vlan → 憑證 OU → RADIUS 回傳 VLAN。

用法（需先執行 phase1_poc/make_certs.py、Docker 已啟動、Azure CLI 已登入）：
    python phase3_webhook/verify.py

會向真實租用戶的 Graph API 查詢群組（唯讀），使用 Azure CLI 的登入權杖。
挑戰碼以 static 模式驗證；Intune 的動態挑戰碼驗證不在此測試範圍。
"""

from __future__ import annotations

import importlib.util
import os
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from auth import AuthError, get_token  # noqa: E402


def _load(path: Path, name: str):
    """依路徑載入另一個階段的 verify.py（三個階段的檔名相同，不能用一般 import）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stack = _load(HERE.parent / "phase2_stepca" / "verify.py", "phase2_verify")

# 真實帳號由專案根目錄的 .env 提供（見 .env.example）：(環境變數, 預期 VLAN；None 表示應拒絕簽發)
USERS = [("TEST_USER_VLAN20", "20"), ("TEST_USER_VLAN10", "10"), ("TEST_USER_NONE", None)]


def main() -> int:
    missing = [key for key in ("VLAN_ATTR", *(k for k, _ in USERS)) if not os.environ.get(key)]
    if missing:
        print(f"請先在專案根目錄的 .env 設定：{', '.join(missing)}", file=sys.stderr)
        return 1
    try:
        graph_token = get_token()
    except AuthError as error:
        print(f"無法取得 Graph 權杖：{error}", file=sys.stderr)
        return 1
    challenge = secrets.token_hex(16)
    stack.COMPOSE_ENV.update({
        "WEBHOOK_TOKEN": secrets.token_hex(24),
        "CHALLENGE_MODE": "static",
        "STATIC_CHALLENGE": challenge,
        "GRAPH_TOKEN": graph_token,
        "VLAN_ATTR": os.environ["VLAN_ATTR"],
    })
    rows: list[tuple[str, str, bool]] = []
    try:
        stack.start_stack("--profile", "webhook")
        for index, (key, want_vlan) in enumerate(USERS):
            upn, user = os.environ[key], f"user{index}"
            # CSR 故意填 OU=VLAN99：正確行為是忽略它，改用 Entra 群組的值
            issued = stack.enroll(user, upn, "VLAN99", challenge)
            if want_vlan is None:
                rows.append((f"{upn}（不在 VLAN 群組）",
                             "已簽發" if issued else "拒絕簽發", not issued))
                continue
            if not issued:
                rows.append((upn, "拒絕簽發", False))
                continue
            result, vlan = stack.authenticate(f"/tmp/{user}/client.pem", f"/tmp/{user}/client.key")
            rows.append((f"{upn}（CSR 填 OU=VLAN99）",
                         f"憑證 {stack.subject(user)} → RADIUS {result} VLAN {vlan or '-'}",
                         result == "accept" and vlan == want_vlan))

        issued = stack.enroll("badchallenge", os.environ[USERS[0][0]], "VLAN20", "wrong-challenge")
        rows.append(("挑戰碼錯誤", "已簽發" if issued else "拒絕簽發", not issued))
    finally:
        stack.cleanup()
    return stack.report(rows)


if __name__ == "__main__":
    sys.exit(main())
