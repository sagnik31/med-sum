import psycopg2
import sys
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_db")


def verify_db():
    dsn = "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"
    try:
        conn = psycopg2.connect(dsn)
        logger.info("Successfully connected to the database.")

        # Test the query syntax (prepare only, or run on a non-existent ID to be safe)
        # We'll try to update a non-existent document_id to check for SQL errors
        import uuid

        document_id = str(uuid.uuid4())
        html = "<html><body>Test Insights</body></html>"

        with conn.cursor() as cur:
            update_query = """
                UPDATE insights
                SET html_insights = %s,
                    status = 'completed',
                    updated_at = NOW()
                WHERE document_id = %s;
            """
            cur.execute(update_query, (html, document_id))
            conn.commit()
            logger.info("Successfully executed UPDATE query (even if 0 rows affected).")

        conn.close()
        logger.info("Verification successful.")

    except Exception as e:
        logger.error(f"Verification failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    verify_db()
