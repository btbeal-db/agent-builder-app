"""Unit tests for OBO authentication logic."""

from __future__ import annotations

import os

import pytest

from backend.auth import set_user_token, get_user_token, get_workspace_client


class TestTokenContextVar:
    def test_set_and_get(self):
        set_user_token("test-token-123")
        assert get_user_token() == "test-token-123"
        # Clean up
        set_user_token(None)

    def test_default_is_none(self):
        set_user_token(None)
        assert get_user_token() is None


class TestGetWorkspaceClient:
    @pytest.mark.integration
    def test_no_token_returns_default_client(self):
        set_user_token(None)
        w = get_workspace_client()
        # Should return a WorkspaceClient using env var credentials
        assert w is not None

    def test_with_token_and_host(self):
        old_host = os.environ.get("DATABRICKS_HOST")
        os.environ["DATABRICKS_HOST"] = "https://test.cloud.databricks.com"
        set_user_token("obo-token-abc")
        try:
            w = get_workspace_client()
            assert w is not None
            assert w.config.host.rstrip("/") == "https://test.cloud.databricks.com"
        finally:
            set_user_token(None)
            if old_host:
                os.environ["DATABRICKS_HOST"] = old_host
            else:
                os.environ.pop("DATABRICKS_HOST", None)

    def test_sp_env_vars_restored_after_obo(self):
        """SP env vars are masked during OBO client creation but restored after."""
        os.environ["DATABRICKS_HOST"] = "https://test.cloud.databricks.com"
        os.environ["DATABRICKS_CLIENT_ID"] = "test-client-id"
        os.environ["DATABRICKS_CLIENT_SECRET"] = "test-secret"
        set_user_token("obo-token")
        try:
            get_workspace_client()
            # Env vars should be restored after the client is created
            assert os.environ.get("DATABRICKS_CLIENT_ID") == "test-client-id"
            assert os.environ.get("DATABRICKS_CLIENT_SECRET") == "test-secret"
        finally:
            set_user_token(None)
            os.environ.pop("DATABRICKS_HOST", None)
            os.environ.pop("DATABRICKS_CLIENT_ID", None)
            os.environ.pop("DATABRICKS_CLIENT_SECRET", None)
