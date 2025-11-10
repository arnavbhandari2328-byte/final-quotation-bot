from flask import Flask, request, jsonify, Response
import os
import google.generativeai as genai
import json
import datetime
import yagmail
import gc
import tempfile
from pathlib import Path
from docx import Document
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configure environment variables
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_PASS = os.environ.get("GMAIL_PASS")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "verify-me-123")

# Configure Gemini API
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"!!! CRITICAL: Could not configure Gemini API: {e}")
else:
    print("!!! CRITICAL: GEMINI_API_KEY not found in environment.")

# Helper Functions
def parse_command_with_ai(command_text):
    """Uses Google's Gemini API to parse a natural language command."""
    print("Sending command to Google AI (Gemini) for parsing...")
    try:
        model = genai.GenerativeModel('models/gemini-pro-latest') 
        system_prompt = f"""
        You are an assistant for a stainless steel trader. Your job is to extract
        quotation details from a user's command.
        The current date is: {datetime.date.today().strftime('%B %d, %Y')}

        Extract the following fields:
        - q_no: The quotation number.
        - date: The date for the quote. If not mentioned, use today's date.
        - company_name: The customer's company name (e.g., "Raj Pvt Ltd").
        - customer_name: The contact person's name (e.g., "Raju").
        - product: The full product description (e.g., "3 inch SS Pipe Sch 40").
        - quantity: The numerical quantity of items (e.g., "500"). Extract only the number.
        - rate: The price per item (e.g., "600").
        - units: The unit of measurement (e.g., "Pcs", "Nos", "Kgs"). Default to "Nos" if not specified.
        - hsn: The HSN code (e.g., "7304").
        - email: The customer's email address.

        Return the result ONLY as a single, minified JSON string.
        If a field is not found, set its value to null.
        """

        full_prompt = system_prompt + "\n\nUser: " + command_text
        response = model.generate_content(full_prompt)
        ai_response_json = response.text.strip().replace("```json", "").replace("```", "").strip()
        print(f"AI response received: {ai_response_json}")

        context = json.loads(ai_response_json)

        required_fields = ['product', 'customer_name', 'email', 'rate', 'quantity']
        for field in required_fields:
            if field not in context or not context[field]: 
                print(f"!!! ERROR: AI did not find a required field: '{field}' or value was empty.")
                return None

        try:
            price_num = float(context['rate'])
            qty_num = int(context['quantity'])
            total_num = price_num * qty_num

            context['rate_formatted'] = f"₹{price_num:,.2f}"
            context['total_formatted'] = f"₹{total_num:,.2f}"
            context['quantity'] = str(qty_num) 
        except ValueError:
            print(f"!!! ERROR: AI returned 'rate' or 'quantity' as invalid numbers.")
            return None

        if 'date' not in context or not context['date']: context['date'] = datetime.date.today().strftime("%B %d, %Y")
        if 'company_name' not in context: context['company_name'] = ""
        if 'hsn' not in context: context['hsn'] = ""
        if 'q_no' not in context: context['q_no'] = ""
        if 'units' not in context or not context['units']: context['units'] = "Nos"

        print(f"Parsed context: {context}")
        return context

    except Exception as e:
        print(f"!!! ERROR during AI processing or validation: {e}")
        return None

