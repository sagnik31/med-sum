import psycopg2

DB_DSN = "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"


def verify_persistence():
    try:
        conn = psycopg2.connect(DB_DSN)
        with conn.cursor() as cur:
            # Check if any user has patient_insights populated
            cur.execute("""
                SELECT id, LENGTH(patient_insights) 
                FROM users 
                WHERE patient_insights IS NOT NULL AND patient_insights != '';
            """)
            rows = cur.fetchall()

            if rows:
                print(f"Found {len(rows)} user(s) with patient_insights:")
                for row in rows:
                    print(f"  User ID: {row[0]}, Insights Length: {row[1]}")
            else:
                print("No patient_insights found in DB yet.")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    verify_persistence()
