"""Create the complete Portal-owned schema for a fresh installation."""

import sqlalchemy as sa
from alembic import op

revision = "portal_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("site_code", sa.String(length=2), nullable=False),
        sa.Column("site_id", sa.String(length=5), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("location", sa.String(length=512), nullable=True),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column(
            "numbering_mode",
            sa.String(length=16),
            server_default=sa.text("'short'"),
            nullable=False,
        ),
        sa.Column("grid_square", sa.String(length=8), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("owner_subject", sa.String(length=255), nullable=True),
        sa.Column("owner_entity_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 200", name="valid_site_name"),
        sa.CheckConstraint(
            "country_code = upper(country_code) AND length(country_code) = 2",
            name="valid_country_code",
        ),
        sa.CheckConstraint("length(site_code) = 2", name="valid_site_code"),
        sa.CheckConstraint("site_id IS NULL OR length(site_id) = 5", name="valid_site_id"),
        sa.CheckConstraint(
            "numbering_mode IN ('short', 'extended')", name="valid_site_numbering_mode"
        ),
        sa.CheckConstraint(
            "owner_subject IS NULL OR owner_entity_id IS NULL", name="single_site_owner"
        ),
        sa.CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)",
            name="valid_site_coordinates",
        ),
        sa.ForeignKeyConstraint(["owner_entity_id"], ["entities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("site_id", name="uq_sites_site_id"),
    )
    op.create_index("ix_sites_owner_entity_id", "sites", ["owner_entity_id"])
    op.create_index("uq_sites_name", "sites", ["name"], unique=True)

    op.create_table(
        "site_id_allocations",
        sa.Column("site_id", sa.String(length=5), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("site_code", sa.String(length=2), nullable=False),
        sa.Column("site_pk", sa.Integer(), nullable=True),
        sa.Column(
            "allocated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["site_pk"], ["sites.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("site_id"),
        sa.UniqueConstraint("site_pk", name="uq_site_id_allocation_site"),
    )

    op.create_table(
        "repeaters",
        sa.Column("node_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("call_sign", sa.String(length=32), nullable=True),
        sa.Column("frequency", sa.String(length=64), nullable=True),
        sa.Column("tone", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_registration_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_registration_ip", sa.String(length=45), nullable=True),
        sa.Column("registration_source", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "node_id > 0 AND node_id <= 9223372036854775807",
            name="positive_repeater_node_id",
        ),
        sa.CheckConstraint(
            "registration_source IS NULL OR length(registration_source) BETWEEN 1 AND 128",
            name="valid_registration_source",
        ),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("node_id"),
    )
    op.create_index("ix_repeaters_site_id", "repeaters", ["site_id"])
    op.create_index("ix_repeaters_last_registration_at", "repeaters", ["last_registration_at"])

    op.create_table(
        "portal_entity_profiles",
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("website", sa.String(length=512), nullable=True),
        sa.Column("max_sites", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.CheckConstraint("max_sites >= 0", name="valid_portal_entity_site_limit"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("entity_id"),
    )

    op.create_table(
        "portal_entity_members",
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "role IN ('Staff', 'Viewer')", name="valid_portal_entity_member_role"
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("entity_id", "user_id"),
    )

    op.create_table(
        "portal_users",
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("call_signs", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("subject"),
    )


def downgrade():
    op.drop_table("portal_users")
    op.drop_table("portal_entity_members")
    op.drop_table("portal_entity_profiles")
    op.drop_index("ix_repeaters_last_registration_at", table_name="repeaters")
    op.drop_index("ix_repeaters_site_id", table_name="repeaters")
    op.drop_table("repeaters")
    op.drop_table("site_id_allocations")
    op.drop_index("uq_sites_name", table_name="sites")
    op.drop_index("ix_sites_owner_entity_id", table_name="sites")
    op.drop_table("sites")
