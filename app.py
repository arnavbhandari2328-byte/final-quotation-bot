from flask import Flask, request, jsonify, Response
from datetime import datetime
from docx import Document
import re, os, traceback, yagmail

app = Flask(__name__)

import logging
app.logger.setLevel(logging.INFO)

# ----- CONFIG -----
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_PASS")

# Strong regex parser for messages like:
# "quote 110 for Raju at Raj Pvt Ltd, 500 pcs 3in SS 316L sheets at 25000, hsn 7219, email raju@example.com"
QUOTE_RE = re.compile(
    r"""
    quote\s*(?P<qno>\d+)                              # quote number
    .*?\bfor\b\s+(?P<name>[A-Za-z][A-Za-z ]{1,60})    # name
    \s+\bat\b\s+(?P<company>[^,]+)                    # company
    ,?\s+(?P<qty>\d+)\s*(?P<units>pcs|nos|kgs|kg|mt|ton|piece|pieces)? # quantity + optional units
    .*?\b(?P<product>ss|stainless|mild|ms|alloy|aluminium|copper|brass|nickel|3in|4in|pipe|sheet|sheets|coil|bar|rod|flange|valve|fitting|316L|304L|310S|duplex|superduplex|sch\s*\d+|sch\d+|[\w\-\s/]+?)\b
    .*?\bat\s+(?P<rate>\d{2,})                        # rate
    (?:.*?\bhsn\b\s*(?P<hsn>\d{4,8}))?                # optional hsn
    .*?\bemail\b\s*(?P<email>[\w.\-\+%]+@[\w.-]+\.[A-Za-z]{2,})  # email
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

def _clean(s: str) -> str:
    return " ".join((s or "").strip().split())

def parse_message(text: str):
    if not text:
        return None
    raw = text
    text = " ".join(text.split())  # squash whitespace
    app.logger.info(f"[PARSE] incoming: {raw}")

    import re
    EMAIL_RE = re.compile(r'([\w.\-+%]+@[\w.\-]+\.[A-Za-z]{2,})')
    QNO_RE   = re.compile(r'\bquote\s*(\d{1,10})\b', re.I)
    RATE_RE  = re.compile(r'\b(?:rate|at)\s*([0-9]{3,})\b', re.I)
    QTY_RE   = re.compile(r'\b(\d{1,7})\s*(pcs|nos|pieces?|kgs?|kg|mt|ton|bundle|bndl)?\b', re.I)
    HSN_RE   = re.compile(r'\bhsn\s*([0-9]{4,8})\b', re.I)

    # Try a few structured patterns first (different word orders)
    patterns = [
        re.compile(
            r"""quote\s*(?P<qno>\d+).*?\bfor\b\s+(?P<name>[^,]+?)\s+\bat\b\s+(?P<company>[^,]+),
                \s*(?P<qty>\d+)\s*(?P<units>pcs|nos|pieces?|kgs?|kg|mt|ton)?\s+
                (?P<product>.+?)\s+\bat\s+(?P<rate>\d{3,})\b(?:.*?\bhsn\b\s*(?P<hsn>\d{4,8}))?
                .*?\bemail\b\s*(?P<email>[\w.\-+%]+@[\w.\-]+\.[A-Za-z]{2,})
            """, re.I | re.X),
        re.compile(
            r"""quote\s*(?P<qno>\d+).*?\bfor\b\s+(?P<name>[^,]+?)\s+\bat\b\s+(?P<company>[^,]+),
                .*?\bemail\b\s*(?P<email>[\w.\-+%]+@[\w.\-]+\.[A-Za-z]{2,}).*?
                (?P<qty>\d+)\s*(?P<units>pcs|nos|pieces?|kgs?|kg|mt|ton)?\s+
                (?P<product>.+?)\s+\bat\s+(?P<rate>\d{3,})
            """, re.I | re.X),
    ]

    for pat in patterns:
        m = pat.search(text)
        if m:
            d = {k: _clean(v) if isinstance(v, str) else v for k, v in m.groupdict().items()}
            # normalise units
            u = (d.get("units") or "").lower()
            units_map = {"piece":"pcs","pieces":"pcs","nos":"pcs","pcs":"pcs","kg":"Kgs","kgs":"Kgs","mt":"MT","ton":"Ton"}
            d["units"] = units_map.get(u, "pcs") if d.get("qty") else ""
            return {
                "qno": d.get("qno",""),
                "name": d.get("name","").title(),
                "company": d.get("company",""),
                "qty": d.get("qty",""),
                "units": d.get("units") or "pcs",
                "product": d.get("product","")[:120],
                "rate": d.get("rate",""),
                "hsn": d.get("hsn",""),
                "email": d.get("email",""),
            }

    # Heuristic fallback: pick fields independently
    qno    = (QNO_RE.search(text) or [None, ""])[1]
    email  = (EMAIL_RE.search(text) or [None, ""])[1]
    rate   = (RATE_RE.search(text) or [None, ""])[1]
    qtym   = QTY_RE.search(text)
    qty    = qtym.group(1) if qtym else ""
    uraw   = (qtym.group(2) if qtym and qtym.lastindex and qtym.lastindex >= 2 else "") or ""
    units  = {"piece":"pcs","pieces":"pcs","nos":"pcs","pcs":"pcs","kg":"Kgs","kgs":"Kgs","mt":"MT","ton":"Ton"}.get(uraw.lower(), "pcs")
    hsn    = (HSN_RE.search(text) or [None, ""])[1]

    # name: after "for", up to " at "
    name = ""
    m = re.search(r"\bfor\s+(.+?)\s+\bat\b", text, re.I)
    if m: name = _clean(m.group(1))
    # company: after " at ", up to comma
    company = ""
    m = re.search(r"\bat\s+([^,]+)", text, re.I)
    if m: company = _clean(m.group(1))
    # product: try between qty and rate
    product = ""
    if qty and rate:
        m = re.search(fr"{re.escape(qty)}\s*(?:{uraw}|pcs|nos|pieces?|kgs?|kg|mt|ton)?\s+(.+?)\s+\bat\s+{rate}\b", text, re.I)
        if m: product = _clean(m.group(1))

    ctx = {
        "qno": qno,
        "name": name.title(),
        "company": company,
        "qty": qty,
        "units": units if qty else "",
        "product": product[:120],
        "rate": rate,
        "hsn": hsn,
        "email": email,
    }

    # minimal validation
    required = ["name","company","qty","rate","email"]
    if any(not ctx[k] for k in required):
        app.logger.warning(f"[PARSE] fallback incomplete -> {ctx}")
        return None
    return ctx

def create_doc(ctx:dict) -> str:
    doc = Document()
    doc.add_heading(f'Quotation #{ctx["qno"]}', level=1)
    doc.add_paragraph(f'Date: {datetime.now().strftime("%d-%b-%Y")}')
    doc.add_paragraph(f'Customer: {ctx["name"]}')
    doc.add_paragraph(f'Company: {ctx["company"]}')
    doc.add_paragraph(f'Product: {ctx["product"]}')
    doc.add_paragraph(f'Quantity: {ctx["qty"]} {ctx["units"]}')
    doc.add_paragraph(f'Rate: {ctx["rate"]}')
    if ctx.get("hsn"):
        doc.add_paragraph(f'HSN: {ctx["hsn"]}')
    doc.add_paragraph(f'Email: {ctx["email"]}')
    fname = f'Quotation_{ctx["name"].replace(" ","_")}_{datetime.now().date()}.docx'
    doc.save(fname)
    return fname

def send_email(attachment_path:str, to_email:str):
    if not GMAIL_USER or not GMAIL_PASS:
        app.logger.error("GMAIL_USER/GMAIL_PASS missing; skipping email send.")
        return False
    try:
        yag = yagmail.SMTP(GMAIL_USER, GMAIL_PASS)
        yag.send(
            to=to_email,
            subject="Quotation from Nivee Metal Products Pvt. Ltd.",
            contents="Please find the attached quotation.",
            attachments=attachment_path,
        )
        return True
    except Exception as e:
        app.logger.exception(f"Email send failed: {e}")
        return False

def extract_text_from_meta(payload:dict) -> str | None:
    try:
        entry = payload.get("entry", [])[0]
        change = entry.get("changes", [])[0]
        value = change.get("value", {})
        msgs = value.get("messages", [])
        if not msgs:
            return None
        msg = msgs[0]
        if msg.get("type") == "text":
            return msg["text"]["body"]
        return None
    except Exception:
        return None

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    try:
        if request.method == "GET":
            # Support initial verification if you use this endpoint for Meta verification
            hub_mode = request.args.get("hub.mode")
            hub_challenge = request.args.get("hub.challenge")
            hub_verify_token = request.args.get("hub.verify_token")
            # Optional: compare to your env VERIFY_TOKEN
            if hub_mode == "subscribe" and hub_challenge:
                return Response(hub_challenge, status=200)
            return Response("OK", status=200)

        # POST
        body = request.get_json(silent=True) or {}
        # Accept both tester JSON and Meta payload
        app.logger.info(f"[WEBHOOK] body: {body}")
        text = body.get("message") or extract_text_from_meta(body)
        if not text:
            app.logger.info("Webhook received but no parsable text message; returning 200.")
            return jsonify({"status": "ignored"}), 200

        app.logger.info(f"Received message: {text}")
        ctx = parse_message(text)
        if not ctx:
            app.logger.warning("Regex parsing failed; returning success 200 to avoid WA retry.")
            return jsonify({"status": "parsed:false"}), 200

        file_path = create_doc(ctx)
        ok = send_email(file_path, ctx["email"])
        return jsonify({"status": "ok", "emailed": bool(ok), "file": file_path}), 200

    except Exception as e:
        app.logger.error("Exception in /webhook: %s\n%s", e, traceback.format_exc())
        # Always 200 for Meta to avoid retry storms; log the error
        return jsonify({"status": "error_logged"}), 200

@app.route("/")
def health():
    return "Quotation bot is running (regex-only)."

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
