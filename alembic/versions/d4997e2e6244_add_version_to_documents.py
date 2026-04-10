"""add_version_to_documents

Revision ID: d4997e2e6244
Revises: a23fa09e7c11
Create Date: 2026-04-05 13:37:12.652300

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4997e2e6244'
down_revision: Union[str, Sequence[str], None] = 'a23fa09e7c11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('documents', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('documents', 'version')
