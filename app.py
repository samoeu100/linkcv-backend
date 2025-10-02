import os
import time
import json
import secrets
import hashlib
import requests
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from dotenv import load_dotenv

# ========== Config ==========
load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")  # URL completa do Render
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "dev-webhook")

BRIGHTDATA_API_KEY = os.environ.get("BRIGHTDATA_API_KEY")
BRIGHTDATA_DATASET_ID = os.environ.get("BRIGHTDATA_DATASET_ID")
BRIGHTDATA_BASE = os.environ.get("BRIGHTDATA_BASE", "https://api.brightdata.com/datasets/v3")

CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

app = Flask(__name__)
CORS(app)
app.secret_key = SECRET_KEY

# ========== DB Helpers ==========
def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL, sslmode="require")
    return g.db

@app.teardown_appcontext
def close_db(error):
    if "db" in g:
        g.db.close()

def init_db():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id SERIAL PRIMARY KEY,
        cpf TEXT,
        transaction_id TEXT UNIQUE,
        resume_token TEXT,
        used INTEGER DEFAULT 0,
        amount INTEGER,
        status TEXT,
        created_at REAL,
        expires_at TIMESTAMP DEFAULT (NOW() + interval '3 days'),
        usage_count INTEGER DEFAULT 0,
        max_usage INTEGER DEFAULT 2
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS resumes (
        id SERIAL PRIMARY KEY,
        cpf TEXT,
        resume_json TEXT,
        linkedin_url TEXT,
        created_at REAL
    )
    """)
    db.commit()
    cur.close()

# ========== LinkedIn Extractor ==========
def extract_profile_ats_from_linkedin_url(profile_url: str):
    mock_file = os.path.join(CACHE_DIR, "profile_ats.json")
    if os.path.exists(mock_file):
        print(">>> Usando MOCK profile_ats.json em vez do BrightData")
        with open(mock_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["links"]["linkedin"] = profile_url
        return data

    try:
        endpoint = f"{BRIGHTDATA_BASE}/trigger"
        headers = {"Authorization": f"Bearer {BRIGHTDATA_API_KEY}", "Content-Type": "application/json"}
        params = {"dataset_id": BRIGHTDATA_DATASET_ID, "include_errors": "true"}
        payload = [{"url": profile_url}]
        r = requests.post(endpoint, headers=headers, params=params, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        snapshot_id = data[0]["snapshot_id"] if isinstance(data, list) else data["snapshot_id"]

        # Espera até estar pronto
        while True:
            url = f"{BRIGHTDATA_BASE}/progress/{snapshot_id}"
            r = requests.get(url, headers=headers, timeout=30)
            status = (r.json() or {}).get("status", "").lower()
            if status == "ready":
                break
            if status == "failed":
                raise RuntimeError("Coleta falhou (status=failed)")
            time.sleep(3)

        # Busca snapshot
        url = f"{BRIGHTDATA_BASE}/snapshot/{snapshot_id}"
        r = requests.get(url, headers=headers, params={"format": "json"}, timeout=60)
        r.raise_for_status()
        items = json.loads(r.text)
        return items[0]
    except Exception as e:
        print(">>> Erro BrightData:", e)
        return {"error": f"Falha na extração: {e}"}

# ========== Helpers ==========
def get_cache_path(cpf, linkedin_url):
    key = f"{cpf}_{linkedin_url}"
    hashed = hashlib.md5(key.encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{hashed}.json")

# ========== Health ==========
@app.route("/healthz")
def healthz():
    return {"status": "ok"}, 200

# ========== Webhook ==========
@app.route("/webhook/payment", methods=["POST"])
def webhook_payment():
    secret_qs = request.args.get("webhookSecret")
    if secret_qs != WEBHOOK_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json(force=True, silent=True) or {}
    if payload.get("event") != "billing.paid":
        return jsonify({"status": "ignored"}), 200

    billing = payload.get("data", {}).get("billing", {})
    customer = billing.get("customer", {})
    metadata = customer.get("metadata", {})

    cpf = metadata.get("taxId")
    tx_id = billing.get("id")
    amount = billing.get("paidAmount") or billing.get("amount", 0)
    status = billing.get("status", "UNKNOWN")

    # Considera ACTIVE como pago também
    if status.upper() in ["ACTIVE", "PAID"]:
        resume_token = secrets.token_hex(8)

        db = get_db()
        cur = db.cursor()
        try:
            cur.execute(
                """
                INSERT INTO payments (cpf, transaction_id, resume_token, amount, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (transaction_id) DO NOTHING
                """,
                (cpf, tx_id, resume_token, amount, status, time.time())
            )
            db.commit()
            print(f">>> Pagamento registrado {tx_id}, token={resume_token}")
        except Exception as e:
            print("Erro ou duplicado:", e)
        cur.close()

        return jsonify({"status": "ok", "resume_token": resume_token}), 200
    else:
        return jsonify({"error": f"Status inválido: {status}"}), 400

# ========== Generate ==========
@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json() or {}
    cpf = data.get("cpf")
    linkedin_url = data.get("linkedin_url")
    token = data.get("resume_token")

    if not cpf or not linkedin_url or not token:
        return jsonify({"error": "CPF, LinkedIn e token são obrigatórios"}), 400

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 🔑 Sempre valida token antes de qualquer coisa
    cur.execute("SELECT * FROM validate_resume_token(%s, %s)", (cpf, token))
    result = cur.fetchone()
    if not result or not result["valid"]:
        return jsonify({"error": result["reason"] if result else "Token inválido"}), 403

    # Se já tiver cache → retorna e incrementa contador
    cache_file = get_cache_path(cpf, linkedin_url)
    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            ats_data = json.load(f)
        cur.execute("UPDATE payments SET usage_count = usage_count + 1 WHERE cpf=%s AND resume_token=%s", (cpf, token))
        db.commit()
        return jsonify({"message": "Currículo carregado do cache!", "resume": ats_data}), 200

    # Se já tiver no banco → retorna e incrementa contador
    cur.execute("SELECT resume_json FROM resumes WHERE cpf=%s AND linkedin_url=%s", (cpf, linkedin_url))
    existing_resume = cur.fetchone()
    if existing_resume:
        cur.execute("UPDATE payments SET usage_count = usage_count + 1 WHERE cpf=%s AND resume_token=%s", (cpf, token))
        db.commit()
        return jsonify({
            "message": "Currículo carregado do banco!",
            "resume": json.loads(existing_resume['resume_json'])
        }), 200

    # 🔑 Incrementa contador de uso (primeira geração)
    cur.execute("UPDATE payments SET usage_count = usage_count + 1 WHERE cpf=%s AND resume_token=%s", (cpf, token))
    db.commit()

    # Extrai dados (ou usa mock)
    ats_data = extract_profile_ats_from_linkedin_url(linkedin_url)
    if "error" in ats_data:
        return jsonify(ats_data), 500

    # Salva em cache
    with open(cache_file, "w") as f:
        json.dump(ats_data, f)

    # Salva no banco
    cur.execute(
        "INSERT INTO resumes (cpf, resume_json, linkedin_url, created_at) VALUES (%s, %s, %s, %s)",
        (cpf, json.dumps(ats_data), linkedin_url, time.time())
    )
    db.commit()
    cur.close()

    return jsonify({"message": "Currículo gerado com sucesso!", "resume": ats_data}), 200

# ========== Dashboard ==========
@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    cpf = request.args.get("cpf")
    if not cpf:
        return jsonify({"error": "CPF é obrigatório"}), 400

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM resumes WHERE cpf=%s", (cpf,))
    resumes = cur.fetchall()
    cur.close()
    return jsonify({"resumes": resumes}), 200

# ========== Entry ==========
if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
