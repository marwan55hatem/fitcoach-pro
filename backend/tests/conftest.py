from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from alembic.config import Config
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.main import create_app
from app.services.storage import Storage, get_storage, make_storage

TEST_DB_NAME = "fitcoach_test"
TEST_BUCKET = "fitcoach-test"


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


@pytest.fixture(scope="session")
def storage() -> Iterator[Storage]:
    """Real MinIO, in a dedicated test bucket (never the dev bucket)."""
    store = make_storage(TEST_BUCKET)
    client = store._client
    try:
        client.head_bucket(Bucket=TEST_BUCKET)
    except ClientError:
        client.create_bucket(Bucket=TEST_BUCKET)
    yield store
    store.delete_prefix("")


@pytest.fixture(autouse=True)
def _rate_limiting_off() -> Iterator[None]:
    """Rate limiting is only enabled by the test that exercises it."""
    limiter.enabled = False
    limiter.reset()
    yield
    limiter.enabled = True
    limiter.reset()


@pytest.fixture
def client(db: Session, storage: Storage) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage] = lambda: storage
    with TestClient(app) as test_client:
        yield test_client


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
