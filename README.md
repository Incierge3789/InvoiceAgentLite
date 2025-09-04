# InvoiceAgent Lite

A FastAPI-based PDF invoice processing system that extracts financial data from PDF invoices and automatically saves the results to Google Sheets.

## Features

- **PDF Upload**: Web interface with drag & drop support for multiple PDF files (up to 10MB each)
- **Advanced Text Extraction**: Uses pdfplumber (primary) with pdfminer fallback for reliable text extraction
- **Smart Data Extraction**: Automatically extracts:
  - Vendor names
  - Invoice numbers
  - Issue dates (normalized to YYYY-MM-DD format)
  - Currency detection (JPY, USD, EUR)
  - Subtotal, tax, and total amounts
  - Confidence scoring and review flags
- **Google Sheets Integration**: Automatically appends extracted data to a Google Sheet with 13-column schema
- **RESTful API**: Clean JSON API endpoints for programmatic access
- **CORS Support**: Configured for localhost and *.pages.dev domains

## Setup Instructions

### 1. Environment Variables (Replit Secrets)

Set the following secrets in your Replit environment:

#### Required Secrets:

- **`SHEET_ID`**: The Google Sheets ID where data will be appended
  - Example: `1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms`
  - Found in the Google Sheets URL: `https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit`

- **`GOOGLE_SERVICE_ACCOUNT_JSON`**: Service account credentials as JSON string
  - Copy the entire JSON content as a string (including curly braces)
  - Example format: `{"type": "service_account", "project_id": "...", "private_key": "...", "client_email": "...", ...}`

#### Optional Secrets:

- **`ADMIN_PASSWORD`**: Password for the `/selfcheck` endpoint (defaults to "default_admin_password")

### 2. Create Google Service Account

