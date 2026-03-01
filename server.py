from flask import Flask, jsonify
import requests

app = Flask(__name__)

REPO = "ami-nope/SoundboardEZ"

@app.route("/manifest")
def manifest():
    try:
        r = requests.get(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            timeout=5
        )
        r.raise_for_status()
        data = r.json()

        # Example tag: "1.1"
        tag = data["tag_name"]

        asset_url = None
        for asset in data.get("assets", []):
            if asset["name"] == "SoundboardEZ.exe":
                asset_url = asset["browser_download_url"]
                break

        if not asset_url:
            return jsonify({"error": "SoundboardEZ.exe not found in latest release"}), 500

        return jsonify({
            "version": tag,
            "mandatory": False,
            "files": {
                "SoundboardEZ.exe": {
                    "url": asset_url
                }
            }
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
