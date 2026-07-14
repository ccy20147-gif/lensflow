"""Create all SQLAlchemy ORM tables for ToonFlow."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.infra.db.base import Base
from src.infra.db.session import get_engine
import src.infra.db.models  # noqa: F401 — register all model tables

Base.metadata.create_all(get_engine())
print("Tables created successfully")
