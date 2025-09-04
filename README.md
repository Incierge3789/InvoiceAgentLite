# InvoiceAgent Lite

A FastAPI-based PDF invoice processing system that extracts financial data from PDF invoices and automatically saves the results to Google Sheets.

## Features

- **PDF Upload**: Web interface with drag & drop support for multiple PDF files
- **Text Extraction**: Uses pdfminer.six to extract text from PDF files
- **Smart Data Extraction**: Automatically extracts:
  - Invoice amounts (¥, JPY, 合計, 請求金額)
  - Issue dates (multiple formats supported)
  - Vendor names (using predefined hints and heuristics)
- **Google Sheets Integration**: Automatically appends extracted data to a Google Sheet
- **Confidence Scoring**: Calculates confidence scores and flags entries needing review
- **File Validation**: Ensures only PDF files under 3MB are processed
- **Health Monitoring**: Health check and self-check endpoints

## Setup Instructions

### 1. Environment Variables (Replit Secrets)

Set the following secrets in your Replit environment:

#### Required Secrets:

- **`SHEET_ID`**: The Google Sheets ID where data will be appended
  - Example: `1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms`
  - Found in the Google Sheets URL: `https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit`

- **`GOOGLE_SERVICE_ACCOUNT_JSON`**: Service account credentials as JSON string
  - Create a service account in Google Cloud Console
  - Download the JSON key file
  - Copy the entire JSON content as a string (including curly braces)
  - Example format: `{"type": "service_account", "project_id": "...", ...}`

#### Optional Secrets:

- **`ADMIN_PASSWORD`**: Password for the `/selfcheck` endpoint (defaults to "default_admin_password")

### 2. Google Sheets Setup

1. **Create a Google Sheet** with the following column headers in row 1:
   ```
   timestamp | filename | vendor | issue_date | amount | confidence | needs_review | raw_excerpt
   ```

2. **Share the sheet** with your service account email:
   - Open your Google Sheet
   - Click "Share" button
   - Add the service account email (found in your JSON credentials under "client_email")
   - Give "Editor" permissions

3. **Get the Sheet ID**:
   - Copy the ID from your sheet URL and set it as the `SHEET_ID` secret

### 3. Service Account Setup

1. **Create a Google Cloud Project** (if you don't have one)
2. **Enable the Google Sheets API**:
   - Go to Google Cloud Console → APIs & Services → Library
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

### 4. Deployment

#### For Replit Autoscale:
1. Set the health check path to `/healthz`
2. The app will automatically bind to port 5000
3. All secrets should be configured in Replit Secrets

#### For other platforms:
- Ensure port 5000 is accessible
- Set environment variables instead of Replit secrets
- Install dependencies: `pip install fastapi uvicorn[standard] pdfminer.six google-api-python-client google-auth python-multipart`

## API Endpoints

### `GET /healthz`
Health check endpoint that returns `{"ok": true}`.

### `GET /upload`
Serves the web interface for uploading PDF files.

### `POST /api/upload`
Processes uploaded PDF files and returns extraction results.

**Request**: Multipart form data with PDF files
**Response**: JSON with extraction results

### `GET /selfcheck?pw=PASSWORD`
Administrative endpoint that adds a test row to the Google Sheet.

**Parameters**: `pw` - Admin password (set via `ADMIN_PASSWORD` secret)

## File Processing Limits

- **File Type**: PDF only (checked by MIME type and extension)
- **File Size**: Maximum 3MB per file
- **Multiple Files**: Supported (drag & drop or browse)
- **Processing**: Files are temporarily stored during processing and automatically deleted

## Data Extraction Patterns

### Amount Extraction
- `合計[\\s:：]*([\\d,]+\\.?\\d*)`
- `請求金額[\\s:：]*([\\d,]+\\.?\\d*)`
- `¥\\s?([\\d,]+)`
- `JPY\\s?([\\d,]+)`

### Date Extraction
- `発行日[:：]?\\s?(\\d{4}[/-]\\d{1,2}[/-]\\d{1,2})`
- `(\\d{4}[.-]\\d{1,2}[.-]\\d{1,2})`
- `(\\d{4}年\\d{1,2}月\\d{1,2}日)`

### Vendor Extraction
- Predefined company names (Amazon, Google, Meta, etc.)
- Heuristic extraction from document header

## Confidence Scoring

- **Score Range**: 0.00 to 1.00
- **Calculation**: (Number of successfully extracted fields) / 3
- **Review Flag**: Set to "TRUE" if confidence < 0.67

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
   - File may be corrupted or password-protected
   - Ensure file is a proper PDF with extractable text

4. **"File size exceeds 3MB limit"**:
   - Compress PDF or split into smaller files
   - This is a deliberate limit for the Lite version

### HTTP Status Codes:
- `200`: Success
- `400`: Bad request (no files, invalid format)
- `401`: Unauthorized (wrong admin password)
- `422`: Unprocessable entity (file validation failed)
- `500`: Internal server error

## Monitoring and Logs

- Processing time is logged for each file
- Extraction results are logged with confidence scores
- Failed operations are logged with error details
- Use `/selfcheck` endpoint to verify Google Sheets integration

## Limitations (Lite Version)

- Text-based PDF extraction only (no OCR for image-based PDFs)
- No email integration or automated fetching
- No Slack notifications
- 3MB file size limit per upload
- Basic vendor recognition patterns

## Support

For issues with:
- **PDF extraction**: Ensure PDFs contain extractable text (not scanned images)
- **Google Sheets**: Verify service account permissions and API access
- **File uploads**: Check file format and size constraints
- **Missing data**: Review confidence scores and use manual review for low-confidence extractions

## Development

To run locally:
```bash
pip install fastapi uvicorn[standard] pdfminer.six google-api-python-client google-auth python-multipart
python main.py
