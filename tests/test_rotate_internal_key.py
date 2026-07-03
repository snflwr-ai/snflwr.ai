import importlib.util
import re

_spec = importlib.util.spec_from_file_location(
    "rotate_internal_key", "scripts/rotate_internal_key.py"
)
rk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rk)


def _write(tmp_path, text):
    p = tmp_path / ".env"
    p.write_text(text)
    return str(p)


def test_rotate_moves_current_to_previous_and_stamps(tmp_path):
    path = _write(tmp_path, "FOO=bar\nINTERNAL_API_KEY=oldkey123\nBAZ=qux\n")
    rk.rotate_env(path)
    out = open(path).read()
    assert "INTERNAL_API_KEY_PREVIOUS=oldkey123" in out
    m = re.search(r"^INTERNAL_API_KEY=([0-9a-f]{64})$", out, re.M)
    assert m and m.group(1) != "oldkey123"  # new 64-hex key, different
    ca = re.search(r"^INTERNAL_API_KEY_CREATED_AT=(.+)$", out, re.M)
    assert ca
    from datetime import datetime
    datetime.fromisoformat(ca.group(1).replace("Z", "+00:00"))
    # unrelated lines preserved
    assert "FOO=bar" in out and "BAZ=qux" in out


def test_rotate_refuses_without_key(tmp_path):
    path = _write(tmp_path, "FOO=bar\n")
    import pytest
    with pytest.raises(ValueError):
        rk.rotate_env(path)


def test_rotate_updates_existing_previous_and_created(tmp_path):
    path = _write(
        tmp_path,
        "INTERNAL_API_KEY=curr\nINTERNAL_API_KEY_PREVIOUS=older\n"
        "INTERNAL_API_KEY_CREATED_AT=2020-01-01T00:00:00Z\n",
    )
    rk.rotate_env(path)
    out = open(path).read()
    assert "INTERNAL_API_KEY_PREVIOUS=curr" in out  # previous = the old current
    assert "INTERNAL_API_KEY=curr" not in re.sub(  # current changed
        r"INTERNAL_API_KEY_PREVIOUS=curr", "", out
    )
    assert out.count("INTERNAL_API_KEY_CREATED_AT=") == 1  # updated in place, not duped
