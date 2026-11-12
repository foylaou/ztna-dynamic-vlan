"""在本機容器實測 EAP-TLS 動態 VLAN。

用法（需先執行 make_certs.py，且 Docker 已啟動）：
    python phase1_poc/verify.py

流程：建置 radius/ 映像檔 → 建立隔離的測試網路 → 啟動 FreeRADIUS →
由「模擬 FortiGate」容器用 eapol_test 以各張憑證做 802.1X 驗證 →
檢查 Access-Accept 內的 Tunnel 屬性 → 清除容器與網路。
全部符合預期時結束碼為 0。
"""

from __future__ import annotations

import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
CERTS = HERE / "certs"
RADIUS_DIR = HERE.parent / "radius"
IMAGE = "ztna-radius"
NETWORK = "ztna-poc-net"
SUBNET = "172.30.99.0/24"
RADIUS = ("ztna-poc-radius", "172.30.99.10")
NAS = ("ztna-poc-nas", "172.30.99.20")            # 模擬 FortiGate，已登錄於 clients.conf
STRANGER = ("ztna-poc-stranger", "172.30.99.30")  # 未登錄的來源
SECRET = secrets.token_hex(16)

# (說明, 來源容器, 憑證檔名, 共用密鑰, 預期結果, 預期 VLAN)
CASES = [
    ("OU=VLAN11", NAS[0], "client-vlan11", SECRET, "accept", "11"),
    ("OU=VLAN20", NAS[0], "client-vlan20", SECRET, "accept", "20"),
    ("無 VLAN OU", NAS[0], "client-noou", SECRET, "reject", None),
    ("不受信任的 CA", NAS[0], "client-rogue", SECRET, "reject", None),
    ("共用密鑰錯誤", NAS[0], "client-vlan11", "wrong-secret", "no-reply", None),
    ("未登錄的來源 IP", STRANGER[0], "client-vlan11", SECRET, "no-reply", None),
]

EAPOL_CONF = """network={{
  key_mgmt=IEEE8021X
  eap=TLS
  identity="poc-test"
  ca_cert="/etc/raddb/certs/ca.pem"
  client_cert="/etc/raddb/certs/{stem}.pem"
  private_key="/etc/raddb/certs/{stem}.key"
}}
"""


def run(*args: str, stdin: str | None = None, check: bool = True) -> str:
    result = subprocess.run(args, input=stdin, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}\n{result.stdout}{result.stderr}")
    return result.stdout + result.stderr


def attribute(output: str, number: int) -> str | None:
    """取出 eapol_test 傾印的最後一個指定 RADIUS 屬性（十六進位字串）。"""
    found = re.findall(rf"Attribute {number} \([^)]*\) length=\d+\s+Value: (\S+)", output)
    return found[-1] if found else None


def test_case(source: str, stem: str, secret: str) -> tuple[str, str | None, bool]:
    run("docker", "exec", "-i", source, "sh", "-c", "cat > /tmp/eapol.conf",
        stdin=EAPOL_CONF.format(stem=stem))
    output = run("docker", "exec", source, "eapol_test", "-c", "/tmp/eapol.conf",
                 "-a", RADIUS[1], "-s", secret, "-t", "6", check=False)
    if output.strip().endswith("SUCCESS"):
        result = "accept"
    elif "Access-Reject" in output or "EAP-Failure" in output:
        result = "reject"
    else:
        result = "no-reply"
    vlan_hex = attribute(output, 81) if result == "accept" else None
    vlan = bytes.fromhex(vlan_hex).decode() if vlan_hex else None
    # Tunnel-Type 13 = VLAN、Tunnel-Medium-Type 6 = IEEE-802
    tunnel_ok = attribute(output, 64) == "0000000d" and attribute(output, 65) == "00000006"
    return result, vlan, tunnel_ok


def cleanup() -> None:
    run("docker", "rm", "-f", RADIUS[0], NAS[0], STRANGER[0], check=False)
    run("docker", "network", "rm", NETWORK, check=False)


def start(container: tuple[str, str], *command: str, env: dict[str, str] | None = None) -> None:
    name, ip = container
    args = ["docker", "run", "-d", "--name", name, "--network", NETWORK, "--ip", ip,
            "-v", f"{CERTS}:/etc/raddb/certs:ro"]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    run(*args, IMAGE, *command)


def main() -> int:
    if not (CERTS / "ca.pem").exists():
        print("找不到測試憑證，請先執行 make_certs.py", file=sys.stderr)
        return 1
    run("docker", "build", "-q", "-t", IMAGE, str(RADIUS_DIR))
    cleanup()
    failures = 0
    try:
        run("docker", "network", "create", "--subnet", SUBNET, NETWORK)
        start(RADIUS, env={"FORTIGATE_IP": NAS[1], "RADIUS_SECRET": SECRET})
        start(NAS, "sleep", "600")
        start(STRANGER, "sleep", "600")
        time.sleep(4)
        if "true" not in run("docker", "inspect", "-f", "{{.State.Running}}", RADIUS[0]):
            print(run("docker", "logs", "--tail", "30", RADIUS[0], check=False), file=sys.stderr)
            return 1
        print(f"{'情境':<18}{'結果':<12}{'VLAN':<8}判定")
        for label, source, stem, secret, want_result, want_vlan in CASES:
            result, vlan, tunnel_ok = test_case(source, stem, secret)
            ok = result == want_result and vlan == want_vlan
            if want_result == "accept":
                ok = ok and tunnel_ok
            failures += not ok
            print(f"{label:<18}{result:<12}{vlan or '-':<8}{'通過' if ok else '不符預期'}")
    finally:
        cleanup()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
