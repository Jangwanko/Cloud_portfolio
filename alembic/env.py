from logging.config import fileConfig
import os
from urllib.parse import quote

from alembic import context
from sqlalchemy import engine_from_config, pool


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = None


def _override_sqlalchemy_url_from_env() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        db_host = os.getenv("DB_HOST")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME")
        db_user = os.getenv("DB_USER")
        db_password = os.getenv("DB_PASSWORD")
        if all([db_host, db_name, db_user, db_password]):
            database_url = (
                f"postgresql+psycopg2://{quote(db_user, safe='')}:"
                f"{quote(db_password, safe='')}@{db_host}:{db_port}/"
                f"{db_name}"
            )

    if database_url:
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


_override_sqlalchemy_url_from_env()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            # API replicas start independently. A transaction-scoped advisory
            # lock serializes upgrades and is released automatically on commit,
            # rollback, or connection loss.
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(864209731)")
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
