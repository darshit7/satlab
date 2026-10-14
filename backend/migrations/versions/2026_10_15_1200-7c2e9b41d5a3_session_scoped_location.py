"""session-scoped observer location

A mission session now carries its own location (copied from a preset station, or a custom
lat/lon that is never saved as a ground station), so ``station_id`` becomes optional on
``mission_session`` and ``pass_window``.

Revision ID: 7c2e9b41d5a3
Revises: 1a3c54fd37e4
Create Date: 2026-10-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c2e9b41d5a3'
down_revision: Union[str, Sequence[str], None] = '1a3c54fd37e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('mission_session', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location_name', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('lat_deg', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('lon_deg', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('alt_m', sa.Float(), server_default='0', nullable=True))

    # Existing sessions observed from their station: copy its location onto the session.
    op.execute(
        """
        UPDATE mission_session SET
            location_name = (SELECT name FROM ground_station WHERE ground_station.id = mission_session.station_id),
            lat_deg = (SELECT lat_deg FROM ground_station WHERE ground_station.id = mission_session.station_id),
            lon_deg = (SELECT lon_deg FROM ground_station WHERE ground_station.id = mission_session.station_id),
            alt_m = (SELECT alt_m FROM ground_station WHERE ground_station.id = mission_session.station_id)
        """
    )

    with op.batch_alter_table('mission_session', schema=None) as batch_op:
        batch_op.alter_column('location_name', existing_type=sa.Text(), nullable=False)
        batch_op.alter_column('lat_deg', existing_type=sa.Float(), nullable=False)
        batch_op.alter_column('lon_deg', existing_type=sa.Float(), nullable=False)
        batch_op.alter_column('alt_m', existing_type=sa.Float(), nullable=False)
        batch_op.alter_column('station_id', existing_type=sa.Integer(), nullable=True)

    with op.batch_alter_table('pass_window', schema=None) as batch_op:
        batch_op.alter_column('station_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Downgrade schema. Sessions at a custom location have no station to fall back to: dropped."""
    op.execute(
        "DELETE FROM pass_window WHERE session_id IN "
        "(SELECT id FROM mission_session WHERE station_id IS NULL)"
    )
    op.execute("DELETE FROM pass_window WHERE station_id IS NULL")
    op.execute("DELETE FROM mission_session WHERE station_id IS NULL")

    with op.batch_alter_table('pass_window', schema=None) as batch_op:
        batch_op.alter_column('station_id', existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table('mission_session', schema=None) as batch_op:
        batch_op.alter_column('station_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('alt_m')
        batch_op.drop_column('lon_deg')
        batch_op.drop_column('lat_deg')
        batch_op.drop_column('location_name')
