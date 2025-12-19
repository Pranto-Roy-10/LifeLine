from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402


def main() -> None:
    with app.app.app_context():
        review_hours = (
            app.db.session.query(app.func.coalesce(app.func.sum(app.Review.duration_hours), 0.0)).scalar()
            or 0.0
        )
        event_hours = (
            app.db.session.query(app.func.coalesce(app.func.sum(app.ImpactLog.hours), 0.0)).scalar()
            or 0.0
        )

        recipient_rows = (
            app.db.session.query(app.Request.is_offer, app.Request.user_id, app.Request.helper_id)
            .join(app.Review, app.Review.request_id == app.Request.id)
            .all()
        )
        recipient_ids = set()
        for is_offer, owner_id, helper_id in recipient_rows:
            recipient_id = helper_id if is_offer else owner_id
            if recipient_id:
                recipient_ids.add(int(recipient_id))

        print("review_hours:", float(review_hours))
        print("event_hours:", float(event_hours))
        print("recipient_rows:", recipient_rows)
        print("recipient_ids:", sorted(recipient_ids))
        print("total_helped:", len(recipient_ids))


if __name__ == "__main__":
    main()
