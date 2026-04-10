"""change_embedding_dimension_to_384

Revision ID: 55694c3f5cdd
Revises: d4997e2e6244
Create Date: 2026-04-05 14:04:50.831753

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy.vector


# revision identifiers, used by Alembic.
revision: str = '55694c3f5cdd'
down_revision: Union[str, Sequence[str], None] = 'd4997e2e6244'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Switch from OpenAI 1536-dim embeddings to local 384-dim embeddings."""
    # Drop HNSW index first
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding")
    # Clear existing embeddings (wrong dimensions)
    op.execute("UPDATE document_chunks SET embedding = NULL")
    # Change column dimension
    op.alter_column('document_chunks', 'embedding',
               existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=1536),
               type_=pgvector.sqlalchemy.vector.VECTOR(dim=384),
               existing_nullable=True)
    # Recreate HNSW index with new dimensions
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    """Revert to 1536-dim embeddings."""
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding")
    op.execute("UPDATE document_chunks SET embedding = NULL")
    op.alter_column('document_chunks', 'embedding',
               existing_type=pgvector.sqlalchemy.vector.VECTOR(dim=384),
               type_=pgvector.sqlalchemy.vector.VECTOR(dim=1536),
               existing_nullable=True)
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
