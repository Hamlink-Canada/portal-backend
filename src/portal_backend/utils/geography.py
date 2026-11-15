"""Maidenhead grid-square location and country lookup."""

from functools import lru_cache

from rpt_common.utils.regex import GRID_SQUARE_RE


def grid_square_center(grid_square):
    """Return the center latitude/longitude for a four- or six-character locator."""
    if not isinstance(grid_square, str):
        raise ValueError("grid square is required")
    grid = grid_square.strip().upper()
    if not GRID_SQUARE_RE.fullmatch(grid):
        raise ValueError("grid square must be a valid four- or six-character Maidenhead locator")

    lon = -180 + (ord(grid[0]) - ord("A")) * 20
    lon += int(grid[2]) * 2
    lat = -90 + (ord(grid[1]) - ord("A")) * 10
    lat += int(grid[3])
    if len(grid) == 4:
        lon += 1
        lat += 0.5
    else:
        lon += (ord(grid[4]) - ord("A")) * (2 / 24) + (1 / 24)
        lat += (ord(grid[5]) - ord("A")) * (1 / 24) + (1 / 48)
    return grid, lat, lon


def grid_square_bounds(grid_square):
    """Return south, west, north, east bounds for a Maidenhead locator."""
    grid, latitude, longitude = grid_square_center(grid_square)
    latitude_half_size = 0.5 if len(grid) == 4 else 1 / 48
    longitude_half_size = 1 if len(grid) == 4 else 1 / 24
    return (
        grid,
        latitude - latitude_half_size,
        longitude - longitude_half_size,
        latitude + latitude_half_size,
        longitude + longitude_half_size,
    )


@lru_cache(maxsize=1)
def _country_resolver():
    from coordinates2country.coordinates2country import Coordinates2Country

    return Coordinates2Country()


def country_for_grid_square(grid_square):
    """Resolve a Maidenhead grid center to an ISO 3166-1 alpha-2 country code."""
    grid, latitude, longitude = grid_square_center(grid_square)
    return resolve_site_location(grid, latitude, longitude)


def resolve_site_location(grid_square, latitude=None, longitude=None):
    """Resolve country from an optional precise point, falling back to grid center."""
    grid, grid_latitude, grid_longitude = grid_square_center(grid_square)
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude and longitude must be provided together")
    if latitude is None:
        latitude, longitude = grid_latitude, grid_longitude
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        raise ValueError("invalid geographic point")
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid geographic point") from error
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("geographic point is outside valid latitude/longitude bounds")
    _, south, west, north, east = grid_square_bounds(grid)
    # Maidenhead cells are closed on the south/west edges and open on the
    # north/east edges, except at the geographic poles/dateline.
    latitude_in_grid = south <= latitude < north or (north == 90 and latitude == 90)
    longitude_in_grid = west <= longitude < east or (east == 180 and longitude == 180)
    if not latitude_in_grid or not longitude_in_grid:
        raise ValueError("precise coordinates must fall inside the supplied grid square")
    try:
        country_code = _country_resolver().country_code(latitude, longitude)
    except Exception as error:
        raise ValueError("could not determine a country for this location") from error
    if not isinstance(country_code, str) or len(country_code.strip()) != 2:
        raise ValueError("could not determine a country for this location")
    return grid, latitude, longitude, country_code.strip().upper()
