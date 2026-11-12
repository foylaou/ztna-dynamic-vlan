"""在本機實測 Step-CA（SCEP）簽發的憑證能通過 RADIUS 並取得 VLAN。

用法（需先執行 phase1_poc/make_certs.py，且 Docker 已啟動）：
    python phase2_stepca/verify.py

流程：初始化並啟動 Step-CA → 用 scepclient 以 SCEP 申請憑證 →
啟動只信任 Step-CA 的 FreeRADIUS → 用 eapol_test 驗證並檢查 VLAN → 全部清除。
全部符合預期時結束碼為 0。
"""

from __future__ import annotations

import importlib.util
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent


def _load(path: Path, name: str):
    """依路徑載入另一個階段的 verify.py（三個階段的檔名相同，不能用一般 import）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_phase1 = _load(ROOT / "phase1_poc" / "verify.py", "phase1_verify")
EAPOL_CONF, attribute, run = _phase1.EAPOL_CONF, _phase1.attribute, _phase1.run

STEPCA_DIR = ROOT / "stepca"
POC_CERTS = ROOT / "phase1_poc" / "certs"
WORK = HERE / "work"            # 測試用暫存憑證目錄，結束時刪除
PROJECT = "ztnapoc"
NETWORK = f"{PROJECT}_default"
RADIUS, CLIENT = "ztna-poc-radius", "ztna-poc-client"
SCEP_URL = "http://stepca:8080/scep/intune"
CHALLENGE = secrets.token_hex(12)
SECRET = secrets.token_hex(16)
COMPOSE_ENV = {
    **os.environ,
    "COMPOSE_PROJECT_NAME": PROJECT,
    "CA_NAME": "POC Test",
    "CA_DNS": "stepca",
}


def compose(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", "compose", *args], cwd=STEPCA_DIR, env=COMPOSE_ENV,
                            capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)}\n{result.stdout}{result.stderr}")
    return result.stdout + result.stderr


def cleanup() -> None:
    run("docker", "rm", "-f", RADIUS, CLIENT, check=False)
    compose("--profile", "*", "down", "-v", check=False)
    shutil.rmtree(WORK, ignore_errors=True)


def container_ip(name: str) -> str:
    return run("docker", "inspect", "-f",
               "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name).strip()


def start_stack(*up_args: str) -> None:
    """初始化並啟動 Step-CA，再啟動只信任它的 RADIUS 與測試用戶端。"""
    run("docker", "build", "-q", "-t", "ztna-radius", str(ROOT / "radius"))
    run("docker", "build", "-q", "-t", CLIENT, str(HERE / "test-client"))
    cleanup()
    compose("run", "--rm", "init")
    compose(*up_args, "up", "-d", "--build")
    time.sleep(4)

    # RADIUS 只信任 Step-CA（根 + 中繼）；伺服器憑證沿用 Phase 1 的測試憑證
    WORK.mkdir()
    chain = (run("docker", "exec", "ztna-stepca", "cat", "/home/step/certs/root_ca.crt")
             + run("docker", "exec", "ztna-stepca", "cat", "/home/step/certs/intermediate_ca.crt"))
    (WORK / "ca.pem").write_text(chain)
    shutil.copy(POC_CERTS / "server.pem", WORK / "server.pem")
    shutil.copy(POC_CERTS / "ca.pem", WORK / "poc-ca.pem")
    for stem in ("client-vlan11.pem", "client-vlan11.key"):
        shutil.copy(POC_CERTS / stem, WORK / stem)

    mount = ["-v", f"{WORK}:/etc/raddb/certs:ro"]
    run("docker", "run", "-d", "--name", CLIENT, "--network", NETWORK, *mount,
        CLIENT, "sleep", "600")
    run("docker", "run", "-d", "--name", RADIUS, "--network", NETWORK, *mount,
        "-e", f"FORTIGATE_IP={container_ip(CLIENT)}", "-e", f"RADIUS_SECRET={SECRET}",
        "ztna-radius")
    time.sleep(4)


def enroll(name: str, cn: str, ou: str, challenge: str) -> bool:
    """以 SCEP 申請憑證，存到用戶端容器的 /tmp/<name>/。回傳是否成功取得憑證。"""
    script = (f"mkdir -p /tmp/{name} && cd /tmp/{name} && "
              f"scepclient -server-url {SCEP_URL} -challenge {challenge} "
              f"-cn {cn} -ou {ou} -private-key client.key -certificate client.pem "
              f">/dev/null 2>&1; test -s client.pem")
    return subprocess.run(["docker", "exec", CLIENT, "sh", "-c", script]).returncode == 0


def subject(name: str) -> str:
    return run("docker", "exec", CLIENT, "openssl", "x509", "-in", f"/tmp/{name}/client.pem",
               "-noout", "-subject").strip().removeprefix("subject=")


def authenticate(cert: str, key: str) -> tuple[str, str | None]:
    """用指定憑證向 RADIUS 做 EAP-TLS，回傳 (結果, VLAN)。"""
    conf = (EAPOL_CONF.format(stem="unused")
            .replace('ca_cert="/etc/raddb/certs/ca.pem"', 'ca_cert="/etc/raddb/certs/poc-ca.pem"')
            .replace('client_cert="/etc/raddb/certs/unused.pem"', f'client_cert="{cert}"')
            .replace('private_key="/etc/raddb/certs/unused.key"', f'private_key="{key}"'))
    run("docker", "exec", "-i", CLIENT, "sh", "-c", "cat > /tmp/eapol.conf", stdin=conf)
    output = run("docker", "exec", CLIENT, "eapol_test", "-c", "/tmp/eapol.conf",
                 "-a", container_ip(RADIUS), "-s", SECRET, "-t", "6", check=False)
    if not output.strip().endswith("SUCCESS"):
        return "reject", None
    vlan_hex = attribute(output, 81)
    return "accept", bytes.fromhex(vlan_hex).decode() if vlan_hex else None


def report(rows: list[tuple[str, str, bool]]) -> int:
    for label, outcome, ok in rows:
        print(f"{'通過' if ok else '不符預期'}  {label}：{outcome}")
    return 0 if rows and all(ok for *_, ok in rows) else 1


def main() -> int:
    if not (POC_CERTS / "server.pem").exists():
        print("找不到 RADIUS 伺服器測試憑證，請先執行 phase1_poc/make_certs.py", file=sys.stderr)
        return 1
    COMPOSE_ENV["SCEP_CHALLENGE"] = CHALLENGE
    rows: list[tuple[str, str, bool]] = []
    try:
        start_stack()
        for name, ou, want_vlan in (("vlan20", "VLAN20", "20"), ("vlan11", "VLAN11", "11")):
            issued = enroll(name, f"{name}@example.com", ou, CHALLENGE)
            result, vlan = (authenticate(f"/tmp/{name}/client.pem", f"/tmp/{name}/client.key")
                            if issued else ("no-cert", None))
            rows.append((f"SCEP 申請 OU={ou} → RADIUS",
                         f"{result} VLAN {vlan or '-'}", result == "accept" and vlan == want_vlan))

        issued = enroll("badchallenge", "eve@example.com", "VLAN20", "wrong-challenge")
        rows.append(("SCEP 挑戰密碼錯誤", "已簽發" if issued else "拒絕簽發", not issued))

        result, vlan = authenticate("/etc/raddb/certs/client-vlan11.pem",
                                    "/etc/raddb/certs/client-vlan11.key")
        rows.append(("非 Step-CA 簽發的憑證 → RADIUS", f"{result} VLAN {vlan or '-'}",
                     result == "reject"))
    finally:
        cleanup()
    return report(rows)


if __name__ == "__main__":
    sys.exit(main())
