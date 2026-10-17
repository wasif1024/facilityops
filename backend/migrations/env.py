from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db import models as _models


config = context.config
if config.config_file_name is not None:
    # Keep loggers the app already created; the default would silently disable them.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Same source as the app: DATABASE_URL from the environment, falling back to .env.
# Callers such as the integration tests may set sqlalchemy.url explicitly instead.
if not config.get_main_option("sqlalchemy.url"):
    database_url = get_settings().database_url
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata
# Any fixed number, the same for every replica: identifies the migration lock.
MIGRATION_LOCK_KEY = 72_001_001


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    if get_settings().database_entra_auth:
        from backend.app.db.engine import use_entra_password

        use_entra_password(connectable)
    with connectable.connect() as connection:
        # In Azure every API replica runs migrations on start: a session-level advisory lock makes
        # the others wait, then find nothing left to do.
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        connection.commit()
        try:
            context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()