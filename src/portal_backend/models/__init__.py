"""Portal-owned persistence models."""

from datetime import datetime

from rpt_common.models import Base
from rpt_common.models.entities import normalize_call_sign
from rpt_common.utils.regex import SITE_CODE_RE
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates


class Site(Base):
    """A physical repeater site managed by Portal."""

    __tablename__ = "sites"
    __table_args__ = (
        CheckConstraint("length(name) BETWEEN 1 AND 200", name="valid_site_name"),
        CheckConstraint("country_code = upper(country_code) AND length(country_code) = 2", name="valid_country_code"),
        CheckConstraint("length(site_code) = 2", name="valid_site_code"),
        CheckConstraint("site_id IS NULL OR length(site_id) = 5", name="valid_site_id"),
        CheckConstraint(
            "numbering_mode IN ('short', 'extended')", name="valid_site_numbering_mode"
        ),
        CheckConstraint("owner_subject IS NULL OR owner_entity_id IS NULL", name="single_site_owner"),
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)",
            name="valid_site_coordinates",
        ),
        UniqueConstraint("site_id", name="uq_sites_site_id"),
        Index("uq_sites_name", "name", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    site_code: Mapped[str] = mapped_column(String(2), nullable=False)
    site_id: Mapped[str | None] = mapped_column(String(5))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(String(2000))
    numbering_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="short", server_default=text("'short'")
    )
    grid_square: Mapped[str | None] = mapped_column(String(8))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    owner_subject: Mapped[str | None] = mapped_column(String(255))
    owner_entity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("entities.id", ondelete="RESTRICT"), index=True
    )
    repeaters: Mapped[list["Repeater"]] = relationship(
        back_populates="site", passive_deletes=True
    )

    @validates("site_code")
    def validate_site_code(self, key, value):
        if (
            not isinstance(value, str)
            or len(value) != 2
            or SITE_CODE_RE.fullmatch(value) is None
        ):
            raise ValueError("invalid site code")
        return value

    @validates("country_code")
    def validate_country_code(self, key, value):
        if (
            not isinstance(value, str)
            or len(value) != 2
            or not value.isascii()
            or not value.isalpha()
        ):
            raise ValueError("invalid country code")
        return value.upper()

    @validates("site_id")
    def validate_site_id(self, key, value):
        if value is None:
            return value
        if (
            not isinstance(value, str)
            or len(value) != 5
            or not value.isascii()
            or not value.isdigit()
            or value == "00000"
        ):
            raise ValueError("invalid Site ID")
        return value

    @validates("name")
    def validate_name(self, key, value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 200:
            raise ValueError("invalid site name")
        return value.strip()

    @validates("numbering_mode")
    def validate_numbering_mode(self, key, value):
        if value not in {"short", "extended"}:
            raise ValueError("invalid site numbering mode")
        return value

    def to_dict(self):
        return {
            "id": self.id,
            "country_code": self.country_code,
            "site_code": self.site_code,
            "site_id": self.site_id,
            "name": self.name,
            "location": self.location,
            "description": self.description,
            "numbering_mode": self.numbering_mode,
            "grid_square": self.grid_square,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "owner_entity_id": self.owner_entity_id,
            "owner_subject": self.owner_subject,
            "owner_type": (
                "entity"
                if self.owner_entity_id is not None
                else "user"
                if self.owner_subject is not None
                else "system"
            ),
        }


class SiteIdAllocation(Base):
    """Permanent reservation for an allocated Site ID until explicitly cleared."""

    __tablename__ = "site_id_allocations"
    __table_args__ = (UniqueConstraint("site_pk", name="uq_site_id_allocation_site"),)

    site_id: Mapped[str] = mapped_column(String(5), primary_key=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    site_code: Mapped[str] = mapped_column(String(2), nullable=False)
    site_pk: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sites.id", ondelete="SET NULL")
    )
    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

class Repeater(Base):
    """A repeater registered at a Portal-managed site."""

    __tablename__ = "repeaters"
    __table_args__ = (
        CheckConstraint(
            "node_id > 0 AND node_id <= 9223372036854775807", name="positive_repeater_node_id"
        ),
        CheckConstraint(
            "registration_source IS NULL OR length(registration_source) BETWEEN 1 AND 128",
            name="valid_registration_source",
        ),
    )

    node_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sites.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    call_sign: Mapped[str | None] = mapped_column(String(32))
    frequency: Mapped[str | None] = mapped_column(String(64))
    tone: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    last_registration_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_registration_ip: Mapped[str | None] = mapped_column(String(45))
    registration_source: Mapped[str | None] = mapped_column(String(128))
    site: Mapped[Site] = relationship(back_populates="repeaters")

    @validates("node_id")
    def validate_node_id(self, key, value):
        if type(value) is not int or not 0 < value <= 2**63 - 1:
            raise ValueError("invalid Node ID")
        return value

    @validates("call_sign")
    def validate_call_sign(self, key, value):
        if value is None:
            return value
        return normalize_call_sign(value)

    def to_dict(self, *, lifetime_seconds=None, now=None):
        from portal_backend.registration import registration_freshness

        return {
            "node_id": self.node_id,
            "site_id": self.site_id,
            "site_name": self.site.name if self.site else None,
            "site_code": self.site.site_code if self.site else None,
            "call_sign": self.call_sign,
            "frequency": self.frequency,
            "tone": self.tone,
            "enabled": bool(self.enabled),
            "last_registration_at": self.last_registration_at.isoformat()
            if self.last_registration_at
            else None,
            "last_registration_ip": self.last_registration_ip,
            "registration_source": self.registration_source,
            "registration_freshness": registration_freshness(
                self.last_registration_at, lifetime_seconds=lifetime_seconds, now=now
            ),
        }


class PortalEntityProfile(Base):
    """Portal-owned organization metadata for a shared Entity."""

    __tablename__ = "portal_entity_profiles"
    __table_args__ = (
        CheckConstraint("max_sites >= 0", name="valid_portal_entity_site_limit"),
    )

    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    website: Mapped[str | None] = mapped_column(String(512))
    max_sites: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default=text("5")
    )


class PortalEntityMember(Base):
    """Portal-specific Staff and Viewer memberships for a shared Entity."""

    __tablename__ = "portal_entity_members"
    __table_args__ = (
        CheckConstraint("role IN ('Staff', 'Viewer')", name="valid_portal_entity_member_role"),
    )

    entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)


class PortalUser(Base):
    """Provider-neutral user directory populated by a deployment sync process."""

    __tablename__ = "portal_users"

    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    call_signs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    @validates("subject")
    def validate_subject(self, key, value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 255:
            raise ValueError("invalid user subject")
        return value.strip()

    @validates("display_name")
    def validate_display_name(self, key, value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 255:
            raise ValueError("invalid user display name")
        return value.strip()

    @validates("call_signs")
    def normalize_call_signs(self, key, values):
        normalized = [normalize_call_sign(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("user call signs must be unique")
        return normalized
