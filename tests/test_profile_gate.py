from core.profile_gate import no_profile_block_reason

MSG = (
    "No learning profile is set up yet — please ask your parent or teacher "
    "to create one in Settings before chatting."
)


def test_synthetic_and_missing_ids_block():
    assert no_profile_block_reason("safety_required_stud_1") == MSG
    assert no_profile_block_reason("safety_required_unknown") == MSG
    assert no_profile_block_reason("no_profile_x") == MSG
    assert no_profile_block_reason(None) == MSG
    assert no_profile_block_reason("") == MSG


def test_real_profile_passes():
    assert no_profile_block_reason("prof_teen") is None
    assert no_profile_block_reason("abc123") is None
