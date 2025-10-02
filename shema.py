import psycopg2
import psycopg2.extras

# URL de conexão (Render)
DATABASE_URL = "postgresql://linkcv_db_user:DhZBaXT6gWBi7oybxD36Z9HnWgt3hWff@dpg-d3f9nnb3fgac73b1ah00-a.oregon-postgres.render.com/linkcv_db"

def list_payments():
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT 10")
    rows = cur.fetchall()
    print("\n--- Payments ---")
    for r in rows:
        print(dict(r))
    cur.close()
    conn.close()

def list_resumes():
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM resumes ORDER BY created_at DESC LIMIT 10")
    rows = cur.fetchall()
    print("\n--- Resumes ---")
    for r in rows:
        print(dict(r))
    cur.close()
    conn.close()

def test_token(cpf, token):
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cur = conn.cursor()
    cur.execute("SELECT * FROM validate_resume_token(%s, %s)", (cpf, token))
    result = cur.fetchone()
    print("\n--- Validate Token ---")
    print(f"CPF={cpf}, token={token} -> {result}")
    cur.close()
    conn.close()

if __name__ == "__main__":
    list_payments()
    list_resumes()

    # Teste de token (troque pelos valores reais do seu banco)
    test_token("12345678900", "abcdef1234567890")
