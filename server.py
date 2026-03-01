from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/manifest")
def manifest():
    return jsonify({
        "version": "1.1",
        "mandatory": False,
        "files": {
            "SoundboardEZ.exe": {
                "url": "https://github.com/ami-nope/SoundboardEZ/releases/download/1.1/SoundboardEZ.exe"
            }
        }
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
