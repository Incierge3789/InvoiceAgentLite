import os
import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
import time
import tempfile

from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from pydantic import BaseModel

import PyPDF2
import io

import gspread
from google.oauth2.service_account import Credentials

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="InvoiceAgent Lite", version="1.0.0")

# Add CORS middleware for localhost and *.pages.dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:*", "https://*.pages.dev", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration from environment variables
SHEET_ID = os.getenv("SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "default_admin_password")

# Constants
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_MIME_TYPES = ["application/pdf"]
ALLOWED_EXTENSIONS = [".pdf"]

# Vendor hints for extraction
VENDOR_HINTS = [
    "Amazon", "Google", "Meta", "Facebook", "Slack", "Cloudflare",
    "ヤマト運輸", "日本郵便", "佐川急便", "楽天", "LINE", "Microsoft",
    "Apple", "Netflix", "Adobe", "Salesforce", "Zoom", "GitHub"
]

# Enhanced regex patterns for extraction
AMOUNT_PATTERNS = [
    r"合計[\s:：]*([￥¥]?)([\\d,]+\.?\d*)",
    r"請求金額[\s:：]*([￥¥]?)([\\d,]+\.?\d*)",
    r"小計[\s:：]*([￥¥]?)([\\d,]+\.?\d*)",
    r"税額[\s:：]*([￥¥]?)([\\d,]+\.?\d*)",
    r"¥\s?([\\d,]+)",
    r"JPY\s?([\\d,]+)"
]

DATE_PATTERNS = [
    r"発行日[:：]?\s?(\d{4}[/-]\d{1,2}[/-]\d{1,2})",
    r"請求日[:：]?\s?(\d{4}[/-]\d{1,2}[/-]\d{1,2})",
    r"(\d{4}[.-]\d{1,2}[.-]\d{1,2})",
    r"(\d{4}年\d{1,2}月\d{1,2}日)"
]

INVOICE_NO_PATTERNS = [
    r"請求書番号[:：]?\s?([A-Za-z0-9-]+)",
    r"Invoice No\.?\s?[:：]?\s?([A-Za-z0-9-]+)",
    r"番号[:：]?\s?([A-Za-z0-9-]+)"
]

class ProcessedFile(BaseModel):
    file: str
    ok: bool
    fields: Dict[str, Any]
    sheet_row: Optional[int] = None

class UploadResponse(BaseModel):
    results: List[ProcessedFile]

class InvoiceProcessor:
    def __init__(self):
        self.sheets_client = None
        self.worksheet = None
        self._initialize_sheets_service()
    
    def _initialize_sheets_service(self):
        """Initialize Google Sheets service with service account credentials"""
        if not GOOGLE_SERVICE_ACCOUNT_JSON or not SHEET_ID:
            logger.warning("Google Sheets credentials or Sheet ID not configured")
            return
        
        try:
            credentials_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
            scopes = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]
            credentials = Credentials.from_service_account_info(credentials_dict, scopes=scopes)
            self.sheets_client = gspread.authorize(credentials)
            self.worksheet = self.sheets_client.open_by_key(SHEET_ID).sheet1
            logger.info("Google Sheets service initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets service: {e}")
            raise HTTPException(status_code=500, detail="Failed to initialize Google Sheets service")
    
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
            raise HTTPException(status_code=422, detail="Invalid PDF file format or password-protected")
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {e}")
            raise HTTPException(status_code=500, detail="Failed to extract text from PDF")
    
    def extract_amounts(self, text: str) -> Dict[str, float]:
        """Extract subtotal, tax, and total amounts from text"""
        amounts = {"subtotal": None, "tax": None, "total": None}
        
        # Look for specific patterns
        subtotal_patterns = [r"小計[\s:：]*([￥¥]?)([\\d,]+\.?\d*)", r"税抜[\s:：]*([￥¥]?)([\\d,]+\.?\d*)"]
        tax_patterns = [r"税額[\s:：]*([￥¥]?)([\\d,]+\.?\d*)", r"消費税[\s:：]*([￥¥]?)([\\d,]+\.?\d*)"]
        total_patterns = [r"合計[\s:：]*([￥¥]?)([\\d,]+\.?\d*)", r"請求金額[\s:：]*([￥¥]?)([\\d,]+\.?\d*)"]
        
        # Extract subtotal
        for pattern in subtotal_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match and not amounts["subtotal"]:
                try:
                    amounts["subtotal"] = float(match.group(2).replace(',', ''))
                    break
                except ValueError:
                    continue
        
        # Extract tax
        for pattern in tax_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match and not amounts["tax"]:
                try:
                    amounts["tax"] = float(match.group(2).replace(',', ''))
                    break
                except ValueError:
                    continue
        
        # Extract total
        for pattern in total_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match and not amounts["total"]:
                try:
                    amounts["total"] = float(match.group(2).replace(',', ''))
                    break
                except ValueError:
                    continue
        
        return amounts
    
    def extract_invoice_no(self, text: str) -> Optional[str]:
        """Extract invoice number from text"""
        for pattern in INVOICE_NO_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
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
    
    def detect_currency(self, text: str) -> str:
        """Detect currency from text, default to JPY"""
        if re.search(r'[￥¥]|JPY|円', text):
            return "JPY"
        elif re.search(r'\$|USD', text):
            return "USD"
        elif re.search(r'€|EUR', text):
            return "EUR"
        return "JPY"  # Default
    
    def calculate_confidence(self, fields: Dict[str, Any]) -> float:
        """Calculate confidence score based on extracted fields"""
        key_fields = ['vendor', 'issue_date', 'total']
        hits = sum([1 for field in key_fields if fields.get(field) is not None])
        
        # Bonus for having subtotal and tax
        if fields.get('subtotal') is not None:
            hits += 0.5
        if fields.get('tax') is not None:
            hits += 0.5
        if fields.get('invoice_no') is not None:
            hits += 0.5
            
        max_score = len(key_fields) + 1.5  # 3 key fields + 1.5 bonus
        return round(hits / max_score, 2)
    
    def process_pdf(self, filename: str, pdf_content: bytes) -> Dict[str, Any]:
        """Process a single PDF and extract invoice data"""
        start_time = time.time()
        
        try:
            # Extract text
            text = self.extract_text_from_pdf(pdf_content)
            
            # Extract fields
            vendor = self.extract_vendor(text)
            invoice_no = self.extract_invoice_no(text)
            issue_date = self.extract_issue_date(text)
            currency = self.detect_currency(text)
            amounts = self.extract_amounts(text)
            
            # Build fields dict
            fields = {
                "vendor": vendor,
                "invoice_no": invoice_no,
                "issue_date": issue_date,
                "currency": currency,
                "subtotal": amounts["subtotal"],
                "tax": amounts["tax"],
                "total": amounts["total"],
                "notes": "",
                "raw_text": text[:500] + "..." if len(text) > 500 else text  # First 500 chars
            }
            
            # Calculate confidence and review flag
            confidence = self.calculate_confidence(fields)
            needs_review = confidence < 0.67
            
            fields["confidence"] = confidence
            fields["needs_review"] = needs_review
            
            processing_time = round((time.time() - start_time) * 1000, 2)
            logger.info(f"Processed {filename} in {processing_time}ms - Confidence: {confidence}")
            
            result = {
                "file": filename,
                "ok": True,
                "fields": fields
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Error processing PDF {filename}: {e}")
            return {
                "file": filename,
                "ok": False,
                "fields": {"notes": f"Processing failed: {str(e)}"}
            }
    
    def append_to_sheet(self, data: Dict[str, Any]) -> Optional[int]:
        """Append data to Google Sheets and return row number"""
        if not self.worksheet:
            logger.warning("Google Sheets service not available")
            return None
        
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            fields = data["fields"]
            
            # Column order: timestamp, file_name, vendor, invoice_no, issue_date, currency, 
            #              subtotal, tax, total, confidence, needs_review, notes, raw_text
            row_data = [
                timestamp,
                data["file"],
                fields.get("vendor", ""),
                fields.get("invoice_no", ""),
                fields.get("issue_date", ""),
                fields.get("currency", ""),
                fields.get("subtotal", ""),
                fields.get("tax", ""),
                fields.get("total", ""),
                fields.get("confidence", ""),
                "TRUE" if fields.get("needs_review", False) else "FALSE",
                fields.get("notes", ""),
                fields.get("raw_text", "")
            ]
            
            self.worksheet.append_row(row_data)
            
            # Get the row number of the appended data
            row_count = len(self.worksheet.get_all_values())
            logger.info(f"Appended row {row_count} to sheet")
            return row_count
            
        except Exception as e:
            logger.error(f"Error appending to sheet: {e}")
            return None

# Initialize processor
processor = InvoiceProcessor()

@app.get("/healthz")
async def health_check():
    """Health check endpoint"""
    return {"ok": True}

@app.get("/selfcheck")
async def self_check(pw: str = Query(...)):
    """Self check endpoint with authentication"""
    if pw != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid password")
    
    try:
        # Create test data
        test_data = {
            "file": "selfcheck_test.pdf",
            "ok": True,
            "fields": {
                "vendor": "Test Vendor Inc.",
                "invoice_no": "TEST-001",
                "issue_date": datetime.now().strftime("%Y-%m-%d"),
                "currency": "JPY",
                "subtotal": 10000.0,
                "tax": 1000.0,
                "total": 11000.0,
                "confidence": 1.0,
                "needs_review": False,
                "notes": "Self-check test entry",
                "raw_text": "This is a self-check test entry generated by the system."
            }
        }
        
        # Append to sheet
        row_number = processor.append_to_sheet(test_data)
        
        return {"ok": True}
        
    except Exception as e:
        logger.error(f"Self-check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Self-check failed: {str(e)}")

@app.get("/upload", response_class=HTMLResponse)
async def upload_page():
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
                                        <p class="text-muted">Maximum 10MB per file, PDF format only</p>
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
                    if (file.size > 10 * 1024 * 1024) {
                        showError(`File ${file.name} exceeds 10MB limit`);
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
                        showError(result.detail || 'Upload failed');
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
    return HTMLResponse(content=html_content)

@app.post("/api/upload", response_model=UploadResponse)
async def upload_files(files: List[UploadFile] = File(...)):
    """Process uploaded PDF files"""
    
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    
    results = []
    
    for file in files:
        try:
            # Validate file type
            if file.content_type not in ALLOWED_MIME_TYPES:
                results.append({
                    "file": file.filename or "unknown",
                    "ok": False,
                    "fields": {"notes": f"Invalid file type. Only PDF files are allowed."}
                })
                continue
            
            # Validate file extension
            if not file.filename or not any(file.filename.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
                results.append({
                    "file": file.filename or "unknown",
                    "ok": False,
                    "fields": {"notes": f"Invalid file extension. Only .pdf files are allowed."}
                })
                continue
            
            # Read file content
            content = await file.read()
            
            # Validate file size
            if len(content) > MAX_FILE_SIZE:
                results.append({
                    "file": file.filename,
                    "ok": False,
                    "fields": {"notes": f"File size exceeds 10MB limit."}
                })
                continue
            
            # Process the PDF
            result = processor.process_pdf(file.filename, content)
            
            # Append to Google Sheets if processing was successful
            if result["ok"]:
                sheet_row = processor.append_to_sheet(result)
                result["sheet_row"] = sheet_row
            
            results.append(result)
            
        except Exception as e:
            logger.error(f"Error processing file {file.filename}: {e}")
            results.append({
                "file": file.filename or "unknown",
                "ok": False,
                "fields": {"notes": f"Processing failed: {str(e)}"}
            })
    
    return UploadResponse(results=results)

# No WSGI adapter needed - using uvicorn for ASGI deployment

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    uvicorn.run(app, host="0.0.0.0", port=port)