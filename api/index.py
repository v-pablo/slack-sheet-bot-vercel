# --- File 1: api/index.py ---
# This is the main Python script. It's now a small web server using Flask.

from flask import Flask, request, jsonify
import os
import re
import json
import logging
from datetime import datetime
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- Configuration ---

logging.basicConfig(level=logging.INFO)

# Initialize Flask app - Renamed from flask_app to app for Vercel compatibility
app = Flask(__name__)

# Initialize the Slack App using environment variables
slack_app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
)
handler = SlackRequestHandler(slack_app)

# --- Data Parsing Logic ---

def parse_message_text(text):
    """
    Parses the raw text from a Slack message to extract charter details.
    Returns a dictionary with the extracted data or None if parsing fails.
    """
    if not text.strip().startswith("A new charter request has been received"):
        return None

    logging.info("Parsing a new charter request message.")
    
    patterns = {
        'charter_id': r"Charter Id:\s*(\d+)",
        'name': r"Name:\s*(.+)",
        'phone': r"Phone:\s*([\d\s]+)",
        'pick_up_date': r"Pick up date:\s*([\d-]+)",
        'return_date': r"Return date:\s*([\d-]+)"
    }
    
    data = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
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
        return None
        
    return data

# --- Google Sheets Logic ---

def append_to_sheet(data):
    """
    Appends the extracted data as a new row to the configured Google Sheet.
    """
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
        return True

    except Exception as e:
        logging.error(f"Error appending to Google Sheet: {e}")
        return False

# --- Slack Event Listener ---

@slack_app.event("message")
def handle_message_events(body, logger):
    """
    Listens for any new message events in channels the bot is a member of.
    """
    logger.info(body)
    
    message = body.get("event", {})
    text = message.get("text")
    if message.get("bot_id"):
        return

    parsed_data = parse_message_text(text)
    
    if parsed_data:
        append_to_sheet(parsed_data)

# --- Vercel Entry Point ---

@app.route("/", methods=["GET", "POST"])
def slack_events():
    """
    This is the single endpoint that Vercel will expose.
    It passes all incoming requests to the SlackRequestHandler.
    """
    return handler.handle(request)

# This is necessary for local testing, but Vercel will use the flask_app object directly.
if __name__ == "__main__":
    app.run(port=3000)