1. **Create a Google Cloud Project** (if you don't have one):
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select an existing one

2. **Enable the Google Sheets API**:
   - Go to APIs & Services → Library
   - Search for "Google Sheets API" and enable it

3. **Create a Service Account**:
   - Go to IAM & Admin → Service Accounts
   - Click "Create Service Account"
   - Give it a name (e.g., "invoice-processor")
   - Skip role assignment (not needed for Sheets)

4. **Generate a JSON Key**:
   - Click on your service account
   - Go to "Keys" tab → "Add Key" → "Create new key" → JSON
   - Download the JSON file
   - Copy the entire JSON content as a string into the `GOOGLE_SERVICE_ACCOUNT_JSON` secret

### 3. Set Up Google Sheets

1. **Create a Google Sheet** with the following column headers in row 1:
   ```
   timestamp | file_name | vendor | invoice_no | issue_date | currency | subtotal | tax | total | confidence | needs_review | notes | raw_text
   ```

2. **Share the sheet** with your service account:
   - Open your Google Sheet
   - Click "Share" button
   - Add the service account email (found in your JSON credentials under "client_email")
   - Give "Editor" permissions

3. **Get the Sheet ID**:
   - Copy the ID from your sheet URL and set it as the `SHEET_ID` secret

### 4. Deployment

The application runs on FastAPI with uvicorn. For Replit Autoscale:
1. Set the health check path to `/healthz`
2. The app will automatically bind to port 5000
3. All secrets should be configured in Replit Secrets

## API Endpoints

### `GET /healthz`
Health check endpoint.

**Response:**
```json
{"ok": true}
```

### `GET /upload`
Serves the web interface for uploading PDF files with drag & drop functionality.

### `POST /api/upload`
Processes uploaded PDF files and returns extraction results.

**Request:** Multipart form data with PDF files
**Response:**
```json
{
  "results": [
    {
      "file": "invoice_001.pdf",
      "ok": true,
      "fields": {
        "vendor": "ABC Company Ltd",
        "invoice_no": "INV-2024-001",
        "issue_date": "2024-03-15",
        "currency": "JPY",
        "subtotal": 10000.0,
        "tax": 1000.0,
        "total": 11000.0,
        "confidence": 0.82,
        "needs_review": false,
        "notes": "",
        "raw_text": "Invoice text excerpt..."
      },
      "sheet_row": 42
    }
  ]
}
```

### `GET /selfcheck?pw=PASSWORD`
Administrative endpoint that adds a test row to the Google Sheet.

**Parameters:** `pw` - Admin password (set via `ADMIN_PASSWORD` secret)
**Response:** `{"ok": true}`

## curl Examples

### Upload Single PDF
```bash
curl -X POST "http://localhost:5000/api/upload" \
  -F "files=@invoice.pdf"
```

### Upload Multiple PDFs
```bash
curl -X POST "http://localhost:5000/api/upload" \
  -F "files=@invoice1.pdf" \
  -F "files=@invoice2.pdf" \
  -F "files=@invoice3.pdf"
```

### Health Check
```bash
curl "http://localhost:5000/healthz"
```

### Self Check
```bash
curl "http://localhost:5000/selfcheck?pw=your_admin_password"
```

## Data Extraction Patterns

### Amount Extraction
- `合計[\\s:：]*([￥¥]?)([\\d,]+\\.?\\d*)` (Total)
- `請求金額[\\s:：]*([￥¥]?)([\\d,]+\\.?\\d*)` (Invoice Amount)
- `小計[\\s:：]*([￥¥]?)([\\d,]+\\.?\\d*)` (Subtotal)
- `税額[\\s:：]*([￥¥]?)([\\d,]+\\.?\\d*)` (Tax)

### Date Extraction
- `発行日[:：]?\\s?(\\d{4}[/-]\\d{1,2}[/-]\\d{1,2})` (Issue Date)
- `請求日[:：]?\\s?(\\d{4}[/-]\\d{1,2}[/-]\\d{1,2})` (Invoice Date)
- `(\\d{4}[.-]\\d{1,2}[.-]\\d{1,2})` (Generic Date)
- `(\\d{4}年\\d{1,2}月\\d{1,2}日)` (Japanese Date Format)

### Invoice Number Extraction
- `請求書番号[:：]?\\s?([A-Za-z0-9-]+)` (Invoice Number)
- `Invoice No\\.?\\s?[:：]?\\s?([A-Za-z0-9-]+)` (English Invoice Number)

### Vendor Extraction
- Predefined company names (Amazon, Google, Meta, etc.)
- Heuristic extraction from document header

## Google Sheets Schema

The system appends data to Google Sheets with the following 13-column structure:

| Column | Description | Example |
|--------|-------------|---------|
| timestamp | ISO timestamp when processed | 2024-03-15T10:30:00Z |
| file_name | Original PDF filename | invoice_001.pdf |
| vendor | Company/vendor name | ABC Company Ltd |
| invoice_no | Invoice/reference number | INV-2024-001 |
| issue_date | Invoice issue date (YYYY-MM-DD) | 2024-03-15 |
| currency | Detected currency code | JPY |
| subtotal | Subtotal amount | 10000.0 |
| tax | Tax amount | 1000.0 |
| total | Total amount | 11000.0 |
| confidence | Confidence score (0.0-1.0) | 0.82 |
| needs_review | Review flag (TRUE/FALSE) | FALSE |
| notes | Additional notes | Processing notes |
| raw_text | First 500 chars of extracted text | Invoice text excerpt... |

## Confidence Scoring

- **Score Range**: 0.00 to 1.00
- **Calculation**: Based on successful extraction of key fields (vendor, issue_date, total) plus bonuses for subtotal, tax, and invoice_no
- **Review Flag**: Set to "TRUE" if confidence < 0.67

## File Processing Limits

- **File Type**: PDF only (validated by MIME type and extension)
- **File Size**: Maximum 10MB per file
- **Multiple Files**: Supported (drag & drop or API)
- **Processing**: Files are processed in-memory and not persisted to disk

## Error Handling

### Common Issues and Solutions:

1. **"Google Sheets service not available"**:
   - Check that `GOOGLE_SERVICE_ACCOUNT_JSON` is properly set
   - Verify JSON format is correct (valid JSON string)
   - Ensure service account has been created

2. **"Failed to append to sheet"**:
   - Verify `SHEET_ID` is correct
   - Check that service account email has "Editor" access to the sheet
   - Ensure Google Sheets API is enabled in your project

3. **"Invalid PDF file format"**:
   - File may be corrupted, password-protected, or image-only
   - Ensure file is a proper PDF with extractable text

4. **"File size exceeds 10MB limit"**:
   - Compress PDF or split into smaller files
   - This is a configurable limit for performance

### HTTP Status Codes:
- `200`: Success
- `400`: Bad request (no files, invalid format)
- `401`: Unauthorized (wrong admin password)
- `422`: Unprocessable entity (file validation failed)
- `500`: Internal server error

## Self-Check Verification

Run the self-check endpoint to verify your setup:

```bash
curl "http://localhost:5000/selfcheck?pw=your_admin_password"
```

**Expected result**: A new row should appear in your Google Sheet with test data:
- file_name: "selfcheck_test.pdf"
- vendor: "Test Vendor Inc."
- invoice_no: "TEST-001"
- total: 11000.0
- confidence: 1.0

## Dependencies

The application uses the following pinned dependencies:

```
fastapi==0.111.0
uvicorn[standard]==0.30.0
pdfplumber==0.11.0
pdfminer.six==20231228
pydantic==2.7.4
gspread==6.1.2
google-auth==2.34.0
google-auth-httplib2==0.2.0
oauth2client==4.1.3
python-multipart==0.0.9
```

## Development

To run locally:
```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export SHEET_ID="your_sheet_id"
export GOOGLE_SERVICE_ACCOUNT_JSON='{"type": "service_account", ...}'
export ADMIN_PASSWORD="your_password"

# Run the application
python main.py
```

The application will be available at `http://localhost:5000/upload`

## Support

For issues with:
- **PDF extraction**: Ensure PDFs contain extractable text (not scanned images)
- **Google Sheets**: Verify service account permissions and API access
- **File uploads**: Check file format and size constraints
- **Missing data**: Review confidence scores and use manual review for low-confidence extractions