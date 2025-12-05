import psycopg2

DB_DSN = "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"


def add_column():
    try:
        conn = psycopg2.connect(DB_DSN)
        with conn.cursor() as cur:
            # Check if column exists
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='users' AND column_name='patient_insights';
            """)
            if cur.fetchone():
                print("Column 'patient_insights' already exists.")
                return

            print("Adding 'patient_insights' column to 'users' table...")
            cur.execute("ALTER TABLE users ADD COLUMN patient_insights TEXT;")
            conn.commit()
            print("Column added successfully.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    add_column()
