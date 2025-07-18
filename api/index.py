# --- Vercel Project Structure ---
# Your project needs to be organized in this exact structure in your GitHub repository.
#
# / (root directory)
# |
# |- api/
# |  |- index.py      <-- The main Python bot logic goes here.
# |
# |- requirements.txt  <-- The list of Python libraries.
# |
# |- vercel.json       <-- The configuration file for Vercel.

# --- File 1: api/index.py ---
# This is the UPDATED file. It adds raw message logging and has more robust regex.

import os
import re
import json
import logging
import hashlib
import hmac
import time
from datetime import datetime
from flask import Flask, request, make_response
from threading import Thread
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- Configuration ---

logging.basicConfig(level=logging.INFO)

# Initialize Flask app - Vercel will look for this 'app' object.
app = Flask(__name__)

SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")

# --- Security Verification ---

def verify_slack_request(request_body, timestamp, signature):
    """Verifies the request signature from Slack."""
    if not SLACK_SIGNING_SECRET or not timestamp or not signature:
        logging.error("Verification failed: Missing secret, timestamp, or signature.")
        return False

    try:
        req_timestamp = int(timestamp)
        if abs(time.time() - req_timestamp) > 60 * 5:
            logging.error(f"Verification failed: Timestamp is too old. Server time: {time.time()}, Slack time: {req_timestamp}")
            return False
    except (ValueError, TypeError):
        logging.error("Verification failed: Invalid timestamp format.")
        return False
    
    basestring = f"v0:{timestamp}:{request_body}".encode('utf-8')
    my_signature = 'v0=' + hmac.new(
        SLACK_SIGNING_SECRET.encode('utf-8'),
        basestring,
        hashlib.sha256
    ).hexdigest()
    
    is_valid = hmac.compare_digest(my_signature, signature)
    if not is_valid:
        logging.error("Verification failed: Signatures do not match.")
    
    return is_valid

# --- Data Parsing and Sheets Logic ---

def parse_and_append(message_text):
    """
    Parses a message and appends it to the Google Sheet.
    This function will be run in a separate thread.
    """
    # --- NEW DEBUGGING LOG ---
    # This will show us the exact text the bot is receiving, including hidden characters.
    logging.info("--- RAW SLACK MESSAGE TEXT ---")
    logging.info(repr(message_text)) 
    logging.info("--- END RAW TEXT ---")
    # --- END DEBUGGING LOG ---

    if "A new charter request has been received" not in message_text:
        return

    logging.info("Parsing a new charter request message.")
    
    # THE FIX: Made patterns even more flexible.
    patterns = {
        'charter_id': r"Charter\s*Id\s*:\s*(\d+)",
        'name': r"Name\s*:\s*(.+)",
        'phone': r"Phone\s*:\s*([0-9\s+()-]+)",
        'pick_up_date': r"Pick\s*up\s*date\s*:\s*([\d-]+)",
        'return_date': r"Return\s*date\s*:\s*([\d-]+)"
    }
    
    data = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, message_text, re.DOTALL | re.IGNORECASE)
        if match:
            data[key] = match.group(1).strip()
        else:
            logging.warning(f"Could not find pattern for: {key}")
            data[key] = ""

    if data.get('name'):
        name_parts = data['name'].split()
        data['first_name'] = name_parts[0]
        data['last_name'] = name_parts[-1] if len(name_parts) > 1 else ""
    else:
        data['first_name'] = ""
        data['last_name'] = ""
    
    data['request_received_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not data.get('charter_id'):
        logging.error("Parsing failed: Charter ID is missing.")
        return
        
    logging.info(f"Successfully parsed data: {data}")
    append_to_sheet(data)

def append_to_sheet(data):
    """Appends the extracted data as a new row to the configured Google Sheet."""
    try:
        SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
        RANGE_NAME = "Sheet1"
        
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if not creds_json:
            raise ValueError("GOOGLE_CREDENTIALS_JSON environment variable not set.")
            
        creds_dict = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(creds_dict)
        
        service = build('sheets', 'v4', credentials=creds)

        row_values = [
            data.get('request_received_date', ''),
            data.get('charter_id', ''),
            data.get('first_name', ''),
            data.get('last_name', ''),
            data.get('phone', ''),
            data.get('pick_up_date', ''),
            data.get('return_date', '')
        ]

        body = { 'values': [row_values] }
        
        result = service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME,
            valueInputOption='USER_ENTERED',
            body=body
        ).execute()
        
        logging.info(f"{result.get('updates').get('updatedCells')} cells appended.")
    except Exception as e:
        logging.error(f"Error appending to Google Sheet: {e}")

# --- Vercel Entry Point ---

@app.route("/", methods=["GET", "POST"])
def slack_events():
    """This endpoint now handles GET and POST requests differently."""
    
    if request.method == "GET":
        # A GET request is likely a browser or health check, not a Slack event.
        return make_response("Bot is alive and listening for POST requests from Slack.", 200)

    # --- Handle POST requests from Slack ---
    if request.method == "POST":
        # --- Security Verification ---
        signature = request.headers.get('X-Slack-Signature')
        timestamp = request.headers.get('X-Slack-Request-Timestamp')
        request_body = request.get_data().decode('utf-8')

        if not verify_slack_request(request_body, timestamp, signature):
            logging.error("Slack request verification FAILED!")
            return make_response("Invalid request", 403)

        # --- Slack URL Verification Handshake ---
        body = json.loads(request_body)
        if body.get("type") == "url_verification":
            return make_response(body.get("challenge"), 200, {"Content-Type": "text/plain"})

        # --- Handle Message Events ---
        if body.get("type") == "event_callback":
            event = body.get("event", {})
            if event.get("type") == "message" and not event.get("bot_id"):
                thread = Thread(target=parse_and_append, args=[event.get("text", "")])
                thread.start()
        
        # Respond to Slack immediately
        return make_response("", 200)
    
    # Fallback for other request types
    return make_response("Not Found", 404)
