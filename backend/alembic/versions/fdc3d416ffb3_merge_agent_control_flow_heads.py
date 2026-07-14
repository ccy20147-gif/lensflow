"""merge agent control-flow heads

Revision ID: fdc3d416ffb3
Revises: b0b1c2d3e4f5, f1a2b3c4d5e6
Create Date: 2026-07-13 06:17:46.471965

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fdc3d416ffb3'
down_revision: Union[str, Sequence[str], None] = ('b0b1c2d3e4f5', 'f1a2b3c4d5e6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
