"""Unit tests for lead intake parsing (no database)."""

from datetime import UTC, datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.services.form_answers import AliasMap, normalise_key, parse_notes, parse_yes_no
from app.services.import_service import parse_date
from app.services.website_intake import build_incoming, flatten_form

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


def test_elementor_contact_form_by_label_with_country_code():
    """TEG's /contact/ form (Elementor 'simple' webhook data)."""
    body = (
        b"Full Name=Sana Iqbal&Country Code=United Arab Emirates  %2B971&Phone Number=0501234567"
        b"&Email Address=sana%40example.com&Program of Interest=Portugal"
        b"&How did you hear about us%3F=Instagram&Question=Is D2 open for families%3F"
        b"&Date=November 27, 2026&Time=10:00 am"
        b"&Page URL=https%3A%2F%2Ftegconsultancy.com%2Fcontact%2F"
        b"&User Agent=Mozilla&Remote IP=1.2.3.4&Powered by=Elementor"
    )
    incoming = build_incoming(flatten_form(body))
    assert incoming.full_name == "Sana Iqbal"
    assert incoming.phone == "+971501234567"
    assert incoming.email == "sana@example.com"
    assert incoming.country_of_interest == "Portugal"
    assert incoming.sub_source == "Instagram"
    assert incoming.notes == "Is D2 open for families?"
    assert incoming.answers["Page URL"] == "https://tegconsultancy.com/contact/"
    assert "User Agent" not in incoming.answers and "Remote IP" not in incoming.answers


def test_elementor_assessment_form_advanced_data_reads_labels_not_ids():
    """RBI/CBI popups: the field with id 'name' is Current Citizenship, 'message' the mobile."""
    fields = {
        "name": ("Current Citizenship", "select", "Pakistan"),
        "email": ("Country of Birth", "select", "Pakistan"),
        "field_44b3e2f": ("Marital Status", "select", "Married"),
        "field_cbb58fe": ("Number of Children", "select", "2"),
        "field_4272804": ("Previous Visa Rejections", "select", "None"),
        "field_5950595": ("Full Name", "text", "Kamran Akhtar"),
        "message": ("Mobile Number", "tel", "+923214567890"),
        "field_a250e11": ("Email Address", "email", "kamran@example.com"),
        "field_04a2cf4": ("I am Interested in", "select", " I am Interested in"),
        "field_b16f720": ("Can you Invest minimum of", "select", "Portugal - EUR 500K"),
        "field_8edf133": ("Preferred mode of Investment", "select", "Choose One"),
        "field_c9fb160": ("How did you hear about us?", "select", "Facebook"),
        "field_ac5a2e1": ("", "recaptcha", "token"),
    }
    pairs = [
        ("form[id]", "65fc1250"),
        ("form[name]", "RBI Assessment"),
        ("meta[page_url][title]", "Page URL"),
        ("meta[page_url][value]", "https://tegconsultancy.com/residence-by-investment/"),
    ]
    for fid, (title, ftype, value) in fields.items():
        pairs += [
            (f"fields[{fid}][id]", fid),
            (f"fields[{fid}][type]", ftype),
            (f"fields[{fid}][title]", title),
            (f"fields[{fid}][value]", value),
        ]
    body = urlencode(pairs).encode()
    incoming = build_incoming(flatten_form(body))
    assert incoming.full_name == "Kamran Akhtar"
    assert incoming.phone == "+923214567890"
    assert incoming.email == "kamran@example.com"
    assert incoming.budget_text == "Portugal - EUR 500K"
    assert incoming.country_of_interest is None  # the placeholder " I am Interested in" is blank
    assert incoming.sub_source == "Facebook"
    assert incoming.source_form_name == "RBI Assessment"
    assert incoming.answers["Current Citizenship"] == "Pakistan"
    assert incoming.answers["Number of Children"] == "2"
    assert "Preferred mode of Investment" not in incoming.answers  # "Choose One"
    assert not any("recaptcha" in k.lower() or v == "token" for k, v in incoming.answers.items())


def test_import_dates():
    dubai = ZoneInfo("Asia/Dubai")
    assert parse_date("2026-11-10 20:05:42 UTC", dubai) == datetime(
        2026, 11, 10, 20, 5, 42, tzinfo=ZoneInfo("UTC")
    )
    local = parse_date("10/11/2026", dubai)
    assert local is not None and local.astimezone(UTC).hour == 20  # midnight Dubai = 20:00 UTC
    assert parse_date("not a date", dubai) is None
