#!/bin/sh
# 一次性初始化 Step-CA：建立根 CA、RSA 中繼 CA 與含 SCEP 佈建器的 ca.json。
# 在 step-ca 映像檔內執行（docker compose run --rm init）。已初始化則不做任何事。
set -eu

S=/home/step
if [ -f "$S/config/ca.json" ]; then
	echo "已初始化（$S/config/ca.json 已存在），略過。"
	exit 0
fi

: "${CA_NAME:?需要 CA_NAME}"
: "${CA_DNS:?需要 CA_DNS}"
SCEP_PROVISIONER="${SCEP_PROVISIONER:-intune}"
WEBHOOK_TOKEN="${WEBHOOK_TOKEN:-}"
SCEP_CHALLENGE="${SCEP_CHALLENGE:-}"
if [ -z "$WEBHOOK_TOKEN" ] && [ -z "$SCEP_CHALLENGE" ]; then
	echo "需要 WEBHOOK_TOKEN（Webhook 模式）或 SCEP_CHALLENGE（靜態挑戰密碼模式）" >&2
	exit 1
fi

umask 077
mkdir -p "$S/certs" "$S/secrets" "$S/config" "$S/db" "$S/templates"
head -c 32 /dev/urandom | base64 > "$S/secrets/password"

# SCEP 需要 RSA 金鑰來解密用戶端請求，因此中繼 CA 使用 RSA。
step certificate create "$CA_NAME Root CA" \
	"$S/certs/root_ca.crt" "$S/secrets/root_ca_key" \
	--profile root-ca --kty RSA --size 4096 --not-after 87600h \
	--password-file "$S/secrets/password"

step certificate create "$CA_NAME Intermediate CA" \
	"$S/certs/intermediate_ca.crt" "$S/secrets/intermediate_ca_key" \
	--profile intermediate-ca --kty RSA --size 2048 --not-after 43800h \
	--ca "$S/certs/root_ca.crt" --ca-key "$S/secrets/root_ca_key" \
	--ca-password-file "$S/secrets/password" \
	--password-file "$S/secrets/password"

cp /init/templates/*.tpl "$S/templates/"
chmod 644 "$S/certs/"*.crt "$S/templates/"*.tpl

# Webhook 模式：挑戰碼交給 Webhook 驗證，OU 由 Webhook 回傳的 vlan 決定。
# 靜態模式：比對固定挑戰密碼，Subject 沿用 CSR（僅限測試）。
if [ -n "$WEBHOOK_TOKEN" ]; then
	# Webhook 必須是 HTTPS：由本 CA 簽發它的伺服器憑證，放到與 Webhook 共用的磁碟區
	step certificate create webhook /webhook-tls/webhook.crt /webhook-tls/webhook.key \
		--profile leaf --san webhook --not-after 8760h --bundle \
		--ca "$S/certs/intermediate_ca.crt" --ca-key "$S/secrets/intermediate_ca_key" \
		--ca-password-file "$S/secrets/password" --no-password --insecure
fi

jq -n \
	--arg dns "$CA_DNS" \
	--arg name "$SCEP_PROVISIONER" \
	--arg challenge "$SCEP_CHALLENGE" \
	--arg token "$WEBHOOK_TOKEN" \
	'{
		root: "/home/step/certs/root_ca.crt",
		crt: "/home/step/certs/intermediate_ca.crt",
		key: "/home/step/secrets/intermediate_ca_key",
		address: ":9000",
		insecureAddress: ":8080",
		dnsNames: [$dns, "localhost"],
		logger: {format: "text"},
		db: {type: "badgerv2", dataSource: "/home/step/db"},
		authority: {
			provisioners: [
				{
					type: "SCEP",
					name: $name,
					minimumPublicKeyLength: 2048,
					encryptionAlgorithmIdentifier: 2,
					includeRoot: true,
					claims: {
						minTLSCertDuration: "24h",
						defaultTLSCertDuration: "2160h",
						maxTLSCertDuration: "8760h"
					}
				} + (
					if $token != "" then {
						options: {
							x509: {templateFile: "/home/step/templates/webhook-vlan.tpl"},
							webhooks: [
								{
									name: "intune",
									kind: "SCEPCHALLENGE",
									certType: "X509",
									url: ("https://webhook:8443/hook/" + $token + "/challenge")
								},
								{
									name: "intune-notify",
									kind: "NOTIFYING",
									certType: "X509",
									url: ("https://webhook:8443/hook/" + $token + "/notify")
								}
							]
						}
					} else {
						challenge: $challenge,
						options: {x509: {templateFile: "/home/step/templates/client-auth.tpl"}}
					} end
				)
			]
		},
		tls: {minVersion: 1.2, maxVersion: 1.3, renegotiation: false}
	}' > "$S/config/ca.json"

# init 以 root 執行（才能寫入共用磁碟區），把 CA 資料交還給 step 使用者
chown -R 1000:1000 "$S"

echo "初始化完成。根憑證指紋："
step certificate fingerprint "$S/certs/root_ca.crt"
