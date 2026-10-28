from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings

TEST_DB_NAME = "fitcoach_test"


def _test_url() -> URL:
    return make_url(get_settings().database_url).set(database=TEST_DB_NAME)


@pytest.fixture(scope="session")
def engine() -> Iterator[sa.Engine]:
    """A throwaway database migrated with Alembic, so tests exercise the real migrations."""
    test_url = _test_url()
    admin = sa.create_engine(
        test_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        conn.execute(sa.text(f'CREATE DATABASE "{TEST_DB_NAME}"'))

    cfg = Config("alembic.ini")
    cfg.set_main_option(
        "sqlalchemy.url", test_url.render_as_string(hide_password=False)
    )
    command.upgrade(cfg, "head")

    engine = sa.create_engine(test_url)
    yield engine
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def db(engine: sa.Engine) -> Iterator[Session]:
    """Each test runs in a transaction that is rolled back (savepoints allow inner commits)."""
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
