"""Unit tests for lead intake parsing (no database)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.services.form_answers import AliasMap, normalise_key, parse_notes, parse_yes_no
from app.services.import_service import parse_date
from app.services.website_intake import build_incoming

ALIASES = AliasMap(
    {
        "what_is_your_budget?": "budget_text",
        "whatsapp_number": "whatsapp_e164",
        "city": "city",
        "full name": "full_name",
        "are_you_including_family_members?": "has_family",
    }
)


def test_normalise_key_strips_broken_prefix_and_spaces():
    assert normalise_key("�_City") == "city"
    assert normalise_key("  Full   Name ") == "full_name"


def test_jaldi_notes_become_answers_and_free_text_stays():
    notes = "what_is_your_budget?: 20-30 lakh\ncity: Lahore\nPlease call after 5pm"
    answers, rest = parse_notes(notes)
    assert answers == {"what_is_your_budget?": "20-30 lakh", "city": "Lahore"}
    assert rest == "Please call after 5pm"


def test_single_line_note_is_not_a_form():
    assert parse_notes("Called: no answer") == ({}, "Called: no answer")


def test_answers_fill_columns_and_are_all_kept():
    extracted = ALIASES.extract(
        {
            "What_Is_Your_Budget?": "USD 100k",
            "whatsapp_number": "+92 300 1234567",
            "Full Name": "Ayesha Khan",
            "are_you_including_family_members?": "Yes",
            "favourite_colour": "blue",
        }
    )
    assert extracted.columns == {
        "budget_text": "USD 100k",
        "whatsapp_e164": "+92 300 1234567",
        "full_name": "Ayesha Khan",
        "has_family": True,
    }
    assert extracted.answers["favourite_colour"] == "blue"
    assert extracted.answers["what_is_your_budget?"] == "USD 100k"


def test_yes_no():
    assert parse_yes_no("No, just me") is False
    assert parse_yes_no("maybe") is None


def test_website_payload_maps_common_field_names():
    incoming = build_incoming(
        {
            "name": "Bilal Raza",
            "Phone": "0300 1112223",
            "email": "B@example.com",
            "message": "Interested in E-2",
            "utm_source": "google",
            "budget": "50k",
            "fields": {"programme": "USA E-2"},
            "_gotcha": "",
        }
    )
    assert incoming.full_name == "Bilal Raza"
    assert incoming.phone == "0300 1112223"
    assert incoming.notes == "Interested in E-2"
    assert incoming.sub_source == "google"
    assert incoming.answers == {"utm_source": "google", "budget": "50k", "programme": "USA E-2"}


def test_import_dates():
    dubai = ZoneInfo("Asia/Dubai")
    assert parse_date("2026-11-10 20:05:42 UTC", dubai) == datetime(
        2026, 11, 10, 20, 5, 42, tzinfo=ZoneInfo("UTC")
    )
    local = parse_date("10/11/2026", dubai)
    assert local is not None and local.astimezone(UTC).hour == 20  # midnight Dubai = 20:00 UTC
    assert parse_date("not a date", dubai) is None
