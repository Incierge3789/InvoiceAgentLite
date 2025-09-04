# Overview

InvoiceAgent Lite is a FastAPI-based web application that processes PDF invoices by extracting financial data and automatically saving results to Google Sheets. The system provides a simple web interface for uploading PDF files, uses text extraction to identify invoice amounts, issue dates, and vendor names, then appends this structured data to a Google Sheet with confidence scoring for quality assessment.

# User Preferences

Preferred communication style: Simple, everyday language.

# System Architecture

## Web Framework
- **FastAPI** serves as the core web framework, providing RESTful API endpoints and serving a simple HTML upload interface
- **CORS middleware** enables cross-origin requests for web browser compatibility
- **File upload handling** supports multiple PDF files with drag-and-drop functionality

## PDF Processing Pipeline
- **Text extraction** uses pdfminer.six library to convert PDF content to plain text
- **Pattern matching** employs regex patterns to identify key financial data:
  - Invoice amounts (¥, JPY, 合計, 請求金額 patterns)
  - Issue dates (multiple date formats including Japanese notation)
  - Vendor names (predefined hints list + heuristic extraction)
- **Confidence scoring** calculates quality metrics (0.00-1.00) based on successful field extraction
- **Data validation** includes file size limits (3MB) and PDF-only restrictions

## Data Storage Architecture
- **Google Sheets integration** serves as the primary data store using Google Sheets API v4
- **Service account authentication** eliminates need for OAuth flows
- **Structured data format** with predefined columns: timestamp, filename, vendor, issue_date, amount, confidence, needs_review, raw_excerpt
- **Append-only operations** add new records without modifying existing data

## Security and Configuration
- **Environment-based configuration** uses Replit Secrets for sensitive data
- **Minimal authentication** protects administrative endpoints with simple password
- **Temporary file handling** ensures uploaded PDFs are processed and immediately deleted

## Error Handling and Monitoring
- **Health check endpoints** provide system status monitoring
- **Self-check functionality** validates Google Sheets connectivity and permissions
- **Comprehensive logging** tracks processing operations and errors
- **Graceful error handling** for PDF parsing failures and API errors

# External Dependencies

## Google Cloud Services
- **Google Sheets API v4** for data storage and retrieval
- **Google Cloud Service Account** for authentication and authorization
- Requires service account JSON credentials with Sheets API access

## Python Libraries
- **FastAPI** - Web framework and API development
- **uvicorn** - ASGI server for production deployment
- **pdfminer.six** - PDF text extraction engine
- **google-api-python-client** - Google APIs client library
- **google-auth** - Google authentication library
- **python-multipart** - File upload handling

## Configuration Requirements
- **SHEET_ID** - Target Google Sheets document identifier
- **GOOGLE_SERVICE_ACCOUNT_JSON** - Service account credentials in JSON format
- **ADMIN_PASSWORD** - Administrative endpoint protection (optional)

## Deployment Platform
- **Replit Autoscale** - Cloud hosting platform with automatic scaling
- Environment supports Python runtime with package management
- Secrets management for secure credential storage