def create_quotation_html(context):
    """Generates a professional HTML string for the email body."""
    print("Generating HTML quotation...")
    html_content = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            .container {{ max-width: 600px; margin: auto; border: 1px solid #ddd; border-radius: 8px; padding: 25px; }}
            .header {{ font-size: 24px; font-weight: bold; color: #333; }}
            .sub-header {{ font-size: 18px; color: #555; margin-top: 10px; }}
            .info-table {{ width: 100%; margin-top: 20px; }}
            .info-table td {{ padding: 5px 0; }}
            .quote-table {{ width: 100%; margin-top: 25px; border-collapse: collapse; }}
            .quote-table th, .quote-table td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
            .quote-table th {{ background-color: #f9f9f9; }}
            .total-row td {{ font-weight: bold; font-size: 1.1em; }}
            .footer {{ margin-top: 30px; font-size: 12px; color: #888; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">NIVEE METAL PRODUCTS PVT LTD</div>
            <div class="sub-header">Quotation</div>
            
            <table class="info-table">
                <tr><td><b>To:</b></td><td>{context['customer_name']}</td></tr>
                <tr><td><b>Company:</b></td><td>{context.get('company_name', 'N/A')}</td></tr>
                <tr><td><b>Date:</b></td><td>{context['date']}</td></tr>
                <tr><td><b>Quote #:</b></td><td>{context.get('q_no', 'N/A')}</td></tr>
            </table>

            <table class="quote-table">
                <thead>
                    <tr>
                        <th>Description</th><th>Qty</th><th>Unit</th><th>Rate</th><th>HSN</th><th>Total</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>{context['product']}</td>
                        <td>{context['quantity']}</td>
                        <td>{context['units']}</td>
                        <td>{context['rate_formatted']}</td>
                        <td>{context.get('hsn', 'N/A')}</td>
                        <td>{context['total_formatted']}</td>
                    </tr>
                    <tr class="total-row">
                        <td colspan="5" style="text-align: right;">Grand Total</td>
                        <td>{context['total_formatted']}</td>
                    </tr>
                </tbody>
            </table>
            
            <div class="footer">
                <p>Thank you for your business!</p>
                <p>Terms: 18% GST Extra, 5-Day Validity, etc.</p>
            </div>
        </div>
    </body>
    </html>
    """
    return html_content

def create_quotation_docx(context) -> str:
    """Create a .docx quotation from the parsed context and return the file path."""
    print("Creating .docx quotation...")
    doc = Document()
    doc.add_heading('NIVEE METAL PRODUCTS PVT LTD - Quotation', level=1)

    doc.add_paragraph(f"To: {context.get('customer_name','')}")
    doc.add_paragraph(f"Company: {context.get('company_name','')}")
    doc.add_paragraph(f"Date: {context.get('date','')}")
    doc.add_paragraph(f"Quote #: {context.get('q_no','')}")

    doc.add_paragraph('')
    table = doc.add_table(rows=1, cols=6)
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = 'Description'
    hdr_cells[1].text = 'Qty'
    hdr_cells[2].text = 'Unit'
    hdr_cells[3].text = 'Rate'
    hdr_cells[4].text = 'HSN'
    hdr_cells[5].text = 'Total'

    row_cells = table.add_row().cells
    row_cells[0].text = str(context.get('product',''))
    row_cells[1].text = str(context.get('quantity',''))
    row_cells[2].text = str(context.get('units',''))
    row_cells[3].text = str(context.get('rate_formatted',''))
    row_cells[4].text = str(context.get('hsn',''))
    row_cells[5].text = str(context.get('total_formatted',''))

    doc.add_paragraph('')
    doc.add_paragraph(f"Grand Total: {context.get('total_formatted','')}")
    doc.add_paragraph('')
    doc.add_paragraph('Terms: 18% GST Extra, 5-Day Validity, etc.')

    # Write to a temporary file and return path
    tf = tempfile.NamedTemporaryFile(delete=False, suffix='.docx')
    tf.close()
    doc.save(tf.name)
    return tf.name

def send_email_quote(recipient_email, subject, html_body):
    """Sends the quotation via email."""
    if not html_body:
        print("Cannot send email, no HTML body was created.")
        return False
    
    if not GMAIL_USER or not GMAIL_PASS:
        print("!!! ERROR: GMAIL_USER or GMAIL_PASS not set. Cannot send email.")
        return False
    try:
        yag = yagmail.SMTP(GMAIL_USER, GMAIL_PASS)
        # If html_body is a string and attachment_path is provided, caller should
        # pass an attachments argument via kwargs. To keep compatibility, accept
        # attachments from a closure via global var not used here. Simpler: caller
        # will call yag.send directly if needed. But we'll allow optional attachment
        # by checking for a specially set attribute on html_body (not ideal).
        yag.send(
            to=recipient_email,
            subject=subject,
            contents=html_body
        )
        print(f"Email successfully sent to {recipient_email}")
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

def process_quotation_request(message_text):
    """Process a quotation request and return success status and error message if any."""
    context = parse_command_with_ai(message_text)
    
    if not context:
        return False, "Could not parse the quotation details. Please check the format and try again."
    # Generate HTML (for email body) and also create a .docx file to attach
    html_quote = create_quotation_html(context)
    if not html_quote:
        return False, "Could not generate the quotation document."

    # Create a temporary .docx file
    try:
        docx_path = create_quotation_docx(context)
    except Exception as e:
        print(f"Error creating .docx: {e}")
        return False, "Could not create .docx document"

    email_subject = f"Quotation from NIVEE METAL PRODUCTS PVT LTD (Ref: {context.get('q_no', 'N/A')})"

    # Send email with attachment
    try:
        yag = yagmail.SMTP(GMAIL_USER, GMAIL_PASS)
        yag.send(
            to=context['email'],
            subject=email_subject,
            contents=html_quote,
            attachments=docx_path,
        )
        print(f"Quotation sent to {context['email']} successfully.")
        sent = True
    except Exception as e:
        print(f"Error sending email with attachment: {e}")
        sent = False

    # Cleanup temporary file
    try:
        if docx_path and Path(docx_path).exists():
            Path(docx_path).unlink()
    except Exception as e:
        print(f"Failed to remove temporary file {docx_path}: {e}")

    if sent:
        return True, f"Quotation sent successfully to {context['email']}"
    else:
        return False, f"Failed to send quotation to {context['email']}"

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
                print("Processing WhatsApp message:", user_text)

                success, message = process_quotation_request(user_text)
                if success:
                    print(message)  # Log success message
                    return jsonify({"status": "ok"})
                else:
                    print("Error:", message)  # Log error message
                    # For webhook, return a generic invalid-format response per spec
                    return jsonify({"status": "error", "message": "Invalid format"})
            else:
                print("Received non-text message type:", msg.get("type"))
                return jsonify({"status": "error", "message": "Only text messages are supported"})
    except Exception as e:
        print("Webhook parse error:", e, data)
        return jsonify({"status": "error", "message": "Invalid format"})

    return jsonify({"status": "ok"})

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
