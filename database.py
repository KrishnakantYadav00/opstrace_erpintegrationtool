import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Default to SQLite local database file in the project folder
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "erp.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

engine_args = {}
if DATABASE_URL.startswith("sqlite"):
    engine_args["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency / context helper for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(drop_all: bool = False):
    """Initialize database tables."""
    import models  # noqa: F401 - ensure models are registered with Base

    if drop_all:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
