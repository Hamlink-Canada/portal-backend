"""Country-based Site Code and Site ID allocation."""

import secrets

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from portal_backend.models import SiteIdAllocation
from portal_backend.utils.site_codes import site_codes_for_country

LOCAL_NUMBER_COUNT = 1000


def site_code_options(session, country_code, mapping_path=None):
    codes = site_codes_for_country(country_code, mapping_path)
    rows = session.execute(
        select(SiteIdAllocation.site_code, func.count())
        .where(SiteIdAllocation.site_code.in_(codes))
        .group_by(SiteIdAllocation.site_code)
    ).all()
    reserved = dict(rows)
    return [
        {
            "site_code": code,
            "available_numbers": max(0, LOCAL_NUMBER_COUNT - reserved.get(code, 0)),
        }
        for code in codes
    ]


def allocate_site_id(session, site, *, selected_site_code=None, mapping_path=None):
    """Reserve a random unused three-digit suffix beneath an eligible Site Code."""
    if site.id is None:
        session.flush()
    options = [item for item in site_code_options(session, site.country_code, mapping_path) if item["available_numbers"]]
    eligible = {item["site_code"] for item in options}

    if selected_site_code is None:
        if not options:
            raise ValueError("no Site Code has available Site IDs for this country")
        if len(options) != 1:
            raise ValueError("select a Site Code because multiple codes have available Site IDs")
        selected_site_code = options[0]["site_code"]
    if selected_site_code not in eligible:
        raise ValueError("selected Site Code is unavailable for this country")

    start = secrets.randbelow(LOCAL_NUMBER_COUNT)
    for offset in range(LOCAL_NUMBER_COUNT):
        suffix = (start + offset) % LOCAL_NUMBER_COUNT
        candidate = f"{selected_site_code}{suffix:03d}"
        allocation = SiteIdAllocation(
            site_id=candidate,
            country_code=site.country_code,
            site_code=selected_site_code,
            site_pk=site.id,
        )
        try:
            with session.begin_nested():
                session.add(allocation)
                session.flush()
            site.site_code = selected_site_code
            site.site_id = candidate
            session.flush()
            return candidate
        except IntegrityError:
            # Another creator may have claimed this suffix after the availability
            # check. The unique reservation key makes retry safe.
            continue
    raise ValueError("no unused Site ID remains for the selected Site Code")
