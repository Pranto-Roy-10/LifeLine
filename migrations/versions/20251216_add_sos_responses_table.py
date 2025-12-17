"""add sos responses table

Revision ID: 20251216_sos_responses
Revises: 20251216_emergency_number
Create Date: 2025-12-16

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251216_sos_responses'
down_revision = '20251216_emergency_number'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sos_responses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.Integer(), nullable=False),
        sa.Column('helper_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['helper_id'], ['user.id']),
        sa.ForeignKeyConstraint(['request_id'], ['requests.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sos_responses_request_id', 'sos_responses', ['request_id'])
    op.create_index('ix_sos_responses_helper_id', 'sos_responses', ['helper_id'])


def downgrade():
    op.drop_index('ix_sos_responses_helper_id', table_name='sos_responses')
    op.drop_index('ix_sos_responses_request_id', table_name='sos_responses')
    op.drop_table('sos_responses')
