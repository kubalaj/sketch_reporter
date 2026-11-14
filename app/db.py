from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import config


class Base(DeclarativeBase):
    pass


if config.database_url.startswith("sqlite:///"):
    Path(config.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(config.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db


def init_db() -> None:
    from . import models

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        models.get_settings(db)  # seeds the row on first run
