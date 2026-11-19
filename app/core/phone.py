"""Phone-number normalisation (E.164), using Google's libphonenumber."""

from dataclasses import dataclass

import phonenumbers

# Most TEG leads are Pakistani; numbers without a +country prefix are read as PK.
DEFAULT_REGION = "PK"


@dataclass(frozen=True)
class NormalisedPhone:
    """A phone number as typed, plus its E.164 form when it could be parsed."""

    raw: str
    e164: str | None
    country_code: str | None


def normalise_phone(
    value: str | None, default_region: str = DEFAULT_REGION
) -> NormalisedPhone | None:
    """Parse ``value`` into E.164 (``+923001234567``).

    Args:
        value: Number as entered or imported (spaces, dashes, leading 0 are fine).
        default_region: Region used when the number has no ``+`` prefix.

    Returns:
        ``None`` for blank input. Otherwise the raw value, with ``e164`` and
        ``country_code`` set only if the number is valid.
    """
    if value is None or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = phonenumbers.parse(raw, None if raw.startswith("+") else default_region)
    except phonenumbers.NumberParseException:
        return NormalisedPhone(raw=raw, e164=None, country_code=None)
    if not phonenumbers.is_possible_number(parsed):
        return NormalisedPhone(raw=raw, e164=None, country_code=None)
    return NormalisedPhone(
        raw=raw,
        e164=phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        country_code=str(parsed.country_code),
    )
