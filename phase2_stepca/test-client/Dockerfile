# 測試用戶端：scepclient（向 Step-CA 申請憑證）+ eapol_test（做 802.1X 驗證）
FROM golang:1.25 AS build
RUN CGO_ENABLED=0 go install github.com/micromdm/scep/v2/cmd/scepclient@latest

FROM ztna-radius
COPY --from=build /go/bin/scepclient /usr/local/bin/scepclient
