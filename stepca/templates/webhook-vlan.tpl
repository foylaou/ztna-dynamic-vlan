{
	"subject": {
		"commonName": {{ toJson .Subject.CommonName }},
		"organizationalUnit": {{ toJson (printf "VLAN%v" .Webhooks.intune.vlan) }}
	},
	"sans": {{ toJson .SANs }},
	"keyUsage": ["digitalSignature", "keyEncipherment"],
	"extKeyUsage": ["clientAuth"]
}
