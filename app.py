import yagmail
import re
import datetime
import os
import google.generativeai as genai
import json
from flask import Flask, request, Response, jsonify
from dotenv import load_dotenv
import requests
import gc

# --- 1. INITIAL SETUP & KEY LOADING ---
# load_dotenv() will load from .env for local testing
load_dotenv() 

# This loads keys from Render's Environment Variables in production
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_PASS = os.environ.get("GMAIL_PASS")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN") 

app = Flask(__name__)

# --- 2. CONFIGURE GEMINI API ---
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"!!! CRITICAL: Could not configure Gemini API: {e}")
else:
    print("!!! CRITICAL: GEMINI_API_KEY not found in environment.")

# --- 3. HELPER FUNCTIONS ---

def send_whatsapp_reply(to_phone_number, message_text):
    """Sends a reply message back to the customer via the Meta API."""
    if not META_ACCESS_TOKEN or not PHONE_NUMBER_ID:
        print("!!! ERROR: Meta API keys (TOKEN or ID) are missing. Cannot send reply.")
        return

    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = { "messaging_product": "whatsapp", "to": to_phone_number, "type": "text", "text": { "body": message_text } }

    response = None
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status() 
        print(f"Successfully sent WhatsApp reply to {to_phone_number}")
    except requests.exceptions.RequestException as e:
        print(f"!!! ERROR sending WhatsApp reply: {e}")
        if response is not None:
            print(f"Response Body: {response.text}")

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
    """
    Generates a professional HTML string for the email body.
    This uses very little memory.
    """
    print("Generating HTML quotation...")
    # You can customize this HTML with your company's branding, colors, etc.
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
                <tr>
                    <td><b>To:</b></td>
                    <td>{context['customer_name']}</td>
                </tr>
                <tr>
                    <td><b>Company:</b></td>
                    <td>{context.get('company_name', 'N/A')}</td>
                </tr>
                <tr>
                    <td><b>Date:</b></td>
                    <td>{context['date']}</td>
                </tr>
                <tr>
                    <td><b>Quote #:</b></td>
                    <td>{context.get('q_no', 'N/A')}</td>
                </tr>
            </table>

            <table class="quote-table">
                <thead>
                    <tr>
                        <th>Description</th>
                        <th>Qty</th>
                        <th>Unit</th>
                        <th>Rate</th>
                        <th>HSN</th>
                        <th>Total</th>
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

def send_email_quote(recipient_email, subject, html_body):
    """Connects to Gmail and sends the HTML quote."""
    if not html_body:
        print("Cannot send email, no HTML body was created.")
        return False
    
    if not GMAIL_USER or not GMAIL_PASS:
        print("!!! ERROR: GMAIL_USER or GMAIL_PASS not set. Cannot send email.")
        return False

    try:
        # yagmail automatically handles HTML content
        yag = yagmail.SMTP(GMAIL_USER, GMAIL_PASS)
        yag.send(
            to=recipient_email,
            subject=subject,
            contents=html_body # Pass the HTML string directly
        )
        print(f"Email successfully sent to {recipient_email}")
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

