"""Whether a MET Norway warning's area covers a place (broker/services/panels.py `covers`), as countdown's board says.

getNotices sends a Norwegian device only the warnings covering its postcode's location, by the same even-odd point in
polygon test as countdown's renderer (notices/norway.py), so the kiosk and the e-paper board show the same warnings.
What getNotices answers with it is in tests/routes/test_touchscreen_panels.py.
"""

import pytest

from broker.services.panels import covers

# A square around Oslo's centre (longitude first, as GeoJSON has it), with a hole in its middle.
SQUARE = [[10.0, 59.0], [11.0, 59.0], [11.0, 60.0], [10.0, 60.0], [10.0, 59.0]]
HOLE = [[10.4, 59.4], [10.6, 59.4], [10.6, 59.6], [10.4, 59.6], [10.4, 59.4]]
BERGEN = [[5.0, 60.0], [6.0, 60.0], [6.0, 61.0], [5.0, 61.0], [5.0, 60.0]]


@pytest.mark.parametrize(
    ("geometry", "latitude", "longitude", "covered"),
    [
        ({"type": "Polygon", "coordinates": [SQUARE]}, 59.9116, 10.7505, True),
        ({"type": "Polygon", "coordinates": [SQUARE]}, 60.3913, 5.3221, False),
        ({"type": "Polygon", "coordinates": [SQUARE, HOLE]}, 59.5, 10.5, False),
        ({"type": "Polygon", "coordinates": [SQUARE, HOLE]}, 59.2, 10.2, True),
        ({"type": "MultiPolygon", "coordinates": [[BERGEN], [SQUARE]]}, 59.9116, 10.7505, True),
        ({"type": "MultiPolygon", "coordinates": [[BERGEN]]}, 59.9116, 10.7505, False),
        ({"type": "Polygon", "coordinates": [[[x, y, 0] for x, y in SQUARE]]}, 59.9116, 10.7505, True),
        ({"type": "Point", "coordinates": [10.7505, 59.9116]}, 59.9116, 10.7505, False),
        (None, 59.9116, 10.7505, False),
    ],
    ids=[
        "inside",
        "outside",
        "in-its-hole",
        "beside-its-hole",
        "in-one-of-many",
        "in-none",
        "with-altitude",
        "a-point",
        "none",
    ],
)
def test_a_warning_covers_a_place_in_its_area_and_none_of_its_holes(geometry, latitude, longitude, covered):
    assert covers(geometry, latitude, longitude) is covered
