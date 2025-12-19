import sqlite3
from pathlib import Path


def main() -> None:
    db_path = Path(__file__).resolve().parents[1] / "lifeline.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) FROM requests q JOIN reviews r ON r.request_id = q.id WHERE q.category = 'ride'"
    )
    print("ride_reviews:", cur.fetchone()[0])

    cur.execute(
        "SELECT q.id, q.category, q.status, q.helper_id, q.user_id, q.is_offer FROM requests q ORDER BY q.id"
    )
    for row in cur.fetchall():
        print("request:", row)

    conn.close()


if __name__ == "__main__":
    main()
