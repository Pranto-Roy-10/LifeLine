"""add emergency number to user

Revision ID: 20251216_emergency_number
Revises: 382916b98233
Create Date: 2025-12-16

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251216_emergency_number'
down_revision = '382916b98233'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user', sa.Column('emergency_number', sa.String(length=30), nullable=True))


def downgrade():
    op.drop_column('user', 'emergency_number')
