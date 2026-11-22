"""sar_archive_explore

Phase 7a: sar_scene (a shared cache of public Sentinel-1 RTC scene metadata) and sar_comparison
(a session's before/after comparison and its result). Expand-only: two new tables the running
revision never touches, so it keeps working while this applies; the downgrade drops them (cache
and session-scoped data only). No PostGIS: areas and footprints are bbox columns plus GeoJSON
until Phase 8.

Revision ID: 8f3a6c2d9b14
Revises: 5b8c1e7d2f4a
Create Date: 2026-11-22 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.db.base


# revision identifiers, used by Alembic.
revision: str = '8f3a6c2d9b14'
down_revision: Union[str, Sequence[str], None] = '5b8c1e7d2f4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('sar_scene',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('collection', sa.String(length=64), nullable=False),
    sa.Column('stac_scene_id', sa.String(length=128), nullable=False),
    sa.Column('platform', sa.String(length=16), nullable=False),
    sa.Column('orbit_state', sa.String(length=16), nullable=False),
    sa.Column('relative_orbit', sa.Integer(), nullable=False),
    sa.Column('polarizations', sa.JSON(), nullable=False),
    sa.Column('acquired_at', app.db.base.UTCDateTime(length=32), nullable=False),
    sa.Column('min_lon_deg', sa.Float(), nullable=False),
    sa.Column('min_lat_deg', sa.Float(), nullable=False),
    sa.Column('max_lon_deg', sa.Float(), nullable=False),
    sa.Column('max_lat_deg', sa.Float(), nullable=False),
    sa.Column('footprint', sa.JSON(), nullable=False),
    sa.Column('epsg', sa.Integer(), nullable=False),
    sa.Column('assets', sa.JSON(), nullable=False),
    sa.Column('fetched_at', app.db.base.UTCDateTime(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('collection', 'stac_scene_id')
    )
    op.create_table('sar_comparison',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('session_id', sa.Integer(), nullable=False),
    sa.Column('scene_before_id', sa.Integer(), nullable=False),
    sa.Column('scene_after_id', sa.Integer(), nullable=False),
    sa.Column('frames', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error', sa.String(length=32), nullable=True),
    sa.Column('polarization', sa.String(length=4), nullable=False),
    sa.Column('aoi_lat_deg', sa.Float(), nullable=False),
    sa.Column('aoi_lon_deg', sa.Float(), nullable=False),
    sa.Column('aoi_size_km', sa.Float(), nullable=False),
    sa.Column('min_lon_deg', sa.Float(), nullable=False),
    sa.Column('min_lat_deg', sa.Float(), nullable=False),
    sa.Column('max_lon_deg', sa.Float(), nullable=False),
    sa.Column('max_lat_deg', sa.Float(), nullable=False),
    sa.Column('aoi_geojson', sa.JSON(), nullable=False),
    sa.Column('grid', sa.JSON(), nullable=False),
    sa.Column('requested_before', sa.Date(), nullable=False),
    sa.Column('requested_after', sa.Date(), nullable=False),
    sa.Column('method', sa.String(length=16), nullable=False),
    sa.Column('params_version', sa.Integer(), nullable=False),
    sa.Column('generation', sa.Integer(), server_default='1', nullable=False),
    sa.Column('threshold_db', sa.Float(), nullable=True),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('stats', sa.JSON(), nullable=True),
    sa.Column('regions', sa.JSON(), nullable=True),
    sa.Column('sim_time', app.db.base.UTCDateTime(length=32), nullable=False),
    sa.Column('created_at', app.db.base.UTCDateTime(length=32), nullable=False),
    sa.Column('finished_at', app.db.base.UTCDateTime(length=32), nullable=True),
    sa.ForeignKeyConstraint(['scene_after_id'], ['sar_scene.id'], ),
    sa.ForeignKeyConstraint(['scene_before_id'], ['sar_scene.id'], ),
    sa.ForeignKeyConstraint(['session_id'], ['mission_session.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_sar_comparison_session', 'sar_comparison', ['session_id', 'id'], unique=False)
    op.create_index('ix_sar_comparison_status', 'sar_comparison', ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_sar_comparison_status', table_name='sar_comparison')
    op.drop_index('ix_sar_comparison_session', table_name='sar_comparison')
    op.drop_table('sar_comparison')
    op.drop_table('sar_scene')
