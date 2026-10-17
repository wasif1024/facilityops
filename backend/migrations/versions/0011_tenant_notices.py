"""Tenant notices: approved English text and its translation for the tenant (FR-61)

Revision ID: 0011_tenant_notices
Revises: 0010_incident_actions
Create Date: 2026-10-18 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0011_tenant_notices'
down_revision: Union[str, Sequence[str], None] = '0010_incident_actions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('tenant_notices',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('incident_id', sa.Uuid(), nullable=False),
    sa.Column('request_id', sa.Uuid(), nullable=False),
    sa.Column('subject', sa.String(length=120), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('language', sa.String(length=8), nullable=False),
    sa.Column('translated_subject', sa.Text(), nullable=True),
    sa.Column('translated_body', sa.Text(), nullable=True),
    sa.Column('reason_code', sa.String(length=64), nullable=True),
    sa.Column('edited', sa.Boolean(), nullable=False),
    sa.Column('approved_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id'], name=op.f('fk_tenant_notices_approved_by_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], name=op.f('fk_tenant_notices_incident_id_incidents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['request_id'], ['intake_requests.request_id'], name=op.f('fk_tenant_notices_request_id_intake_requests'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tenant_notices'))
    )
    op.create_index(op.f('ix_tenant_notices_incident_id'), 'tenant_notices', ['incident_id'], unique=False)
    op.create_index(op.f('ix_tenant_notices_request_id'), 'tenant_notices', ['request_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tenant_notices_request_id'), table_name='tenant_notices')
    op.drop_index(op.f('ix_tenant_notices_incident_id'), table_name='tenant_notices')
    op.drop_table('tenant_notices')
