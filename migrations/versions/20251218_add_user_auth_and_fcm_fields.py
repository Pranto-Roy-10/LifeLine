"""add missing user auth/fcm fields

Revision ID: 20251218_user_auth_fcm
Revises: 20251216_sos_response_location
Create Date: 2025-12-18

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20251218_user_auth_fcm"
down_revision = "20251216_sos_response_location"
branch_labels = None
depends_on = None


def _user_columns(bind):
    inspector = inspect(bind)
    return {col["name"] for col in inspector.get_columns("user")}


def upgrade():
    bind = op.get_bind()
    existing = _user_columns(bind)

    # These fields exist on the SQLAlchemy User model but were missing from migrations.
    if "role" not in existing:
        op.add_column(
            "user",
            sa.Column("role", sa.String(length=20), nullable=False, server_default="user"),
        )

    if "fcm_token" not in existing:
        op.add_column(
            "user",
            sa.Column("fcm_token", sa.String(length=512), nullable=True),
        )

    if "is_premium" not in existing:
        op.add_column(
            "user",
            sa.Column("is_premium", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )

    if "premium_expiry" not in existing:
        op.add_column(
            "user",
            sa.Column("premium_expiry", sa.DateTime(), nullable=True),
        )

    if "is_admin" not in existing:
        op.add_column(
            "user",
            sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )


def downgrade():
    bind = op.get_bind()
    existing = _user_columns(bind)

    if "is_admin" in existing:
        op.drop_column("user", "is_admin")

    if "premium_expiry" in existing:
        op.drop_column("user", "premium_expiry")

    if "is_premium" in existing:
        op.drop_column("user", "is_premium")

    if "fcm_token" in existing:
        op.drop_column("user", "fcm_token")

    if "role" in existing:
        op.drop_column("user", "role")
