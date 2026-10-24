from unittest.mock import patch

from broker.services import stops


def test_resolve_keeps_the_saved_order_across_both_sources():
    tfl_stops = [{"id": "940GZZLUKNG", "name": "King's Cross"}, {"id": "490000254W", "name": "Euston Road"}]
    quay = {"id": "NSR:Quay:11145", "source": "entur", "name": "Jernbanetorget", "mode": "metro"}
    with (
        patch("broker.services.stops.tfl.resolve_stops", return_value=tfl_stops) as tfl_resolve,
        patch("broker.services.stops.entur.resolve_stops", return_value={quay["id"]: quay}) as entur_resolve,
    ):
        resolved = stops.resolve(["940GZZLUKNG", "NSR:Quay:11145", "490000254W"])

    assert [(stop["id"], stop["source"]) for stop in resolved] == [
        ("940GZZLUKNG", "tfl"),
        ("NSR:Quay:11145", "entur"),
        ("490000254W", "tfl"),
    ]
    tfl_resolve.assert_called_once_with(["940GZZLUKNG", "490000254W"])
    entur_resolve.assert_called_once_with(["NSR:Quay:11145"])


def test_resolve_gives_an_entur_stop_it_couldnt_describe_a_placeholder():
    with (
        patch("broker.services.stops.tfl.resolve_stops", return_value=[]),
        patch("broker.services.stops.entur.resolve_stops", return_value={}),
    ):
        resolved = stops.resolve(["NSR:StopPlace:58366"])

    assert resolved == [
        {
            "id": "NSR:StopPlace:58366",
            "source": "entur",
            "name": "NSR:StopPlace:58366",
            "mode": "unknown",
            "letter": "",
            "lines": [],
        }
    ]
