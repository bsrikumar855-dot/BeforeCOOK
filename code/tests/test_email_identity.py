"""F2: email identity edge cases (exact matching only; no provider-specific normalisation; lookalikes never equated)."""
from agent.identity import IdentityUniverse, norm_email, valid_email

USERS = [{"user_id": "user_1", "name": "Ana Lee", "email": "ana.lee@gmail.com", "phone": "+44 7700 900001",
          "email_hash": "a" * 64, "updated_at": "2026-01-01T00:00:00Z"},
         {"user_id": "user_2", "name": "Kenta Ito", "email": "kenta.ito+shop@outlook.com", "phone": "+81 90 0000 0002",
          "email_hash": "b" * 64, "updated_at": "2026-01-01T00:00:00Z"}]
U = IdentityUniverse(USERS, [])


def resolve(e):
    return U.resolve("case_x", e).user_ids


def test_case_insensitive_exact_match_and_trimming():
    assert resolve("ANA.LEE@GMAIL.COM") == ["user_1"]
    assert resolve("  ana.lee@gmail.com \t") == ["user_1"]


def test_dots_are_preserved_no_gmail_dot_removal():
    assert resolve("analee@gmail.com") == []
    assert resolve("a.na.lee@gmail.com") == []


def test_plus_addressing_is_not_collapsed():
    assert resolve("kenta.ito@outlook.com") == []
    assert resolve("kenta.ito+shop@outlook.com") == ["user_2"]


def test_malformed_addresses_are_rejected_without_crashing():
    for bad in ("ana..lee@gmail.com", ".ana@gmail.com", "ana.@gmail.com", "ana@@gmail.com", "ana@gmail", "@gmail.com",
                "", None, "ana lee@gmail.com", "ana@-gmail.com"):
        assert not valid_email(norm_email(bad))
        assert resolve(bad) == []
    assert U.classify("note", "contact ana..lee@gmail.com", "SAME_RECORD") == []


def test_unicode_lookalikes_are_not_equated():
    kelvin = "Kenta.ito+shop@outlook.com"              # KELVIN SIGN lowercases to ASCII 'k' with str.lower()
    cyrillic = "аna.lee@gmail.com"                     # Cyrillic a
    assert kelvin.lower().startswith("k") and norm_email(kelvin) != norm_email("kenta.ito+shop@outlook.com")
    assert resolve(kelvin) == [] and resolve(cyrillic) == []


def test_no_link_from_a_normalised_variant_or_a_name_alone():
    ids = U.classify("requester_email", "analee@gmail.com", "SAME_RECORD")
    assert all(not U.owners_of(i.kind, i.value) for i in ids)
    name_only = U.classify("requester_name", "Ana Lee", "SAME_RECORD")
    assert [i.kind for i in name_only] == ["name"] and not U.owners_of("name", "Ana Lee")
