"""Local privileged maintenance commands for a Portal database."""

import argparse

from sqlalchemy import delete, select

from portal_backend.config import load_settings
from portal_backend.database import create_database_engine, create_session_factory
from portal_backend.models import Repeater, Site, SiteIdAllocation


def clear_site_id(site_id, *, session_factory):
    """Release a reserved Site ID by explicit local administrator action."""
    if not isinstance(site_id, str) or len(site_id) != 5 or not site_id.isascii() or not site_id.isdigit():
        raise ValueError("Site ID must be exactly five digits")

    with session_factory() as session, session.begin():
        site = session.scalar(select(Site).where(Site.site_id == site_id).with_for_update())
        if site is not None:
            has_repeaters = session.scalar(
                select(Repeater.node_id).where(Repeater.site_id == site.id).limit(1)
            )
            if has_repeaters is not None:
                raise ValueError("remove or move this site's repeaters before clearing its Site ID")
            site.site_id = None

        session.execute(delete(SiteIdAllocation).where(SiteIdAllocation.site_id == site_id))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="portal-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    clear_parser = commands.add_parser(
        "clear-site-id", help="explicitly release a reserved five-digit Site ID"
    )
    clear_parser.add_argument("site_id", help="the complete five-digit Site ID")
    args = parser.parse_args(argv)

    database_url = load_settings().database_url
    if not database_url:
        parser.error("PORTAL_DATABASE_URL must be configured")
    engine = create_database_engine(database_url)
    try:
        clear_site_id(args.site_id, session_factory=create_session_factory(engine))
    except ValueError as error:
        parser.error(str(error))
    finally:
        engine.dispose()
    print(f"Cleared Site ID {args.site_id}.")


if __name__ == "__main__":
    main()
