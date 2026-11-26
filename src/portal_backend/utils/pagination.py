"""Shared helpers for bounded list API responses."""

from sqlalchemy import func, select


def page_query(session, statement, *, page, page_size):
    """Run a filtered SELECT with a matching count and return page metadata."""
    total = session.scalar(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ) or 0
    items = session.scalars(
        statement.order_by(None).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return items, {
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


def paginated_response(items, pagination, **extra):
    """Build a consistent JSON envelope for paged resources."""
    return {"items": items, "pagination": pagination, **extra}
