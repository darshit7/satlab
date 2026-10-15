"""mission session token

Sessions get a capability token (returned once by ``POST /api/sessions``); only its SHA-256 is
stored. Nullable and additive, so the running revision keeps working while this applies. Older
sessions have no hash and can no longer be attached; clients simply start a new session.

Revision ID: b6f6785c3d6d
Revises: 7c2e9b41d5a3
Create Date: 2026-10-16 01:41:19.665123

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6f6785c3d6d'
down_revision: Union[str, Sequence[str], None] = '7c2e9b41d5a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('mission_session', schema=None) as batch_op:
        batch_op.add_column(sa.Column('token_hash', sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('mission_session', schema=None) as batch_op:
        batch_op.drop_column('token_hash')
