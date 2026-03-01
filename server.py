from flask import Flask, jsonify, request, render_template, redirect
import json
import os

app = Flask(__name__)
CONFIG_FILE = "update_config.json"

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        channel = request.form["channel"]
        version = request.form["version"]
        mandatory = request.form.get("mandatory") == "on"
        patch_notes = request.form["patch_notes"]
        min_required = request.form["min_required"]

        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)

        config[channel] = {
            "version": version,
            "mandatory": mandatory,
            "patch_notes": patch_notes,
            "min_required_version": min_required
        }

        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

        return redirect("/admin")

    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)

    return render_template("admin.html", config=config)
