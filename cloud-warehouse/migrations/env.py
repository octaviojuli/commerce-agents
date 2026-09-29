"""Migration URLs are injected programmatically; secrets never enter alembic.ini."""

from alembic import context
from sqlalchemy import create_engine

config = context.config


def run() -> None:
    engine = create_engine(config.attributes["database_url"], hide_parameters=True)
    with engine.connect() as connection:
        context.configure(connection=connection, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run()
