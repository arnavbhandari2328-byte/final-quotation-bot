from flask import Flask, request, jsonify
import re, time, os, google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted
from docx import Document
from datetime import datetime
import yagmail

app = Flask(__name__)

# ====== CONFIG ======
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
else:
    model = None  # fallback only if key missing

LAST_CALL = 0
MIN_GAP = 65  # seconds between AI calls (1/min safe limit)


# ====== HELPERS ======
def call_gemini_safely(prompt):
    """Use Gemini safely under free-tier rate limits"""
    global LAST_CALL
    if not model:
        return None
    gap = time.time() - LAST_CALL
    if gap < MIN_GAP:
        time.sleep(MIN_GAP - gap)
    for i in range(4):  # retries if 429
        try:
            resp = model.generate_content(prompt)
            LAST_CALL = time.time()
            return resp.text
        except ResourceExhausted:
            time.sleep(20 * (i + 1))
    return None


def parse_with_rules(text):
    """Regex-based quick parse for quote messages"""
    pattern = re.compile(
        r"quote\s*(?P<qno>\d+).*?(?P<name>[A-Za-z ]+)\s+at\s+(?P<company>[^,]+),\s*(?P<qty>\d+)\s*(?:pcs|nos|k?gs)?\s*(?P<product>.+?)\s+at\s+(?P<rate>\d+).*?(?:hsn\s*(?P<hsn>\d+))?.*?email\s*(?P<email>\S+@\S+)",
        re.I | re.S,
    )
    m = pattern.search(text)
    if not m:
        return None
    data = m.groupdict()
    data.setdefault("units", "pcs")
    return data


def create_quotation_doc(data):
    """Generate Word quotation file"""
    doc = Document()
    doc.add_heading(f"Quotation #{data['qno']}", level=1)
    doc.add_paragraph(f"Customer Name: {data['name']}")
    doc.add_paragraph(f"Company: {data['company']}")
    doc.add_paragraph(f"Quantity: {data['qty']} {data['units']}")
    doc.add_paragraph(f"Product: {data['product']}")
    doc.add_paragraph(f"Rate: {data['rate']}")
    doc.add_paragraph(f"HSN: {data.get('hsn', '-')}")
    doc.add_paragraph(f"Email: {data['email']}")
    filename = f"Quotation_{data['name'].replace(' ', '_')}_{datetime.now().strftime('%Y-%m-%d')}.docx"
    doc.save(filename)
    return filename


def send_email(file_path, to_email):
    """Email quotation file"""
    yag = yagmail.SMTP("your_email@gmail.com", "your_app_password")
    yag.send(to=to_email, subject="Quotation File", contents="Attached quotation", attachments=file_path)


# ====== ROUTES ======
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data or "messages" not in data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}):
        return jsonify({"status": "ignored"}), 200

    message = data["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"]
    print(f"Received message: {message}")

    parsed = parse_with_rules(message)
    if not parsed and model:
        print("Regex failed, using Gemini...")
        ai_output = call_gemini_safely(f"Extract quote info: {message}")
        if ai_output:
            # Fallback structure
            parsed = {"qno": "000", "name": "Unknown", "company": "Unknown", "qty": "0", "product": "Unknown", "rate": "0", "hsn": "-", "email": "unknown@example.com"}

    if not parsed:
        return jsonify({"error": "Failed to parse"}), 400

    filename = create_quotation_doc(parsed)
    send_email(filename, parsed["email"])
    return jsonify({"status": "ok", "file": filename}), 200


@app.route("/")
def home():
    return "Quotation Bot is running!"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
