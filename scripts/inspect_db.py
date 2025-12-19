import json
import sqlite3
from pathlib import Path


def main() -> None:
    db_path = Path(__file__).resolve().parents[1] / "lifeline.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    res: dict[str, object] = {}

    for table in ["requests", "reviews", "impact_log", "event"]:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            res[table] = int(cur.fetchone()[0])
        except Exception as exc:  # pragma: no cover
            res[table] = f"ERROR: {exc}"

    try:
        cur.execute("SELECT status, COUNT(*) FROM requests GROUP BY status")
        res["requests_by_status"] = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM requests WHERE completed_at IS NOT NULL")
        res["requests_completed_at"] = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM requests WHERE helper_id IS NOT NULL")
        res["requests_with_helper"] = int(cur.fetchone()[0])
    except Exception as exc:  # pragma: no cover
        res["requests_details_error"] = f"ERROR: {exc}"

    try:
        cur.execute("SELECT COUNT(*) FROM reviews r JOIN requests q ON q.id = r.request_id")
        res["reviews_join_requests"] = int(cur.fetchone()[0])
    except Exception as exc:  # pragma: no cover
        res["reviews_join_requests"] = f"ERROR: {exc}"

    try:
        cur.execute("SELECT COALESCE(SUM(duration_hours), 0) FROM reviews")
        res["review_hours_sum"] = float(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT id, request_id, reviewer_id, helper_id, duration_hours, created_at FROM reviews ORDER BY id DESC LIMIT 5"
        )
        res["recent_reviews"] = cur.fetchall()
    except Exception as exc:  # pragma: no cover
        res["review_hours_error"] = f"ERROR: {exc}"

    try:
        cur.execute(
            """
            SELECT q.id, q.is_offer, q.user_id, q.helper_id
            FROM requests q
            JOIN reviews r ON r.request_id = q.id
            ORDER BY q.id DESC
            LIMIT 10
            """.strip()
        )
        rows = cur.fetchall()
        res["recipient_inference_rows"] = rows
        recipient_ids = set()
        for _req_id, is_offer, owner_id, helper_id in rows:
            recipient_id = helper_id if bool(is_offer) else owner_id
            if recipient_id is not None:
                recipient_ids.add(int(recipient_id))
        res["recipient_ids_from_rows"] = sorted(recipient_ids)
    except Exception as exc:  # pragma: no cover
        res["recipient_inference_error"] = f"ERROR: {exc}"

    print(json.dumps(res, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
