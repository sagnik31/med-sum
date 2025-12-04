import psycopg2
import sys
import logging
import uuid
import os

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_db")


def verify_db():
    dsn = "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"
    conn = None
    try:
        conn = psycopg2.connect(dsn)
        logger.info("Successfully connected to the database.")

        # 1. Get an existing user_id
        user_id = None
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users LIMIT 1")
            row = cur.fetchone()
            if row:
                user_id = row[0]
            else:
                logger.error("No users found in DB. Cannot insert document.")
                sys.exit(1)

        # 2. Insert a dummy document with a known path
        doc_id = str(uuid.uuid4())
        # Create a dummy file
        dummy_file_path = os.path.abspath("dummy_report.pdf")
        with open(dummy_file_path, "w") as f:
            f.write("Dummy PDF content")

        with conn.cursor() as cur:
            insert_query = """
                INSERT INTO documents (id, user_id, original_name, content_type, storage_path, status)
                VALUES (%s, %s, 'dummy_report.pdf', 'application/pdf', %s, 'uploaded')
            """
            cur.execute(insert_query, (doc_id, user_id, dummy_file_path))
            conn.commit()
            logger.info(
                f"Inserted dummy document with ID: {doc_id} for User ID: {user_id}"
            )

        # 3. Verify we can fetch it (simulating what insights_service does)
        with conn.cursor() as cur:
            cur.execute("SELECT storage_path FROM documents WHERE id = %s", (doc_id,))
            row = cur.fetchone()
            if row and row[0] == dummy_file_path:
                logger.info("Successfully fetched correct storage_path from DB.")
            else:
                logger.error(f"Failed to fetch storage_path. Got: {row}")
                sys.exit(1)

        # Clean up
        with conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
            conn.commit()
        os.remove(dummy_file_path)
        logger.info("Cleanup complete.")

        logger.info("Verification successful.")

    except Exception as e:
        logger.error(f"Verification failed: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    verify_db()
