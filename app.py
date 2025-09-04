import os
import json
import tempfile
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import time

from flask import Flask, request, jsonify, render_template_string, Response
from werkzeug.utils import secure_filename
from flask_cors import CORS

import PyPDF2
import io

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import gspread
import csv
from io import StringIO

# Japanese invoice extraction rule pack
import re, unicodedata
AMOUNT_RE = re.compile(r'(?:ご?\s*請求(?:金額|合計)|税込?合計|合計)[^\d]*(\d{1,3}(?:,\d{3})+|\d+)\s*円?')
DATE_RE   = re.compile(r'(?:発行日|請求日|納品日|支払期限)[^\d]*(\d{4}[./年]\s*\d{1,2}[./月]\s*\d{1,2}日?)')
VENDOR_RE = re.compile(r'(?:株式会社|有限会社|合同会社)[^\n]+|.+?御中')
def _normalize(txt: str) -> str:
    import unicodedata, re
    t = unicodedata.normalize('NFKC', txt).replace('\u3000',' ')
    return re.sub(r'[ \t]+', ' ', t)
def extract_fields_jp(text: str):
    t = _normalize(text)
    amount = None
    m = AMOUNT_RE.search(t)
    if m: amount = int(m.group(1).replace(',', ''))
    date = None
    dm = DATE_RE.search(t)
    if dm:
        date = dm.group(1)
        date = (date.replace('年','-').replace('月','-').replace('日','')
                    .replace('/','-').replace('.','-'))
        import re
        date = re.sub(r'\s+', '', date)
    vendor = None
    head = '\n'.join(t.splitlines()[:20])
    vm = VENDOR_RE.search(head)
    if vm: vendor = vm.group(0).replace('御中','').strip()
    score = sum(x is not None for x in [amount, date, vendor]) / 3
    return {
        "amount": amount, "date": date, "vendor": vendor,
        "confidence": round(score, 2),
        "needs_review": "TRUE" if score < 0.8 else "FALSE",
    }

# Config helpers
def load_config():
    """Load config from data/config.json and environment variables"""
    config = {}
    
    # Try to load from data/config.json first
    try:
        if os.path.exists('data/config.json'):
            with open('data/config.json', 'r') as f:
                config = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load config.json: {e}")
    
    # Override with environment variables if present
    if os.getenv('GSPREAD_CREDENTIALS_JSON'):
        config['service_account_json'] = os.getenv('GSPREAD_CREDENTIALS_JSON')
    if os.getenv('SHEET_ID'):
        config['sheet_id'] = os.getenv('SHEET_ID')
    if os.getenv('SHEET_NAME'):
        config['sheet_name'] = os.getenv('SHEET_NAME')
        
    return config

def get_worksheet():
    """Get worksheet from gspread using current config"""
    config = load_config()
    
    if not config.get('service_account_json') or not config.get('sheet_id'):
        return None
    
    try:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(config['service_account_json']),
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(config['sheet_id'])
        worksheet_name = config.get('sheet_name', 'invoices')
        
        try:
            worksheet = sheet.worksheet(worksheet_name)
        except gspread.WorksheetNotFound:
            # Create worksheet if it doesn't exist
            worksheet = sheet.add_worksheet(title=worksheet_name, rows="1000", cols="20")
            # Add headers
            headers = ['timestamp', 'file', 'vendor', 'date', 'amount', 'currency', 'category', 'description', 'notes', 'confidence', 'needs_review', 'raw_excerpt', 'source']
            worksheet.append_row(headers)
            
        return worksheet
    except Exception as e:
        logger.error(f"Failed to get worksheet: {e}")
        return None

