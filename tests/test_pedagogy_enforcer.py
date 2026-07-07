from config import system_config as settings


def test_guidance_flags_default_safe():
    assert settings.GUIDANCE_ENFORCEMENT_ENABLED is False
    assert settings.GUIDANCE_ENFORCER_CONFIRM_MODEL == ""
    assert settings.GUIDANCE_ENFORCER_TIMEOUT_S == 8.0
