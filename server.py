from flask import Flask, jsonify, request
import requests
import json
import os

app = Flask(__name__)

REPO = "ami-nope/SoundboardEZ"
CONFIG_FILE = "update_config.json"

@app.route("/manifest")
def manifest():
    channel = request.args.get("channel", "stable")

    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)

        if channel not in config:
            return jsonify({"error": "Invalid channel"}), 400

        channel_data = config[channel]
        version = channel_data["version"]

        # Fetch specific release by tag
        r = requests.get(
            f"https://api.github.com/repos/{REPO}/releases/tags/{version}",
            timeout=5
        )
        r.raise_for_status()
        release = r.json()

        files = {}

        for asset in release.get("assets", []):
            files[asset["name"]] = {
                "url": asset["browser_download_url"]
            }

        return jsonify({
            "version": version,
            "mandatory": channel_data["mandatory"],
            "patch_notes": channel_data["patch_notes"],
            "min_required_version": channel_data["min_required_version"],
            "files": files
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
