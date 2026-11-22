"""A Norwegian notice board's household address, for its bin collection days: the contract's NoticeBoardAddress.

Only a notice board with a Norwegian postcode has one, and only an address Kartverket's address register has in that
postcode is saved, spelled as Kartverket has it: the street (adressenavn), the house number with any letter ("12B")
and the postal town (poststed) in title case. It goes in the device's config (`notice_board.address`) and, as
"<street>|<house_number>|<postcode>|<city>", in v1:sub:address, which countdown's bins provider fetches the collection
days for. A new postcode, or one that isn't Norwegian, clears it.

An address says where someone lives: it is never logged.
"""

from typing import TYPE_CHECKING

from countdown_api.models import NoticeBoardAddress

from broker.clients import kartverket
from broker.services.postcodes import postcode_country

if TYPE_CHECKING:
    from broker.models import DeviceConfig

AddressUnavailableError = kartverket.AddressLookupUnavailableError

# The words of a postal town that stay lower case after its first ("Mo i Rana", "Bø i Telemark").
LOWER_WORDS = frozenset({"i", "og", "på"})


class AddressNotFoundError(Exception):
    """No address in the postcode matches the one given, or several do (`several`)."""

    def __init__(self, *, several: bool) -> None:
        """Say whether several addresses matched (or none)."""
        super().__init__("several addresses match" if several else "no address matches")
        self.several = several


def stored(config: DeviceConfig) -> NoticeBoardAddress | None:
    """Return the notice board's address, or None without one (or without a Norwegian postcode)."""
    if postcode_country(config.postcode) != "NO":
        return None
    if not (config.address_street and config.address_house_number and config.address_city):
        return None
    return NoticeBoardAddress(
        street=config.address_street, house_number=config.address_house_number, city=config.address_city
    )


def store(config: DeviceConfig, address: NoticeBoardAddress | None) -> None:
    """Save the address to the device's settings, or clear it (None)."""
    config.address_street = address.street if address else None
    config.address_house_number = address.house_number if address else None
    config.address_city = address.city if address else None


def text_of(address: NoticeBoardAddress) -> str:
    """Return the address as the settings page shows it, and as it's looked up: "Thorvald Meyers gate 10"."""
    return f"{address.street} {address.house_number}"


def member(config: DeviceConfig) -> str | None:
    """Return the notice board's v1:sub:address member, "<street>|<house_number>|<postcode>|<city>", or None."""
    address = stored(config)
    if address is None:
        return None
    return f"{address.street}|{address.house_number}|{config.postcode}|{address.city}"


def posted(config: DeviceConfig, postcode: str, text: str | None) -> NoticeBoardAddress | None:
    """Return the address to save with the notice board's new `postcode` (normalized), from the text posted.

    `text` is what was typed ("" to clear it), or None when none was posted, which keeps the one saved. The one saved
    is kept, unasked, while the postcode is too; a new postcode, or one that isn't Norwegian, clears it, unless a new
    address is posted with it. Anything else is looked up in the postcode (lookup). The config isn't changed: `store`
    the address once everything else saved with it is right.
    """
    if postcode_country(postcode) != "NO":
        return None
    saved = stored(config)
    same_postcode = postcode == config.postcode
    text = None if text is None else " ".join(text.split())
    if text is None:
        return saved if same_postcode else None
    if not text:
        return None
    if saved is not None and text == text_of(saved):
        return saved if same_postcode else None
    return lookup(text, postcode)


def lookup(text: str, postcode: str) -> NoticeBoardAddress:
    """Return the address Kartverket has for `text` in the Norwegian `postcode`, when exactly one matches.

    Raises AddressNotFoundError when none does, or several, and AddressUnavailableError when Kartverket couldn't be
    asked.
    """
    found = kartverket.search(text, postcode)
    if len(found) != 1:
        raise AddressNotFoundError(several=len(found) > 1)
    (address,) = found
    street, number, letter, town = (address.get(name) for name in ("adressenavn", "nummer", "bokstav", "poststed"))
    # A cadastral address (a farm's, by its numbers) has no street to collect the bins on.
    if not street or number is None or not town:
        raise AddressNotFoundError(several=False)
    return NoticeBoardAddress(street=street, house_number=f"{number}{letter or ''}", city=title_case(town))


def title_case(town: str) -> str:
    """Return a postal town, which Kartverket has in capitals ("OSLO", "MO I RANA"), in title case ("Mo i Rana")."""
    words = town.lower().split()
    return " ".join(
        word if index and word in LOWER_WORDS else "-".join(part.capitalize() for part in word.split("-"))
        for index, word in enumerate(words)
    )
