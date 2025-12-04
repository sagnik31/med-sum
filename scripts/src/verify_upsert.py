import psycopg2
import sys
import logging
import uuid
import os

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_upsert")


def verify_upsert():
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

        # 2. Insert a dummy document
        doc_id = str(uuid.uuid4())
        dummy_file_path = os.path.abspath("dummy_report_upsert.pdf")
        with open(dummy_file_path, "w") as f:
            f.write("Dummy PDF content")

        with conn.cursor() as cur:
            insert_query = """
                INSERT INTO documents (id, user_id, original_name, content_type, storage_path, status, extracted_markdown)
                VALUES (%s, %s, 'dummy_report_upsert.pdf', 'application/pdf', %s, 'uploaded', 'Some markdown')
            """
            cur.execute(insert_query, (doc_id, user_id, dummy_file_path))
            conn.commit()
            logger.info(f"Inserted dummy document with ID: {doc_id}")

        # 3. Simulate UPSERT (Insert new insight)
        html_content = "<html><body>New Insight</body></html>"
        new_insight_id = str(uuid.uuid4())

        with conn.cursor() as cur:
            upsert_query = """
                INSERT INTO insights (id, document_id, user_id, html_insights, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 'completed', NOW(), NOW())
                ON CONFLICT (document_id) 
                DO UPDATE SET
                    html_insights = EXCLUDED.html_insights,
                    status = 'completed',
                    updated_at = NOW();
            """
            cur.execute(upsert_query, (new_insight_id, doc_id, user_id, html_content))
            conn.commit()
            logger.info("Executed UPSERT query (Insert case).")

        # 4. Verify Insert
        with conn.cursor() as cur:
            cur.execute(
                "SELECT html_insights FROM insights WHERE document_id = %s", (doc_id,)
            )
            row = cur.fetchone()
            if row and row[0] == html_content:
                logger.info("Verified Insert: Insight correctly saved.")
            else:
                logger.error("Verified Insert: Failed to save insight.")
                sys.exit(1)

        # 5. Simulate UPSERT (Update existing insight)
        updated_html = "<html><body>Updated Insight</body></html>"
        with conn.cursor() as cur:
            # Re-run same query with new content
            cur.execute(
                upsert_query, (str(uuid.uuid4()), doc_id, user_id, updated_html)
            )
            conn.commit()
            logger.info("Executed UPSERT query (Update case).")

        # 6. Verify Update
        with conn.cursor() as cur:
            cur.execute(
                "SELECT html_insights FROM insights WHERE document_id = %s", (doc_id,)
            )
            row = cur.fetchone()
            if row and row[0] == updated_html:
                logger.info("Verified Update: Insight correctly updated.")
            else:
                logger.error("Verified Update: Failed to update insight.")
                sys.exit(1)

        # Clean up
        with conn.cursor() as cur:
            cur.execute("DELETE FROM insights WHERE document_id = %s", (doc_id,))
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
    verify_upsert()