# --- WEBHOOK LISTENER ---
@app.route("/webhook", methods=['GET', 'POST'])
def handle_webhook():

    # --- Handle GET request (Meta's verification) ---
    if request.method == 'GET':
        print("Webhook received GET verification request...")
        if request.args.get('hub.mode') == 'subscribe' and request.args.get('hub.verify_token'):
            if request.args.get('hub.verify_token') == META_VERIFY_TOKEN:
                print("Verification successful!")
                return Response(request.args.get('hub.challenge'), status=200)
            else:
                print(f"Verification failed: Token mismatch. Expected '{META_VERIFY_TOKEN}'")
                return Response("Verification token mismatch", status=403)
        else:
            print("Failed: Did not receive correct hub.mode or hub.verify_token")
            return Response("Failed verification", status=400)

    # --- Handle POST request (A real WhatsApp message) ---
    if request.method == 'POST':
        print("Webhook received POST (new message or status)!")
        customer_phone_number = None
        command_text = None

        try:
            data = request.json
            if 'entry' not in data or not data['entry'] or 'changes' not in data['entry'][0] or not data['entry'][0]['changes']:
                 print("Received unrecognized structure (no entry/changes). Ignoring.")
                 return Response(status=200)

            change = data['entry'][0]['changes'][0]

            if 'value' in change and 'messages' in change['value'] and change['value']['messages']:
                message_data = change['value']['messages'][0]
                if message_data.get('type') == 'text':
                    customer_phone_number = message_data['from']
                    command_text = message_data['text']['body']
                else:
                    print(f"Received non-text message type: {message_data.get('type')}. Ignoring.")
                    return Response(status=200)
            elif 'value' in change and 'statuses' in change['value'] and change['value']['statuses']:
                status_data = change['value']['statuses'][0]
                print(f"Received status update: {status_data.get('status')} for message {status_data.get('id')}. Ignoring.")
                return Response(status=200)
            else:
                print("Received change structure without messages or statuses. Ignoring.")
                return Response(status=200)

        except Exception as e:
            print(f"Error parsing incoming JSON from Meta: {e}")
            print(f"Full data received: {request.data}")
            return Response(status=200)

        # --- If it was a text message, run the full bot logic ---
        if command_text and customer_phone_number:
            context = parse_command_with_ai(command_text)
            
            gc.collect() # Force garbage collection (still good practice)

            if not context:
                print("Sorry, I couldn't understand that. (AI parsing failed)")
                send_whatsapp_reply(customer_phone_number, "Sorry, I couldn't understand your request. Please check the details and try again.")
                return Response(status=200)

            # --- USE NEW HTML FUNCTIONS ---
            print(f"\nGenerating quote for {context['customer_name']}...")
            html_quote = create_quotation_html(context)

            if not html_quote:
                print("Error: Could not create the HTML quote.")
                send_whatsapp_reply(customer_phone_number, "Sorry, an internal error occurred while creating your document.")
                return Response(status=200)

            email_subject = f"Quotation from NIVEE METAL PRODUCTS PVT LTD (Ref: {context.get('q_no', 'N/A')})"
            
            email_sent = send_email_quote(context['email'], email_subject, html_quote)

            if email_sent:
                print("Process complete!")
                reply_msg = f"Success! Your quotation for {context['product']} has been generated and sent to {context['email']}."
                send_whatsapp_reply(customer_phone_number, reply_msg)
            else:
                print("Process failed.")
                send_whatsapp_reply(customer_phone_number, f"Sorry, I created the quote but failed to send the email to {context['email']}.")

            return Response(status=200)
        else:
            print("Webhook processed but no command text found. Ignoring.")
            return Response(status=200)

# --- 4. FLASK ROUTES FOR LOCAL TESTING ---
@app.route("/quote", methods=['POST'])
def handle_local_test():
    """
    This route is for local testing.
    It receives: {"message": "quote 101 for Raju..."}
    It creates the HTML but DOES NOT email or reply.
    """
    print("--- Local /quote test received! ---")
    data = request.json
    command_text = data.get('message')

    if not command_text:
        return jsonify({"error": "No message found"}), 400

    context = parse_command_with_ai(command_text)
    if not context:
        return jsonify({"error": "AI parsing failed"}), 500

    html_quote = create_quotation_html(context)
    if not html_quote:
        return jsonify({"error": "HTML creation failed"}), 500
    
    print(f"Local test successful. HTML generated.")
    
    # Return the HTML as a response for testing
    return Response(html_quote, status=200, mimetype='text/html')

# --- 5. START THE APP ---
if __name__ == "__main__":
    # This block runs ONLY when you execute 'python app.py' locally
    print(f"--- Starting Flask server for LOCAL TESTING ---")
    print(f"--- Listening at http://127.0.0.1:5000 ---")
    if not all([GEMINI_API_KEY, GMAIL_USER, GMAIL_PASS]):
         print("!!! WARNING: Missing one or more local .env variables (GEMINI_API_KEY, GMAIL_USER, GMAIL_PASS)")
    app.run(host='0.0.0.0', port=5000, debug=True)