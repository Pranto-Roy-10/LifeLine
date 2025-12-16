"""add responder location to sos_responses

Revision ID: 20251216_sos_response_location
Revises: 20251216_sos_responses
Create Date: 2025-12-16

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251216_sos_response_location'
down_revision = '20251216_sos_responses'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('sos_responses', sa.Column('responder_lat', sa.Float(), nullable=True))
    op.add_column('sos_responses', sa.Column('responder_lng', sa.Float(), nullable=True))


def downgrade():
    op.drop_column('sos_responses', 'responder_lng')
    op.drop_column('sos_responses', 'responder_lat')
