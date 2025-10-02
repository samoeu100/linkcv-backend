import requests
import json
import time

BASE = "http://127.0.0.1:5000"
WEBHOOK_SECRET = "YVloxauSkgDd7GjJQRrK0rfNqyuOWfT"
CPF = "12345678900"
LINKEDIN_URL = "https://linkedin.com/in/teste-usuario"


def pretty(resp):
    try:
        return json.dumps(resp.json(), indent=2, ensure_ascii=False)
    except Exception:
        return resp.text


def test_health():
    r = requests.get(f"{BASE}/healthz")
    print("\n--- Health ---")
    print("Status:", r.status_code, pretty(r))


def test_generate_without_payment():
    payload = {"cpf": CPF, "resume_token": "inexistente", "linkedin_url": LINKEDIN_URL}
    r = requests.post(f"{BASE}/api/generate", json=payload)
    print("\n--- Generate sem pagamento ---")
    print("Status:", r.status_code, pretty(r))


def test_payment(tx_id):
    payload = {
        "event": "billing.paid",
        "data": {
            "billing": {
                "id": tx_id,
                "paidAmount": 990,
                "status": "paid",
                "customer": {"metadata": {"taxId": CPF}},
            }
        },
    }
    r = requests.post(f"{BASE}/webhook/payment?webhookSecret={WEBHOOK_SECRET}", json=payload)
    print("\n--- Payment ---")
    print("Status:", r.status_code, pretty(r))
    return r.json().get("resume_token")


def test_generate(cpf, token, linkedin_url, label="Generate"):
    payload = {"cpf": cpf, "resume_token": token, "linkedin_url": linkedin_url}
    r = requests.post(f"{BASE}/api/generate", json=payload)
    print(f"\n--- {label} ---")
    print("Status:", r.status_code, pretty(r))


def test_dashboard(cpf):
    r = requests.get(f"{BASE}/api/dashboard?cpf={cpf}")
    print("\n--- Dashboard ---")
    print("Status:", r.status_code, pretty(r))


if __name__ == "__main__":
    test_health()

    # 1) sem pagamento
    test_generate_without_payment()

    # 2) pagamento válido → gera token
    token = test_payment("tx_test_1")

    # 3) primeira geração
    test_generate(CPF, token, LINKEDIN_URL, "Generate 1ª vez")

    # 4) segunda geração (cache)
    test_generate(CPF, token, LINKEDIN_URL, "Generate 2ª vez (cache)")

    # 5) terceira geração (limite excedido)
    test_generate(CPF, token, LINKEDIN_URL, "Generate 3ª vez (limite excedido)")

    # 6) token inválido
    test_generate(CPF, "token_fake", LINKEDIN_URL, "Generate token inválido")

    # 7) token expirado (simulado → força delay)
    print("\n--- Simulando expiração ---")
    time.sleep(1)  # só pra simular fluxo
    test_generate(CPF, token, LINKEDIN_URL, "Generate token expirado (simulado)")

    # 8) dashboard final
    test_dashboard(CPF)
