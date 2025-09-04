import os
import json
import tempfile
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import time

from flask import Flask, request, jsonify, render_template_string
from werkzeug.utils import secure_filename
from flask_cors import CORS

import PyPDF2
import io

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

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
                        <div class="card-header">
                            <h1 class="card-title mb-0">InvoiceAgent Lite</h1>
                            <p class="card-text mb-0">Upload PDF invoices to extract financial data</p>
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
                                <h5>Processing Results</h5>
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
            
            let selectedFiles = [];

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
                resultOutput.textContent = JSON.stringify(result, null, 2);
                resultContainer.classList.remove('d-none');
            }

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