import psycopg2
import logging
import sys

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("check_schema")


def check_schema():
    dsn = "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"
    try:
        conn = psycopg2.connect(dsn)
        logger.info("Successfully connected to the database.")

        with conn.cursor() as cur:
            # Query to get columns of the insights table
            cur.execute("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'insights';
            """)
            columns = cur.fetchall()

            logger.info("Columns in 'insights' table:")
            for col in columns:
                logger.info(f"  {col[0]} ({col[1]})")

        conn.close()

    except Exception as e:
        logger.error(f"Schema check failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    check_schema()
