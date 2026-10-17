"""Actions the application carried out for approved incident plans (FR-25)

Revision ID: 0010_incident_actions
Revises: 0009_facility_data
Create Date: 2026-10-18 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0010_incident_actions'
down_revision: Union[str, Sequence[str], None] = '0009_facility_data'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('incident_actions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('incident_id', sa.Uuid(), nullable=False),
    sa.Column('plan_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('action_type', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('reason_code', sa.String(length=64), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('approved_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id'], name=op.f('fk_incident_actions_approved_by_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name=op.f('fk_incident_actions_incident_id_incidents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['plan_id'], ['incident_plans.id'], name=op.f('fk_incident_actions_plan_id_incident_plans'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_incident_actions')),
    sa.UniqueConstraint('plan_id', 'position', name=op.f('uq_incident_actions_plan_id_position'))
    )
    op.create_index(op.f('ix_incident_actions_incident_id'), 'incident_actions', ['incident_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_incident_actions_incident_id'), table_name='incident_actions')
    op.drop_table('incident_actions')
