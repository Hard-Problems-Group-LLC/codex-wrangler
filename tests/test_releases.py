import pytest

from codex_wrangler.models import CodexWranglerError
from codex_wrangler.releases import (
    fetch_available_codex_versions,
    infer_codex_channel,
    is_exact_version,
    looks_like_preview,
    parse_dist_tags,
    resolve_install_version,
    resolve_upgrade_version,
)


def test_looks_like_preview_detects_preview_versions_and_selectors():
    assert looks_like_preview("__preview__") is True
    assert looks_like_preview("beta") is True
    assert looks_like_preview("0.30.0-beta.3") is True
    assert looks_like_preview("0.30.0") is False


def test_infer_codex_channel_detects_stable_beta_and_alpha_values():
    assert infer_codex_channel("latest") == "stable"
    assert infer_codex_channel("0.30.0") == "stable"
    assert infer_codex_channel("0.31.0-beta.2") == "beta"
    assert infer_codex_channel("alpha") == "alpha"


def test_is_exact_version_accepts_semver_and_prerelease_strings():
    assert is_exact_version("0.30.0") is True
    assert is_exact_version("0.30.0-beta.3") is True
    assert is_exact_version("latest") is False


def test_parse_dist_tags_rejects_non_object_payload():
    with pytest.raises(CodexWranglerError):
        parse_dist_tags('["latest"]')


def test_fetch_available_codex_versions_maps_dist_tags(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        "codex_wrangler.releases.fetch_codex_dist_tags",
        lambda npm_name, project_root, env=None, timeout_seconds=None: {
            "latest": "0.30.0",
            "beta": "0.31.0-beta.2",
            "alpha": "0.31.0-alpha.1",
        },
    )

    versions = fetch_available_codex_versions("npm", tmp_path)

    assert versions == {
        "stable": "0.30.0",
        "beta": "0.31.0-beta.2",
        "alpha": "0.31.0-alpha.1",
    }


def test_fetch_available_codex_versions_rejects_malformed_supported_tag(
    monkeypatch,
    tmp_path,
):
    """A malformed secondary tag cannot enter active managed authority."""

    monkeypatch.setattr(
        "codex_wrangler.releases.fetch_codex_dist_tags",
        lambda npm_name, project_root, env=None, timeout_seconds=None: {
            "latest": "0.30.0",
            "beta": "bogus",
            "alpha": "0.31.0-alpha.1",
        },
    )

    with pytest.raises(CodexWranglerError, match="did not resolve.*exact version"):
        fetch_available_codex_versions("npm", tmp_path)


def test_resolve_install_version_uses_channel_latest_request(
    monkeypatch,
    tmp_path,
    config_factory,
):
    config = config_factory(
        tmp_path,
        codex_selector="latest",
        codex_channel="beta",
        codex_version="latest",
        version_source="explicit --channel latest request",
    )
    monkeypatch.setattr(
        "codex_wrangler.releases.fetch_available_codex_versions",
        lambda npm_name, project_root, env=None, timeout_seconds=None: {
            "stable": "0.30.0",
            "beta": "0.31.0-beta.2",
            "alpha": "0.31.0-alpha.1",
        },
    )

    resolved = resolve_install_version(config, "npm")

    assert resolved.codex_channel == "beta"
    assert resolved.codex_version == "0.31.0-beta.2"
    assert resolved.available_versions["stable"] == "0.30.0"


def test_resolve_upgrade_version_uses_known_latest_for_selected_channel(
    tmp_path,
    config_factory,
):
    config = config_factory(
        tmp_path,
        operation="upgrade",
        codex_selector="latest",
        codex_channel="alpha",
        codex_version="latest",
        available_versions={
            "stable": "0.30.0",
            "beta": "0.31.0-beta.2",
            "alpha": "0.31.0-alpha.1",
        },
    )

    resolved = resolve_upgrade_version(config)

    assert resolved.codex_version == "0.31.0-alpha.1"
    assert resolved.version_source == "known alpha channel from local metadata"


def test_resolve_upgrade_version_rejects_corrupt_persisted_catalog_value(
    tmp_path,
    config_factory,
):
    """A root-only legacy catalog cannot inject a non-version npm spec."""

    config = config_factory(
        tmp_path,
        operation="upgrade",
        codex_selector="latest",
        codex_channel="stable",
        codex_version="0.29.0",
        available_versions={"stable": "file:../../foreign-package"},
    )

    with pytest.raises(CodexWranglerError, match="not an exact Codex version"):
        resolve_upgrade_version(config)


def test_resolve_upgrade_version_accepts_exact_version_for_selected_channel(
    tmp_path,
    config_factory,
):
    config = config_factory(
        tmp_path,
        operation="upgrade",
        codex_selector="0.31.0-beta.1",
        codex_channel="beta",
        codex_version="0.31.0-beta.1",
        available_versions={
            "stable": "0.30.0",
            "beta": "0.31.0-beta.2",
            "alpha": "0.31.0-alpha.1",
        },
    )

    resolved = resolve_upgrade_version(config)

    assert resolved.codex_version == "0.31.0-beta.1"
    assert resolved.version_source == "explicit --version for beta channel"


def test_resolve_explicit_selector_version_rejects_exact_version_channel_mismatch(
    tmp_path,
    config_factory,
):
    config = config_factory(
        tmp_path,
        operation="upgrade",
        codex_selector="0.31.0-beta.1",
        codex_channel="stable",
        codex_version="0.31.0-beta.1",
        available_versions={
            "stable": "0.30.0",
            "beta": "0.31.0-beta.2",
            "alpha": "0.31.0-alpha.1",
        },
    )

    with pytest.raises(CodexWranglerError):
        resolve_upgrade_version(config)
