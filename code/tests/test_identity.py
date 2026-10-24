from agent.identity import subject_owns
from agent.schema import Identifier
from factories import universe


def test_current_email_resolves_case_insensitively():
    s = universe().resolve("case_x", "RHEA.VALE@EXAMPLE.TEST")
    assert s.user_ids == ["user_901"]
    assert "user_identities.email (current)" in s.resolution[0]


def test_historical_email_resolves_and_brings_history():
    s = universe().resolve("case_x", "rhea.old@example.test")
    assert s.user_ids == ["user_901"]
    assert "d" * 64 in s.identifiers["email_hash"]                      # historical hash
    assert s.identifiers["email_hash"]["d" * 64].startswith("user_audit_log.old_value audit_9002")


def test_names_never_link():
    s = universe().resolve("case_x", "rhea.vale@example.test")
    assert not subject_owns(s, Identifier("name", "Rhea Vale", "full_name", "SAME_RECORD"))


def test_near_name_decoy_is_a_different_owner():
    u = universe()
    assert u.owners_of("email", "rhea.valentine@example.test") == {"user_903"}
    assert u.owners_of("email", "rhea.vale@example.test") == {"user_901"}


def test_integrity_seal_is_not_an_email_hash():
    u = universe()
    assert u.classify("integrity_seal", "sha256:" + "e" * 64, "SAME_RECORD") == []
    assert [i.kind for i in u.classify("correlation_key", "d" * 64, "SAME_RECORD")] == ["email_hash"]
