# Step-CA 的 SCEP Webhook 服務。TLS 憑證由 stepca/init.sh 產生並掛載到 /tls。
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY auth.py intune.py vlan_lookup.py app.py ./

EXPOSE 8443
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8443", \
     "--ssl-keyfile", "/tls/webhook.key", "--ssl-certfile", "/tls/webhook.crt", \
     "--no-access-log"]
