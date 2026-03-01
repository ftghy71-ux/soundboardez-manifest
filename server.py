import json
import os
import re
import time
from typing import Any, Dict, Optional
from urllib import error, request

from flask import Flask, jsonify

app = Flask(__name__)

GITHUB_OWNER = os.getenv("GITHUB_OWNER", "ami-nope")
GITHUB_REPO = os.getenv("GITHUB_REPO", "SoundboardEZ")
RELEASE_ASSET_NAME = os.getenv("RELEASE_ASSET_NAME", "SoundboardEZ.exe")
MANDATORY_UPDATE = os.getenv("MANDATORY_UPDATE", "false").strip().lower() == "true"
GITHUB_API_TIMEOUT = float(os.getenv("GITHUB_API_TIMEOUT", "10"))
MANIFEST_CACHE_SECONDS = int(os.getenv("MANIFEST_CACHE_SECONDS", "120"))
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()

SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
_manifest_cache: Dict[str, Any] = {"data": None, "expires_at": 0.0}


def _latest_release_api_url() -> str:
    return f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"


def _github_headers() -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "soundboardez-manifest",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def _http_get_text(url: str) -> str:
    req = request.Request(url=url, headers=_github_headers())
    try:
        with request.urlopen(req, timeout=GITHUB_API_TIMEOUT) as response:
            return response.read().decode("utf-8")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub request failed ({exc.code}): {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"GitHub request failed: {exc.reason}") from exc


def _http_get_json(url: str) -> Dict[str, Any]:
    return json.loads(_http_get_text(url))


def _normalize_version(tag_name: str) -> str:
    return tag_name[1:] if tag_name.startswith(("v", "V")) else tag_name


def _extract_sha256_from_asset(asset: Dict[str, Any]) -> Optional[str]:
    digest = str(asset.get("digest", "")).strip()
    if digest.lower().startswith("sha256:"):
        candidate = digest.split(":", 1)[1].strip().lower()
        if SHA256_RE.fullmatch(candidate):
            return candidate
    return None


def _extract_sha256_from_text(content: str) -> Optional[str]:
    match = SHA256_RE.search(content)
    return match.group(0).lower() if match else None


def _find_exe_asset(assets: Any) -> Optional[Dict[str, Any]]:
    for asset in assets:
        if asset.get("name") == RELEASE_ASSET_NAME:
            return asset
    for asset in assets:
        name = str(asset.get("name", "")).lower()
        if name.endswith(".exe"):
            return asset
    return None


def _find_sha256_asset(assets: Any) -> Optional[Dict[str, Any]]:
    target = RELEASE_ASSET_NAME.lower()
    target_base = os.path.splitext(target)[0]
    exact_names = {
        f"{target}.sha256",
        f"{target}.sha256.txt",
        f"{target_base}.sha256",
        f"{target_base}.sha256.txt",
    }
    for asset in assets:
        name = str(asset.get("name", "")).lower()
        if name in exact_names:
            return asset
        if name.endswith(".sha256") and (target in name or target_base in name):
            return asset
    return None


def _build_manifest() -> Dict[str, Any]:
    release = _http_get_json(_latest_release_api_url())
    tag_name = str(release.get("tag_name", "")).strip()
    if not tag_name:
        raise RuntimeError("Latest release is missing 'tag_name'.")

    assets = release.get("assets") or []
    exe_asset = _find_exe_asset(assets)
    if not exe_asset:
        raise RuntimeError(f"Could not find .exe asset in release '{tag_name}'.")

    exe_url = str(exe_asset.get("browser_download_url", "")).strip()
    if not exe_url:
        raise RuntimeError("Release asset is missing 'browser_download_url'.")

    sha256 = _extract_sha256_from_asset(exe_asset)
    if not sha256:
        sha_asset = _find_sha256_asset(assets)
        if sha_asset:
            sha_url = str(sha_asset.get("browser_download_url", "")).strip()
            if sha_url:
                sha256 = _extract_sha256_from_text(_http_get_text(sha_url))

    if not sha256:
        raise RuntimeError(
            "Could not determine SHA256. Publish a .sha256 asset or include digest in release asset metadata."
        )

    return {
        "version": _normalize_version(tag_name),
        "mandatory": MANDATORY_UPDATE,
        "files": {
            RELEASE_ASSET_NAME: {
                "url": exe_url,
                "sha256": sha256,
            }
        },
    }


@app.route("/manifest")
def manifest():
    now = time.time()
    cached_manifest = _manifest_cache.get("data")
    cached_is_fresh = cached_manifest is not None and now < _manifest_cache["expires_at"]
    if cached_is_fresh:
        return jsonify(cached_manifest)

    try:
        fresh_manifest = _build_manifest()
        _manifest_cache["data"] = fresh_manifest
        _manifest_cache["expires_at"] = now + MANIFEST_CACHE_SECONDS
        return jsonify(fresh_manifest)
    except Exception as exc:
        if cached_manifest is not None:
            response = jsonify(cached_manifest)
            response.headers["X-Manifest-Stale"] = "1"
            return response, 200
        return jsonify({"error": "Failed to build manifest", "details": str(exc)}), 502


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
