"""FIFO connection handoff using Psycopg's supported SQLAlchemy integration."""

from threading import Lock

from psycopg_pool import ConnectionPool
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool


def pooled_engine(
    value: str | URL, *, max_size: int = 8, timeout: float = 30, max_waiting: int = 256
) -> Engine:
    """Create lazily; dispose closes the owned pool, and subsequent use creates a new one.

    NullPool avoids a second checkout queue. Returned connections go straight to the
    first Psycopg waiter, instead of competing with callers arriving after them.
    Engines must be created inside their worker process, not inherited across forks.
    """
    url = make_url(value)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("Cloud warehouse requires postgresql+psycopg with PostgreSQL RLS")
    # Preserve SQLAlchemy's decoded options, SSL and multi-host handling. Rendering
    # its URL as libpq URI would turn spaces in options into literal plus signs.
    _, connect_args = url.get_dialect()().create_connect_args(url)
    lock = Lock()
    owned: ConnectionPool | None = None

    def connection():
        nonlocal owned
        with lock:
            if owned is None:
                owned = ConnectionPool(
                    kwargs=connect_args,
                    min_size=0,
                    max_size=max_size,
                    timeout=timeout,
                    max_waiting=max_waiting,
                    open=True,
                    close_returns=True,
                    check=ConnectionPool.check_connection,
                    name="warehouse-db",
                )
            pool = owned
        # Never keep the ownership lock while waiting for another borrower to return.
        return pool.getconn()

    def dispose(engine):
        nonlocal owned
        with lock:
            previous, owned = owned, None
        if previous is not None:
            previous.close()

    engine = create_engine(url, poolclass=NullPool, creator=connection, hide_parameters=True)
    event.listen(engine, "engine_disposed", dispose)
    return engine
