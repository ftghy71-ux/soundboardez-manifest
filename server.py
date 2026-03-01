from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "update_config.json"
HISTORY_PATH = BASE_DIR / "update_history.json"
GITHUB_API_BASE = "https://api.github.com"
LOCKED_GITHUB_REPO = "ami-nope/SoundboardEZ"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env_key = key.strip()
            env_val = value.strip().strip('"').strip("'")
            if env_key and env_key not in os.environ:
                os.environ[env_key] = env_val
    except OSError:
        return


load_env_file(BASE_DIR / ".env")
app.config["SECRET_KEY"] = (
    os.environ.get("FLASK_SECRET_KEY")
    or os.environ.get("ADMIN_KEY")
    or "dev-insecure-secret-change-me"
)


def manifest_tag_prefix() -> str:
    return os.environ.get("MANIFEST_TAG_PREFIX", "1.2").strip()


def manifest_asset_name() -> str:
    configured_name = os.environ.get("MANIFEST_ASSET_NAME", "SoundboardEZ_full.zip").strip()
    return configured_name or "SoundboardEZ_full.zip"


def default_channel(version: str, asset_name: str, asset_url: str) -> dict[str, Any]:
    return {
        "version": version,
        "mandatory": False,
        "patch_notes": "",
        "min_required_version": "",
        "asset": {
            "name": asset_name,
            "url": asset_url,
        },
    }


def build_default_config() -> dict[str, Any]:
    return {
        "github_repo": LOCKED_GITHUB_REPO,
        "channels": {
            "stable": default_channel(
                version="1.0.0",
                asset_name="SoundboardEZ.exe",
                asset_url="https://yourcdn.com/1.0.0/SoundboardEZ.exe",
            ),
            "beta": default_channel(
                version="1.0.0-beta.1",
                asset_name="SoundboardEZ-beta.exe",
                asset_url="https://yourcdn.com/1.0.0-beta.1/SoundboardEZ-beta.exe",
            ),
        },
    }


def build_default_history() -> dict[str, list[dict[str, Any]]]:
    return {"history": []}


def deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def save_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def load_json(path: Path, default_payload: Any) -> Any:
    if not path.exists():
        save_json(path, default_payload)
        return deep_copy(default_payload)

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        save_json(path, default_payload)
        return deep_copy(default_payload)


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_channel(raw_channel: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    channel = deep_copy(fallback)
    if not isinstance(raw_channel, dict):
        return channel

    if "version" in raw_channel:
        channel["version"] = str(raw_channel.get("version", "")).strip()
    channel["mandatory"] = coerce_bool(raw_channel.get("mandatory", channel["mandatory"]))
    channel["patch_notes"] = str(raw_channel.get("patch_notes", channel["patch_notes"])).strip()
    channel["min_required_version"] = str(
        raw_channel.get("min_required_version", channel["min_required_version"])
    ).strip()

    raw_asset = raw_channel.get("asset")
    if isinstance(raw_asset, dict):
        channel["asset"]["name"] = str(raw_asset.get("name", channel["asset"]["name"])).strip()
        channel["asset"]["url"] = str(raw_asset.get("url", channel["asset"]["url"])).strip()
    else:
        # Compatibility with legacy shape where asset fields were stored at channel root.
        channel["asset"]["name"] = str(raw_channel.get("asset_name", channel["asset"]["name"])).strip()
        channel["asset"]["url"] = str(raw_channel.get("url", channel["asset"]["url"])).strip()

    return channel


def normalize_config(raw_config: Any) -> dict[str, Any]:
    defaults = build_default_config()
    if not isinstance(raw_config, dict):
        return defaults

    raw_channels = raw_config.get("channels")
    if not isinstance(raw_channels, dict):
        # Backward compatibility with older format:
        # { "stable": {...}, "beta": {...} }
        raw_channels = {
            "stable": raw_config.get("stable"),
            "beta": raw_config.get("beta"),
        }

    normalized = {
        # Repository is fixed for this deployment; ignore any stored/user-provided values.
        "github_repo": LOCKED_GITHUB_REPO,
        "channels": {
            "stable": normalize_channel(raw_channels.get("stable"), defaults["channels"]["stable"]),
            "beta": normalize_channel(raw_channels.get("beta"), defaults["channels"]["beta"]),
        },
    }
    return normalized


def load_config() -> dict[str, Any]:
    return normalize_config(load_json(CONFIG_PATH, build_default_config()))


def save_config(config: dict[str, Any]) -> None:
    save_json(CONFIG_PATH, normalize_config(config))


def normalize_history(raw_history: Any) -> dict[str, list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    source: Any = raw_history

    if isinstance(raw_history, dict):
        source = raw_history.get("history", [])

    if isinstance(source, list):
        for entry in source:
            if not isinstance(entry, dict):
                continue
            entries.append(
                {
                    "timestamp": str(entry.get("timestamp", "")).strip(),
                    "channel": str(entry.get("channel", "")).strip(),
                    "old_version": str(entry.get("old_version", "")).strip(),
                    "new_version": str(entry.get("new_version", "")).strip(),
                    "mandatory": coerce_bool(entry.get("mandatory", False)),
                }
            )

    return {"history": entries}


def load_history() -> dict[str, list[dict[str, Any]]]:
    return normalize_history(load_json(HISTORY_PATH, build_default_history()))


def save_history(history: dict[str, list[dict[str, Any]]]) -> None:
    save_json(HISTORY_PATH, normalize_history(history))


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def append_history_entry(channel: str, old_version: str, new_version: str, mandatory: bool) -> None:
    history = load_history()
    history["history"].append(
        {
            "timestamp": utc_timestamp(),
            "channel": channel,
            "old_version": old_version,
            "new_version": new_version,
            "mandatory": mandatory,
        }
    )
    save_history(history)


def build_manifest(channel_config: dict[str, Any], version_override: str | None = None) -> dict[str, Any]:
    asset = channel_config.get("asset", {})
    asset_url = str(asset.get("url", "")).strip()

    return {
        "version": (version_override or str(channel_config.get("version", "")).strip()),
        "mandatory": coerce_bool(channel_config.get("mandatory", False)),
        "files": {
            "full_package": {
                "url": asset_url,
            }
        },
    }


def admin_key() -> str:
    return os.environ.get("ADMIN_KEY", "").strip()


def provided_admin_key() -> str:
    return (request.args.get("key") or "").strip()


def is_admin_authenticated() -> bool:
    return session.get("admin_authenticated") is True


def authorize_admin_from_query_key() -> bool:
    expected = admin_key()
    provided = provided_admin_key()
    if expected and provided and provided == expected:
        session["admin_authenticated"] = True
        return True
    return False


@app.before_request
def protect_admin_routes() -> None:
    if not request.path.startswith("/admin"):
        return

    expected_key = admin_key()
    if not expected_key:
        abort(503, description="Admin is disabled until ADMIN_KEY environment variable is configured.")

    open_admin_paths = {"/admin", "/admin/login", "/admin/logout"}
    if request.path in open_admin_paths:
        authorize_admin_from_query_key()
        return

    if not request.path.startswith("/admin/api") and not request.path.startswith("/admin/update"):
        return

    authed = is_admin_authenticated() or authorize_admin_from_query_key()
    if request.path.startswith("/admin/api") and not authed:
        return jsonify({"error": "Forbidden. Sign in from /admin first."}), 403
    if request.path.startswith("/admin/update") and not authed:
        return redirect(url_for("admin_panel"))


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "soundboardez-manifest-admin",
    }
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    return headers


def fetch_releases(repo: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{GITHUB_API_BASE}/repos/{repo}/releases",
        headers=github_headers(),
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return []

    releases: list[dict[str, Any]] = []
    for release in payload:
        if not isinstance(release, dict):
            continue
        releases.append(
            {
                "tag_name": str(release.get("tag_name", "")).strip(),
                "name": str(release.get("name") or release.get("tag_name") or "").strip(),
                "draft": coerce_bool(release.get("draft", False)),
                "prerelease": coerce_bool(release.get("prerelease", False)),
            }
        )
    return releases


def fetch_raw_releases(repo: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{GITHUB_API_BASE}/repos/{repo}/releases",
        headers=github_headers(),
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return []
    return [release for release in payload if isinstance(release, dict)]


def resolve_manifest_release_asset(
    repo: str,
    channel: str,
    tag_prefix: str,
    required_asset_name: str,
) -> dict[str, str] | None:
    releases = fetch_raw_releases(repo)
    for release in releases:
        if coerce_bool(release.get("draft", False)):
            continue
        if channel == "stable" and coerce_bool(release.get("prerelease", False)):
            continue

        tag_name = str(release.get("tag_name", "")).strip()
        if not tag_name:
            continue
        if tag_prefix and not tag_name.startswith(tag_prefix):
            continue

        assets = release.get("assets", [])
        if not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            asset_name = str(asset.get("name", "")).strip()
            asset_url = str(asset.get("browser_download_url", "")).strip()
            if asset_name == required_asset_name and asset_url:
                return {
                    "version": tag_name,
                    "asset_url": asset_url,
                }
    return None


def fetch_assets_for_tag(repo: str, tag: str) -> list[dict[str, str]]:
    safe_tag = quote(tag, safe="")
    response = requests.get(
        f"{GITHUB_API_BASE}/repos/{repo}/releases/tags/{safe_tag}",
        headers=github_headers(),
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return []

    assets: list[dict[str, str]] = []
    for asset in payload.get("assets", []):
        if not isinstance(asset, dict):
            continue
        assets.append(
            {
                "name": str(asset.get("name", "")).strip(),
                "url": str(asset.get("browser_download_url", "")).strip(),
            }
        )
    return assets


def compute_sha256_from_url(asset_url: str) -> str:
    digest = hashlib.sha256()
    with requests.get(asset_url, stream=True, timeout=120) as response:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                digest.update(chunk)
    return digest.hexdigest()


@app.route("/manifest")
def manifest() -> Any:
    config = load_config()
    selected_channel = (request.args.get("channel") or "stable").strip().lower()
    channel_key = "beta" if selected_channel == "beta" else "stable"
    channel_config = deep_copy(config["channels"][channel_key])
    github_repo = LOCKED_GITHUB_REPO
    resolved_version = str(channel_config.get("version", "")).strip()

    if github_repo:
        try:
            prefix = manifest_tag_prefix()
            release_asset = resolve_manifest_release_asset(
                repo=github_repo,
                channel=channel_key,
                tag_prefix=prefix,
                required_asset_name=manifest_asset_name(),
            )
            if release_asset:
                resolved_version = prefix or release_asset["version"]
                channel_config.setdefault("asset", {})
                channel_config["asset"]["url"] = release_asset["asset_url"]
        except requests.RequestException:
            # Fall back to configured manifest values when GitHub is unavailable.
            pass

    return jsonify(build_manifest(channel_config, version_override=resolved_version))


@app.route("/admin")
def admin_panel() -> Any:
    if not is_admin_authenticated() and not authorize_admin_from_query_key():
        return render_template("admin.html", authenticated=False, error="")

    config = load_config()
    history = load_history()["history"]
    history.sort(key=lambda entry: entry.get("timestamp", ""), reverse=True)
    return render_template(
        "admin.html",
        authenticated=True,
        config=config,
        locked_repo=LOCKED_GITHUB_REPO,
        required_asset_name=manifest_asset_name(),
        history=history,
    )


@app.post("/admin/login")
def admin_login() -> Any:
    expected = admin_key()
    entered = (request.form.get("key") or "").strip()
    if not expected:
        abort(503, description="Admin is disabled until ADMIN_KEY environment variable is configured.")

    if entered != expected:
        return render_template("admin.html", authenticated=False, error="Invalid admin key."), 403

    session["admin_authenticated"] = True
    return redirect(url_for("admin_panel"))


@app.post("/admin/logout")
def admin_logout() -> Any:
    session.pop("admin_authenticated", None)
    return redirect(url_for("admin_panel"))


@app.post("/admin/update/<channel>")
def update_channel(channel: str) -> Any:
    channel_key = channel.strip().lower()
    if channel_key not in {"stable", "beta"}:
        abort(404)

    config = load_config()
    channel_config = config["channels"][channel_key]

    old_version = str(channel_config.get("version", "")).strip()
    new_version = (request.form.get("version") or old_version).strip()
    mandatory = coerce_bool(request.form.get("mandatory"))

    patch_notes = (request.form.get("patch_notes") or channel_config.get("patch_notes", "")).strip()
    min_required = (
        request.form.get("min_required_version") or channel_config.get("min_required_version", "")
    ).strip()
    asset_name = (request.form.get("asset_name") or channel_config["asset"].get("name", "")).strip()
    asset_url = (request.form.get("asset_url") or channel_config["asset"].get("url", "")).strip()

    # Keep repository fixed regardless of request payload.
    config["github_repo"] = LOCKED_GITHUB_REPO

    # Auto-resolve asset URL from selected release if the UI did not provide one.
    if new_version and not asset_url:
        try:
            release_assets = fetch_assets_for_tag(LOCKED_GITHUB_REPO, new_version)
            required_name = manifest_asset_name()
            preferred = next(
                (item for item in release_assets if item.get("url") and item.get("name") == required_name),
                None,
            )
            if preferred is None:
                preferred = next((item for item in release_assets if item.get("url")), None)
            if preferred is not None:
                resolved_name = str(preferred.get("name", "")).strip()
                resolved_url = str(preferred.get("url", "")).strip()
                if resolved_name:
                    asset_name = resolved_name
                if resolved_url:
                    asset_url = resolved_url
        except requests.RequestException:
            pass

    config["channels"][channel_key] = {
        "version": new_version,
        "mandatory": mandatory,
        "patch_notes": patch_notes,
        "min_required_version": min_required,
        "asset": {
            "name": asset_name,
            "url": asset_url,
        },
    }

    save_config(config)
    append_history_entry(channel_key, old_version, new_version, mandatory)

    return redirect(url_for("admin_panel"))


@app.get("/admin/api/releases")
def admin_api_releases() -> Any:
    try:
        releases = fetch_releases(LOCKED_GITHUB_REPO)
    except requests.RequestException as exc:
        return jsonify({"error": "Failed to fetch releases from GitHub.", "details": str(exc)}), 502

    return jsonify({"repo": LOCKED_GITHUB_REPO, "releases": releases})


@app.get("/admin/api/assets")
def admin_api_assets() -> Any:
    tag = (request.args.get("tag") or "").strip()

    if not tag:
        return jsonify({"error": "Release tag is required."}), 400

    try:
        assets = fetch_assets_for_tag(LOCKED_GITHUB_REPO, tag)
    except requests.RequestException as exc:
        return jsonify({"error": "Failed to fetch release assets from GitHub.", "details": str(exc)}), 502

    return jsonify({"repo": LOCKED_GITHUB_REPO, "tag": tag, "assets": assets})


@app.post("/admin/api/sha256")
def admin_api_sha256() -> Any:
    payload = request.get_json(silent=True) or {}
    source_url = (payload.get("url") or request.form.get("url") or "").strip()
    if not source_url:
        return jsonify({"error": "Asset URL is required."}), 400

    try:
        hash_value = compute_sha256_from_url(source_url)
    except requests.RequestException as exc:
        return jsonify({"error": "Failed to download asset for hashing.", "details": str(exc)}), 502

    return jsonify({"sha256": hash_value})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
