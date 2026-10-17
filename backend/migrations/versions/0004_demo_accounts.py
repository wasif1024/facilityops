"""Demo accounts for local development

Revision ID: 0004_demo_accounts
Revises: 0003_review_queue_work_orders
Create Date: 2026-10-17 16:00:00.000000

Creates one account per role so a fresh clone can be explored straight away:
alice (tenant), fm (facility manager), tech (technician), local-developer (administrator).

They are *development* identities: they have no password, and the backend accepts them only
when AUTH_ENABLED=false and APP_ENV is not production. Nothing is created when
APP_ENV=production or SEED_DEMO_ACCOUNTS=false. Before using the project for real, remove them
with `scripts/db.sh remove-demo-accounts` (see README, Demo accounts).

Data migration only; the account list is frozen here on purpose (migrations must not change
when application code does). The application's copy is backend/app/services/demo_accounts.py.
"""
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

from backend.app.core.config import get_settings


revision: str = '0004_demo_accounts'
down_revision: Union[str, Sequence[str], None] = '0003_review_queue_work_orders'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROVIDER = "development"
DEMO_ACCOUNTS = [
    ("alice", "Alice (demo tenant)", "tenant"),
    ("fm", "Fran Manager (demo)", "facility_manager"),
    ("tech", "Taylor Tech (demo)", "technician"),
    ("local-developer", "Local developer (demo admin)", "admin"),
]


def upgrade() -> None:
    settings = get_settings()
    if settings.app_env == "production" or not settings.seed_demo_accounts:
        return
    connection = op.get_bind()
    for external_id, display_name, role in DEMO_ACCOUNTS:
        created = connection.execute(
            sa.text(
                "INSERT INTO users (id, identity_provider, external_id, display_name, status) "
                "VALUES (:id, :provider, :external_id, :display_name, 'active') "
                "ON CONFLICT (identity_provider, external_id) DO NOTHING RETURNING id"
            ),
            {"id": uuid4(), "provider": PROVIDER, "external_id": external_id, "display_name": display_name},
        ).scalar()
        user_id = connection.execute(
            sa.text("SELECT id FROM users WHERE identity_provider = :provider AND external_id = :external_id"),
            {"provider": PROVIDER, "external_id": external_id},
        ).scalar_one()
        connection.execute(
            sa.text(
                "INSERT INTO user_roles (user_id, role_id) VALUES (:user_id, :role) "
                "ON CONFLICT (user_id, role_id) DO NOTHING"
            ),
            {"user_id": user_id, "role": role},
        )
        if created is not None:
            connection.execute(
                sa.text(
                    "INSERT INTO security_audit_events (id, action, target_type, target_id, details) "
                    "VALUES (:id, 'user.demo_account_created', 'user', :target, CAST(:details AS jsonb))"
                ),
                {"id": uuid4(), "target": str(user_id), "details": f'{{"role": "{role}"}}'},
            )


def downgrade() -> None:
    # Removes the demo accounts (their role grants cascade; references to them become NULL).
    op.get_bind().execute(
        sa.text("DELETE FROM users WHERE identity_provider = :provider AND external_id = ANY(:ids)"),
        {"provider": PROVIDER, "ids": [external_id for external_id, _, _ in DEMO_ACCOUNTS]},
    )
