import os
import psycopg

def get_db_connection():
    """
    Establishes and returns a new database connection.

    Reads the database connection string from the DB_URL environment variable.
    """
    try:
        db_url = os.environ.get("DB_URL")
        if not db_url:
            raise ValueError("DB_URL environment variable is not set.")

        conn = psycopg.connect(db_url)
        print("Database connection established successfully.")
        return conn
    except psycopg.OperationalError as e:
        print(f"Error: Could not connect to the database: {e}")
        raise