def save_row_to_sheet(payload):
    """Save data to Google Sheets with 13 columns"""
    worksheet = get_worksheet()
    if not worksheet:
        return False
    
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        row = [
            timestamp,
            payload.get('file', ''),
            payload.get('vendor', ''),
            payload.get('date', ''),
            payload.get('amount', ''),
            'JPY',
            '',  # category
            '',  # description  
            '',  # notes
            payload.get('confidence', ''),
            payload.get('needs_review', ''),
            payload.get('raw_excerpt', '')[:500],
            'upload'
        ]
        worksheet.append_row(row)
        return True
    except Exception as e:
        logger.error(f"Failed to save row to sheet: {e}")
        return False

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# Add CORS
CORS(app, origins=["*"])

# Configuration from environment variables
SHEET_ID = os.getenv("SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "default_admin_password")

# Constants
MAX_FILE_SIZE = 3 * 1024 * 1024  # 3MB
ALLOWED_EXTENSIONS = {'pdf'}

# Vendor hints for extraction
VENDOR_HINTS = [
    "Amazon", "Google", "Meta", "Facebook", "Slack", "Cloudflare",
    "ヤマト運輸", "日本郵便", "佐川急便", "楽天", "LINE", "Microsoft",
    "Apple", "Netflix", "Adobe", "Salesforce", "Zoom", "GitHub"
]

# Regex patterns for extraction
AMOUNT_PATTERNS = [
    r"合計[\s:：]*([\\d,]+\.?\d*)",
    r"請求金額[\s:：]*([\\d,]+\.?\d*)",
    r"¥\s?([\\d,]+)",
    r"JPY\s?([\\d,]+)"
]

DATE_PATTERNS = [
    r"発行日[:：]?\s?(\d{4}[/-]\d{1,2}[/-]\d{1,2})",
    r"(\d{4}[.-]\d{1,2}[.-]\d{1,2})",
    r"(\d{4}年\d{1,2}月\d{1,2}日)"
]

class InvoiceProcessor:
    def __init__(self):
        self.sheets_service = None
        self._initialize_sheets_service()
    
    def _initialize_sheets_service(self):
        """Initialize Google Sheets service with service account credentials"""
        if not GOOGLE_SERVICE_ACCOUNT_JSON or not SHEET_ID:
            logger.warning("Google Sheets credentials or Sheet ID not configured")
            return
        
        try:
            credentials_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
            credentials = service_account.Credentials.from_service_account_info(
                credentials_dict,
                scopes=['https://www.googleapis.com/auth/spreadsheets']
            )
            self.sheets_service = build('sheets', 'v4', credentials=credentials)
            logger.info("Google Sheets service initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets service: {e}")
            raise Exception("Failed to initialize Google Sheets service")
    
    def extract_text_from_pdf(self, pdf_content: bytes) -> str:
        """Extract text from PDF content using PyPDF2"""
        try:
            pdf_file = io.BytesIO(pdf_content)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
            
            return text
        except PyPDF2.errors.PdfReadError as e:
            logger.error(f"PDF read error: {e}")
            raise Exception("Invalid PDF file format or password-protected")
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {e}")
            raise Exception("Failed to extract text from PDF")
    
    def extract_amount(self, text: str) -> Optional[float]:
        """Extract amount from text using regex patterns"""
        for pattern in AMOUNT_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                amount_str = match.group(1).replace(',', '')
                try:
                    return float(amount_str)
                except ValueError:
                    continue
        return None
    
    def extract_issue_date(self, text: str) -> Optional[str]:
        """Extract and normalize issue date from text"""
        for pattern in DATE_PATTERNS:
            match = re.search(pattern, text)
            if match:
                date_str = match.group(1)
                # Normalize date format to ISO (YYYY-MM-DD)
                try:
                    # Handle different separators
                    date_str = re.sub(r'[年月]', '-', date_str)
                    date_str = re.sub(r'日', '', date_str)
                    date_str = re.sub(r'[./]', '-', date_str)
                    
                    # Parse and format
                    if '-' in date_str:
                        parts = date_str.split('-')
                        if len(parts) == 3:
                            year, month, day = parts
                            return f"{year.zfill(4)}-{month.zfill(2)}-{day.zfill(2)}"
                except Exception as e:
                    logger.warning(f"Failed to normalize date {date_str}: {e}")
                    continue
        return None
    
    def extract_vendor(self, text: str) -> Optional[str]:
        """Extract vendor from text using hints and heuristics"""
        # First, check for vendor hints
        for hint in VENDOR_HINTS:
            if hint.lower() in text.lower():
                return hint
        
        # If no hint found, extract from first 10 lines
        lines = text.split('\n')[:10]
        for line in lines:
            # Look for company-like patterns (alphanumeric + Japanese characters)
            candidates = re.findall(r'[a-zA-Z0-9\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF\u3400-\u4DBF]+', line.strip())
            for candidate in candidates:
                if len(candidate) >= 3 and len(candidate) <= 50:
                    return candidate
        
        return None
    
    def calculate_confidence(self, amount: Optional[float], issue_date: Optional[str], vendor: Optional[str]) -> float:
        """Calculate confidence score based on extracted fields"""
        hits = sum([1 for field in [amount, issue_date, vendor] if field is not None])
        return round(hits / 3.0, 2)
    
    def process_pdf(self, filename: str, pdf_content: bytes) -> Dict[str, Any]:
        """Process a single PDF and extract invoice data"""
        start_time = time.time()
        
        try:
            # Extract text
            text = self.extract_text_from_pdf(pdf_content)
            
            # Extract fields
            amount = self.extract_amount(text)
            issue_date = self.extract_issue_date(text)
            vendor = self.extract_vendor(text)
            
            # Calculate confidence and review flag
            confidence = self.calculate_confidence(amount, issue_date, vendor)
            needs_review = "TRUE" if confidence < 0.67 else "FALSE"
            
            # Create raw excerpt (first 200 characters)
            raw_excerpt = text[:200].strip()
            if len(text) > 200:
                raw_excerpt += "..."
            
            processing_time = round((time.time() - start_time) * 1000, 2)
            logger.info(f"Processed {filename} in {processing_time}ms - Amount: {amount}, Date: {issue_date}, Vendor: {vendor}, Confidence: {confidence}")
            
            result = {
                "file": filename,
                "vendor": vendor,
                "date": issue_date,
                "amount": amount,
                "confidence": confidence,
                "needs_review": needs_review,
                "raw_excerpt": raw_excerpt
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Error processing PDF {filename}: {e}")
            raise
    
    def append_to_sheet(self, data: Dict[str, Any]) -> bool:
        """Append data to Google Sheets"""
        if not self.sheets_service:
            logger.warning("Google Sheets service not available")
            return False
        
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            
            values = [[
                timestamp,
                data["file"],
                data["vendor"] or "",
                data["date"] or "",
                data["amount"] or "",
                data["confidence"],
                data["needs_review"],
                data["raw_excerpt"]
            ]]
            
            body = {
                'values': values
            }
            
            result = self.sheets_service.spreadsheets().values().append(
                spreadsheetId=SHEET_ID,
                range='A:H',  # Columns A through H
                valueInputOption='RAW',
                body=body
            ).execute()
            
            logger.info(f"Appended row to sheet: {result.get('updates', {}).get('updatedRows', 0)} rows updated")
            return True
            
        except HttpError as e:
            logger.error(f"Google Sheets API error: {e}")
            return False
        except Exception as e:
            logger.error(f"Error appending to sheet: {e}")
            return False

# Initialize processor
processor = InvoiceProcessor()

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/healthz", methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"ok": True})

@app.route("/selfcheck", methods=['GET'])
def self_check():
    """Self check endpoint with authentication"""
    pw = request.args.get('pw', '')
    if pw != ADMIN_PASSWORD:
        return jsonify({"error": "Invalid password"}), 401
    
    try:
        # Create test data
        test_data = {
            "file": "selfcheck_test.pdf",
            "vendor": "Test Vendor",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "amount": 1000.0,
            "confidence": 1.0,
            "needs_review": "FALSE",
            "raw_excerpt": "This is a self-check test entry generated by the system."
        }
        
        # Append to sheet
        success = processor.append_to_sheet(test_data)
        
        return jsonify({
            "ok": True,
            "message": "Self-check completed",
            "sheet_updated": success,
            "test_data": test_data
        })
        
    except Exception as e:
        logger.error(f"Self-check failed: {e}")
        return jsonify({"error": f"Self-check failed: {str(e)}"}), 500

@app.route("/settings", methods=['GET'])
def settings_page():
    """Serve the settings page"""
    config = load_config()
    connected = bool(config.get('service_account_json') and config.get('sheet_id'))
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en" data-bs-theme="dark">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>InvoiceAgent Lite - Settings</title>
        <link href="https://cdn.replit.com/agent/bootstrap-agent-dark-theme.min.css" rel="stylesheet">
    </head>
    <body>
        <div class="container mt-4">
            <div class="row justify-content-center">
                <div class="col-lg-8">
                    <div class="card">
                        <div class="card-header">
                            <h1 class="card-title mb-0">Google Sheets Connection</h1>
                            <p class="card-text mb-0">Configure your Google Sheets integration</p>
                        </div>
                        <div class="card-body">
                            <div class="alert {'alert-success' if connected else 'alert-warning'} mb-4">
                                <strong>Status:</strong> {'Connected' if connected else 'Not Connected'}
                                {f'<br><small>Sheet ID: {config.get("sheet_id", "")[:20]}...</small>' if connected else ''}
                            </div>
                            
                            <form id="settingsForm">
                                <div class="mb-3">
                                    <label for="serviceAccountJson" class="form-label">Service Account JSON</label>
                                    <textarea class="form-control" id="serviceAccountJson" rows="8" 
                                              placeholder="Paste your Google Service Account JSON credentials here...">{'***hidden***' if config.get('service_account_json') else ''}</textarea>
                                    <div class="form-text">Download from Google Cloud Console → IAM & Admin → Service Accounts</div>
                                </div>
                                
                                <div class="mb-3">
                                    <label for="sheetId" class="form-label">Sheet ID</label>
                                    <input type="text" class="form-control" id="sheetId" 
                                           value="{config.get('sheet_id', '')}" 
                                           placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms">
                                    <div class="form-text">Found in your Google Sheets URL</div>
                                </div>
                                
                                <div class="mb-3">
                                    <label for="sheetName" class="form-label">Sheet Name</label>
                                    <input type="text" class="form-control" id="sheetName" 
                                           value="{config.get('sheet_name', 'invoices')}" 
                                           placeholder="invoices">
                                    <div class="form-text">Name of the worksheet tab (default: invoices)</div>
                                </div>
                                
                                <div class="d-grid gap-2 d-md-flex justify-content-md-end">
                                    <button type="button" class="btn btn-outline-secondary" id="clearBtn">Clear</button>
                                    <button type="submit" class="btn btn-primary" id="saveBtn">
                                        <span class="spinner-border spinner-border-sm me-2 d-none" id="saveSpinner"></span>
                                        Save & Test
                                    </button>
                                </div>
                            </form>
                            
                            <div id="resultContainer" class="mt-4 d-none">
                                <div class="alert" id="resultAlert"></div>
                            </div>
                            
                            <div class="mt-4">
                                <a href="/upload" class="btn btn-outline-primary">← Back to Upload</a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            const settingsForm = document.getElementById('settingsForm');
            const saveBtn = document.getElementById('saveBtn');
            const clearBtn = document.getElementById('clearBtn');
            const saveSpinner = document.getElementById('saveSpinner');
            const resultContainer = document.getElementById('resultContainer');
            const resultAlert = document.getElementById('resultAlert');
            
            const serviceAccountJson = document.getElementById('serviceAccountJson');
            const sheetId = document.getElementById('sheetId');
            const sheetName = document.getElementById('sheetName');

            settingsForm.addEventListener('submit', async (e) => {{
                e.preventDefault();
                
                saveBtn.disabled = true;
                saveSpinner.classList.remove('d-none');
                resultContainer.classList.add('d-none');
                
                const formData = {{
                    service_account_json: serviceAccountJson.value.trim(),
                    sheet_id: sheetId.value.trim(),
                    sheet_name: sheetName.value.trim() || 'invoices'
                }};
                
                try {{
                    const response = await fetch('/settings', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json'
                        }},
                        body: JSON.stringify(formData)
                    }});
                    
                    const result = await response.json();
                    
                    if (response.ok && result.ok) {{
                        resultAlert.className = 'alert alert-success';
                        resultAlert.textContent = 'Settings saved and tested successfully!';
                        setTimeout(() => location.reload(), 1500);
                    }} else {{
                        resultAlert.className = 'alert alert-danger';
                        resultAlert.textContent = result.error || 'Save failed';
                    }}
                    
                    resultContainer.classList.remove('d-none');
                }} catch (error) {{
                    resultAlert.className = 'alert alert-danger';
                    resultAlert.textContent = 'Network error: ' + error.message;
                    resultContainer.classList.remove('d-none');
                }} finally {{
                    saveBtn.disabled = false;
                    saveSpinner.classList.add('d-none');
                }}
            }});

            clearBtn.addEventListener('click', () => {{
                if (confirm('Clear all settings?')) {{
                    serviceAccountJson.value = '';
                    sheetId.value = '';
                    sheetName.value = 'invoices';
                }}
            }});
        </script>
    </body>
    </html>
    """
    return html_content

@app.route("/settings", methods=['POST'])
def save_settings():
    """Save settings and test connection"""
    try:
        data = request.get_json()
        
        if not data.get('service_account_json') or not data.get('sheet_id'):
            return jsonify({"error": "Service Account JSON and Sheet ID are required"}), 400
        
        # Validate JSON format
        try:
            json.loads(data['service_account_json'])
        except json.JSONDecodeError:
            return jsonify({"error": "Invalid JSON format in Service Account"}), 400
        
        # Save to config file
        os.makedirs('data', exist_ok=True)
        with open('data/config.json', 'w') as f:
            json.dump(data, f, indent=2)
        
        # Test connection
        try:
            creds = service_account.Credentials.from_service_account_info(
                json.loads(data['service_account_json']),
                scopes=['https://www.googleapis.com/auth/spreadsheets']
            )
            gc = gspread.authorize(creds)
            sheet = gc.open_by_key(data['sheet_id'])
            worksheet_name = data.get('sheet_name', 'invoices')
            
            try:
                worksheet = sheet.worksheet(worksheet_name)
            except gspread.WorksheetNotFound:
                worksheet = sheet.add_worksheet(title=worksheet_name, rows="1000", cols="20")
                headers = ['timestamp', 'file', 'vendor', 'date', 'amount', 'currency', 'category', 'description', 'notes', 'confidence', 'needs_review', 'raw_excerpt', 'source']
                worksheet.append_row(headers)
            
            # Test with a quick row insertion and deletion
            test_timestamp = datetime.now(timezone.utc).isoformat()
            test_row = ['invoiceagent:test', test_timestamp]
            worksheet.append_row(test_row)
            
            # Find and delete the test row
            rows = worksheet.get_all_values()
            for i, row in enumerate(rows):
                if len(row) >= 2 and row[0] == 'invoiceagent:test' and row[1] == test_timestamp:
                    worksheet.delete_rows(i + 1)
                    break
            
            return jsonify({"ok": True, "message": "Settings saved and connection tested successfully"})
            
        except Exception as e:
            return jsonify({"error": f"Connection test failed: {str(e)}"}), 400
        
    except Exception as e:
        logger.error(f"Settings save error: {e}")
        return jsonify({"error": f"Failed to save settings: {str(e)}"}), 500

@app.route("/download_csv", methods=['POST'])
def download_csv():
    """Generate CSV download for a single result"""
    try:
        data = request.get_json()
        
        # Create CSV content with 13 columns
        output = StringIO()
        writer = csv.writer(output)
        
        # Headers
        headers = ['timestamp', 'file', 'vendor', 'date', 'amount', 'currency', 'category', 'description', 'notes', 'confidence', 'needs_review', 'raw_excerpt', 'source']
        writer.writerow(headers)
        
        # Data row
        timestamp = datetime.now(timezone.utc).isoformat()
        row = [
            timestamp,
            data.get('file', ''),
            data.get('vendor', ''),
            data.get('date', ''),
            data.get('amount', ''),
            'JPY',
            '',  # category
            '',  # description  
            '',  # notes
            data.get('confidence', ''),
            data.get('needs_review', ''),
            data.get('raw_excerpt', '')[:500],
            'upload'
        ]
        writer.writerow(row)
        
        output.seek(0)
        csv_content = output.getvalue()
        
        filename = f"invoice_{data.get('file', 'data')}.csv"
        
        return Response(
            csv_content,
            mimetype='text/csv',
            headers={"Content-disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        logger.error(f"CSV download error: {e}")
        return jsonify({"error": f"Failed to generate CSV: {str(e)}"}), 500

@app.route("/upload", methods=['GET'])
def upload_page():
    """Serve the upload page"""
    html_content = """
    <!DOCTYPE html>
    <html lang="en" data-bs-theme="dark">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>InvoiceAgent Lite - PDF Upload</title>
        <link href="https://cdn.replit.com/agent/bootstrap-agent-dark-theme.min.css" rel="stylesheet">
        <style>
            .drop-zone {
                border: 2px dashed var(--bs-border-color);
                border-radius: 0.375rem;
                padding: 3rem;
                text-align: center;
                transition: border-color 0.15s ease-in-out, background-color 0.15s ease-in-out;
                background-color: var(--bs-body-bg);
            }
            .drop-zone:hover, .drop-zone.dragover {
                border-color: var(--bs-primary);
                background-color: var(--bs-primary-bg-subtle);
            }
            .file-list {
                max-height: 200px;
                overflow-y: auto;
            }
            .result-container {
                max-height: 400px;
                overflow-y: auto;
            }
        </style>
    </head>
    <body>
        <div class="container mt-4">
            <div class="row justify-content-center">
                <div class="col-lg-8">
                    <div class="card">
                        <div class="card-header d-flex justify-content-between align-items-center">
                            <div>
                                <h1 class="card-title mb-0">InvoiceAgent Lite</h1>
                                <p class="card-text mb-0">Upload PDF invoices to extract financial data</p>
                            </div>
                            <a href="/settings" class="btn btn-outline-secondary btn-sm">⚙️ Settings</a>
                        </div>
                        <div class="card-body">
                            <form id="uploadForm">
                                <div class="drop-zone mb-3" id="dropZone">
                                    <div class="mb-3">
                                        <svg width="48" height="48" fill="currentColor" class="mb-3">
                                            <use href="#upload-icon"/>
                                        </svg>
                                        <h5>Drop PDF files here or click to browse</h5>
                                        <p class="text-muted">Maximum 3MB per file, PDF format only</p>
                                    </div>
                                    <input type="file" id="fileInput" multiple accept="application/pdf" class="d-none">
                                    <button type="button" class="btn btn-outline-primary" onclick="document.getElementById('fileInput').click()">
                                        Browse Files
                                    </button>
                                </div>
                                
                                <div id="fileList" class="file-list mb-3"></div>
                                
                                <div class="d-grid gap-2">
                                    <button type="submit" class="btn btn-primary" id="uploadBtn" disabled>
                                        <span class="spinner-border spinner-border-sm me-2 d-none" id="uploadSpinner"></span>
                                        Upload and Process
                                    </button>
                                </div>
                            </form>
                            
                            <div id="resultContainer" class="mt-4 d-none">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <h5>Processing Results</h5>
                                    <button type="button" class="btn btn-outline-primary btn-sm d-none" id="downloadCsvBtn">
                                        📥 Download CSV
                                    </button>
                                </div>
                                <pre id="resultOutput" class="result-container bg-body-secondary p-3 rounded"></pre>
                            </div>
                            
                            <div id="errorContainer" class="mt-4 d-none">
                                <div class="alert alert-danger" id="errorMessage"></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- SVG Icons -->
        <svg style="display: none;">
            <defs>
                <symbol id="upload-icon" viewBox="0 0 16 16">
                    <path d="M.5 9.9a.5.5 0 0 1 .5.5v2.5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-2.5a.5.5 0 0 1 1 0v2.5a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2v-2.5a.5.5 0 0 1 .5-.5z"/>
                    <path d="M7.646 1.146a.5.5 0 0 1 .708 0l3 3a.5.5 0 0 1-.708.708L8.5 2.707V11.5a.5.5 0 0 1-1 0V2.707L5.354 4.854a.5.5 0 1 1-.708-.708l3-3z"/>
                </symbol>
            </defs>
        </svg>

        <script>
            const dropZone = document.getElementById('dropZone');
            const fileInput = document.getElementById('fileInput');
            const fileList = document.getElementById('fileList');
            const uploadForm = document.getElementById('uploadForm');
            const uploadBtn = document.getElementById('uploadBtn');
            const uploadSpinner = document.getElementById('uploadSpinner');
            const resultContainer = document.getElementById('resultContainer');
            const resultOutput = document.getElementById('resultOutput');
            const errorContainer = document.getElementById('errorContainer');
            const errorMessage = document.getElementById('errorMessage');
            const downloadCsvBtn = document.getElementById('downloadCsvBtn');
            
            let selectedFiles = [];
            let lastResult = null;

            // Drag and drop functionality
            dropZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                dropZone.classList.add('dragover');
            });
            
            dropZone.addEventListener('dragleave', () => {
                dropZone.classList.remove('dragover');
            });
            
            dropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                dropZone.classList.remove('dragover');
                handleFiles(e.dataTransfer.files);
            });
            
            dropZone.addEventListener('click', () => {
                fileInput.click();
            });
            
            fileInput.addEventListener('change', (e) => {
                handleFiles(e.target.files);
            });

            function handleFiles(files) {
                selectedFiles = Array.from(files).filter(file => {
                    if (file.type !== 'application/pdf') {
                        showError(`File ${file.name} is not a PDF`);
                        return false;
                    }
                    if (file.size > 3 * 1024 * 1024) {
                        showError(`File ${file.name} exceeds 3MB limit`);
                        return false;
                    }
                    return true;
                });
                
                updateFileList();
                uploadBtn.disabled = selectedFiles.length === 0;
            }

            function updateFileList() {
                if (selectedFiles.length === 0) {
                    fileList.innerHTML = '';
                    return;
                }
                
                const listHtml = selectedFiles.map(file => 
                    `<div class="d-flex justify-content-between align-items-center border-bottom py-2">
                        <span>${file.name}</span>
                        <small class="text-muted">${(file.size / 1024 / 1024).toFixed(2)} MB</small>
                    </div>`
                ).join('');
                
                fileList.innerHTML = `<div class="border rounded p-2">${listHtml}</div>`;
            }

            uploadForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                
                if (selectedFiles.length === 0) return;
                
                // Show loading state
                uploadBtn.disabled = true;
                uploadSpinner.classList.remove('d-none');
                resultContainer.classList.add('d-none');
                errorContainer.classList.add('d-none');
                
                const formData = new FormData();
                selectedFiles.forEach(file => {
                    formData.append('files', file);
                });
                
                try {
                    const response = await fetch('/api/upload', {
                        method: 'POST',
                        body: formData
                    });
                    
                    const result = await response.json();
                    
                    if (response.ok) {
                        showResults(result);
                    } else {
                        showError(result.error || 'Upload failed');
                    }
                } catch (error) {
                    showError('Network error: ' + error.message);
                } finally {
                    uploadBtn.disabled = false;
                    uploadSpinner.classList.add('d-none');
                }
            });

            function showResults(result) {
                lastResult = result;
                resultOutput.textContent = JSON.stringify(result, null, 2);
                resultContainer.classList.remove('d-none');
                
                // Show CSV download button if sheets not connected
                if (result.sheet_status === 'not_connected') {
                    downloadCsvBtn.classList.remove('d-none');
                } else {
                    downloadCsvBtn.classList.add('d-none');
                }
            }

            // CSV Download functionality
            downloadCsvBtn.addEventListener('click', async () => {
                if (!lastResult) return;
                
                try {
                    const response = await fetch('/download_csv', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(lastResult)
                    });
                    
                    if (response.ok) {
                        const blob = await response.blob();
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.style.display = 'none';
                        a.href = url;
                        a.download = `invoice_${lastResult.file || 'data'}.csv`;
                        document.body.appendChild(a);
                        a.click();
                        window.URL.revokeObjectURL(url);
                        document.body.removeChild(a);
                    } else {
                        showError('Failed to download CSV');
                    }
                } catch (error) {
                    showError('Error downloading CSV: ' + error.message);
                }
            });

            function showError(message) {
                errorMessage.textContent = message;
                errorContainer.classList.remove('d-none');
                setTimeout(() => {
                    errorContainer.classList.add('d-none');
                }, 5000);
            }
        </script>
    </body>
    </html>
    """
    return html_content

@app.route("/api/upload", methods=['POST'])
def upload_files():
    """Process uploaded PDF files"""
    
    if 'files' not in request.files:
        return jsonify({"error": "No files uploaded"}), 400
    
    files = request.files.getlist('files')
    
    if not files or files[0].filename == '':
        return jsonify({"error": "No files selected"}), 400
    
    results = []
    errors = []
    
    for file in files:
        try:
            # Validate file type
            if not allowed_file(file.filename):
                errors.append(f"File {file.filename}: Invalid file type. Only PDF files are allowed.")
                continue
            
            # Read file content
            content = file.read()
            
            # Validate file size
            if len(content) > MAX_FILE_SIZE:
                errors.append(f"File {file.filename}: File size exceeds 3MB limit.")
                continue
            
            # Extract text from PDF
            filename = secure_filename(file.filename) if file.filename else "unknown.pdf"
            text = processor.extract_text_from_pdf(content)
            
            # Use Japanese extraction rules
            fields = extract_fields_jp(text)
            
            # Build result with required keys
            result = {
                "ok": True,
                "file": filename,
                "raw_excerpt": text[:500],
                **fields
            }
            
            # Try to save to Google Sheets if config is available
            config = load_config()
            if config.get('service_account_json') and config.get('sheet_id'):
                sheet_saved = save_row_to_sheet(result)
                result["sheet_status"] = "saved" if sheet_saved else "error"
            else:
                result["sheet_status"] = "not_connected"
            
            results.append(result)
            
        except Exception as e:
            logger.error(f"Error processing file {file.filename}: {e}")
            errors.append(f"File {file.filename}: Processing failed - {str(e)}")
    
    if errors and not results:
        return jsonify({"error": "Processing failed", "errors": errors}), 422
    
    # For single file upload, return the result directly
    if len(results) == 1:
        result = results[0]
        if errors:
            result["errors"] = errors
        return jsonify(result)
    
    # For multiple files, return as before
    response = {
        "ok": True,
        "results": results
    }
    
    if errors:
        response["errors"] = errors
    
    return jsonify(response)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)