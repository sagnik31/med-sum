import requests
import json
import psycopg2

# Configuration
API_URL = "http://localhost:8001/internal/generate-user-insights"
DB_DSN = "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"


def get_valid_user_id():
    try:
        conn = psycopg2.connect(DB_DSN)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users LIMIT 1")
            row = cur.fetchone()
            if row:
                return row[0]
    except Exception as e:
        print(f"Error fetching user ID: {e}")
    return None


def test_generate_user_insights():
    print(f"Testing API: {API_URL}")

    user_id = get_valid_user_id()
    if not user_id:
        print("No user found in DB to test with.")
        return

    print(f"Using User ID: {user_id}")

    # Payload
    payload = {"user_id": user_id}

    try:
        response = requests.post(API_URL, json=payload)

        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print("Response Status:", data.get("status"))
            html_content = data.get("html", "")
            print(f"HTML Content Length: {len(html_content)}")
            print("HTML Preview:", html_content[:200])
        else:
            print("Error Response:", response.text)

    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to the service. Is it running?")


if __name__ == "__main__":
    test_generate_user_insights()
