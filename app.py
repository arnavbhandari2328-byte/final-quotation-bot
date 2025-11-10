from flask import Flask, request, jsonify, Response
import os

app = Flask(__name__)

META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "verify-me-123")

@app.route("/", methods=["GET"])
def health():
    return jsonify(ok=True, tip="POST JSON to /quote, or use /webhook for WhatsApp")

# Meta verification (GET) + message ingress (POST)
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        # Verification handshake
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == META_VERIFY_TOKEN:
            return Response(challenge, status=200)
        return Response("Verification token mismatch", status=403)

    # POST: WhatsApp update
    data = request.get_json(silent=True) or {}
    try:
        change = data["entry"][0]["changes"][0]["value"]
        if "messages" in change and change["messages"]:
            msg = change["messages"][0]
            if msg.get("type") == "text":
                user_text = msg["text"]["body"]
                # Reuse your /quote logic
                # -> you can call your parser/creator, or simply echo for now:
                print("WA message:", user_text)
    except Exception as e:
        print("Webhook parse error:", e, data)

    return jsonify(ok=True)

@app.route("/quote", methods=["POST"])
def quote():
    data = request.get_json(silent=True) or {}
    msg = data.get("message", "")
    if not msg:
        return jsonify({"error": "No message provided"}), 400
    # Minimal success response just to prove POST works
    return jsonify({"ok": True, "echo": msg}), 200

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
