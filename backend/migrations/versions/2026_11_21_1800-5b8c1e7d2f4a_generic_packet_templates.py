"""generic_packet_templates

The generic packet-template set, shared by every satellite without its own templates, is stored
once with no satellite: packet_template.norad_id becomes nullable, and a partial unique index on
(kind, name) WHERE norad_id IS NULL keys those rows (NULLs never conflict in the existing
(norad_id, kind, name) constraint). Expand-only: the running revision never writes NULL there,
and its sync deactivates rows it doesn't know before it loads, so it keeps working while this
applies and after a rollback.

Revision ID: 5b8c1e7d2f4a
Revises: 2e2299af7ee7
Create Date: 2026-11-21 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b8c1e7d2f4a'
down_revision: Union[str, Sequence[str], None] = '2e2299af7ee7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_GENERIC = sa.text("norad_id IS NULL")


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('packet_template', schema=None) as batch_op:
        batch_op.alter_column('norad_id', existing_type=sa.Integer(), nullable=True)
    op.create_index(
        'uq_packet_template_generic',
        'packet_template',
        ['kind', 'name'],
        unique=True,
        sqlite_where=_GENERIC,
        postgresql_where=_GENERIC,
    )


def downgrade() -> None:
    """Downgrade schema.

    The generic rows can't exist without a satellite: commands and frames that used them lose
    their template link (template_id NULL, which the schema allows), then the rows go.
    """
    generic_ids = sa.select(sa.column('id')).select_from(sa.table('packet_template')).where(
        _GENERIC
    )
    for table in ('command', 'telemetry_frame'):
        op.execute(
            sa.update(sa.table(table, sa.column('template_id')))
            .where(sa.column('template_id').in_(generic_ids))
            .values(template_id=None)
        )
    op.execute(sa.delete(sa.table('packet_template')).where(_GENERIC))
    op.drop_index(
        'uq_packet_template_generic',
        table_name='packet_template',
        sqlite_where=_GENERIC,
        postgresql_where=_GENERIC,
    )
    with op.batch_alter_table('packet_template', schema=None) as batch_op:
        batch_op.alter_column('norad_id', existing_type=sa.Integer(), nullable=False)
