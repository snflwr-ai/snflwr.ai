"""Tests for the false positive submission endpoint and DB methods."""
import json
import pytest
from pathlib import Path


class TestFalsePositiveDB:
    """Tests for DatabaseManager.insert_false_positive, get_false_positives, mark_false_positive_reviewed."""

    def _make_db(self, tmp_path):
        from storage.database import DatabaseManager
        db = DatabaseManager(db_path=tmp_path / "test.db")
        db.initialize_database()
        # Disable FK enforcement so we don't need a real child_profiles row
        db.execute_write("PRAGMA foreign_keys = OFF")
        return db

    def test_insert_returns_int_id(self, tmp_path):
        """insert_false_positive returns the actual row ID, not just rowcount."""
        db = self._make_db(tmp_path)
        fp_id = db.insert_false_positive(
            profile_id="prof-1",
            message_text="how to make a bomb for class",
            block_reason="Prohibited keyword: bomb",
            triggered_keywords='["bomb"]',
            educator_note="Chemistry homework",
        )
        assert isinstance(fp_id, int)
        assert fp_id > 0
        # Verify the returned ID matches the actual inserted row
        rows = db.get_false_positives(reviewed=True)
        assert any(r["id"] == fp_id for r in rows)

    def test_get_false_positives_unreviewed(self, tmp_path):
        """get_false_positives(reviewed=False) returns unreviewed rows."""
        db = self._make_db(tmp_path)
        fp_id = db.insert_false_positive(
            profile_id="prof-1",
            message_text="test",
            block_reason="reason",
            triggered_keywords='["bomb"]',
        )
        rows = db.get_false_positives(reviewed=False)
        assert any(r["id"] == fp_id for r in rows)

    def test_mark_false_positive_reviewed(self, tmp_path):
        """Marking a row reviewed removes it from unreviewed list."""
        db = self._make_db(tmp_path)
        fp_id = db.insert_false_positive(
            profile_id="prof-1",
            message_text="test",
            block_reason="reason",
            triggered_keywords='["bomb"]',
        )
        db.mark_false_positive_reviewed(fp_id, reviewed_by="admin@school.edu")
        unreviewed = db.get_false_positives(reviewed=False)
        assert not any(r["id"] == fp_id for r in unreviewed)

    def test_get_false_positives_reviewed_all(self, tmp_path):
        """get_false_positives(reviewed=True) returns all rows including reviewed."""
        db = self._make_db(tmp_path)
        fp_id = db.insert_false_positive(
            profile_id="prof-1",
            message_text="test",
            block_reason="reason",
            triggered_keywords='["bomb"]',
        )
        db.mark_false_positive_reviewed(fp_id, "admin")
        all_rows = db.get_false_positives(reviewed=True)
        assert any(r["id"] == fp_id for r in all_rows)


class TestFalsePositiveEndpoint:
    """Tests for POST /api/safety/false-positive."""

    def test_endpoint_exists(self):
        """POST /api/safety/false-positive route is registered."""
        from api.server import app
        routes = [r.path for r in app.routes]
        assert any("false-positive" in r for r in routes)

    def test_submit_false_positive_unauthenticated(self):
        """Unauthenticated request is rejected."""
        from fastapi.testclient import TestClient
        from api.server import app
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/api/safety/false-positive",
            json={
                "profile_id": "prof-1",
                "message_text": "test",
                "block_reason": "reason",
            },
        )
        assert response.status_code in (401, 403)


class TestAdminFalsePositiveEndpoints:
    """Tests for GET/PATCH /api/admin/false-positives."""

    def test_list_false_positives_route_registered(self):
        """GET /api/admin/false-positives route is registered."""
        from api.server import app
        routes = [r.path for r in app.routes]
        assert any("false-positives" in r and "admin" in r for r in routes)

    def test_patch_false_positive_route_registered(self):
        """PATCH /api/admin/false-positives/{fp_id} route is registered."""
        from api.server import app
        routes = [r.path for r in app.routes]
        assert any("false-positives" in r and "fp_id" in r for r in routes)

    def test_list_false_positives_requires_admin(self):
        """Unauthenticated request to list false positives is rejected."""
        from fastapi.testclient import TestClient
        from api.server import app
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/admin/false-positives")
        assert response.status_code in (401, 403)

    def test_mark_reviewed_requires_admin(self):
        """Unauthenticated PATCH is rejected."""
        from fastapi.testclient import TestClient
        from api.server import app
        client = TestClient(app, raise_server_exceptions=False)
        response = client.patch(
            "/api/admin/false-positives/1",
            json={"reviewed_by": "admin@test.com"},
        )
        assert response.status_code in (401, 403)

    def test_db_roundtrip_via_methods(self, tmp_path):
        """Full roundtrip: insert → list unreviewed → mark reviewed → gone from unreviewed."""
        from storage.database import DatabaseManager
        db = DatabaseManager(db_path=tmp_path / "test.db")
        db.initialize_database()
        db.execute_write("PRAGMA foreign_keys = OFF")
        fp_id = db.insert_false_positive(
            profile_id="prof-1",
            message_text="test",
            block_reason="reason",
            triggered_keywords='["bomb"]',
        )
        unreviewed = db.get_false_positives(reviewed=False)
        assert any(r["id"] == fp_id for r in unreviewed)
        db.mark_false_positive_reviewed(fp_id, "admin@school.edu")
        unreviewed_after = db.get_false_positives(reviewed=False)
        assert not any(r["id"] == fp_id for r in unreviewed_after)
        all_rows = db.get_false_positives(reviewed=True)
        assert any(r["id"] == fp_id and r["reviewed_by"] == "admin@school.edu" for r in all_rows)
