# Tableau Credentials Management via Google Sheets

This system allows you to store Tableau credentials securely in Google Sheets and fetch them via username lookup, eliminating the need to hardcode credentials in `pass_config.json`.

## 📋 Table of Contents
1. [Google Sheet Setup](#1-google-sheet-setup)
2. [Google Apps Script Deployment](#2-google-apps-script-deployment)
3. [Configuration](#3-configuration)
4. [Usage in Python](#4-usage-in-python)
5. [Testing](#5-testing)
6. [Security Considerations](#6-security-considerations)

---

## 1. Google Sheet Setup

### Step 1.1: Create a New Google Sheet

1. Go to [Google Sheets](https://sheets.google.com)
2. Create a new spreadsheet
3. Name it something like "Tableau Credentials Store"
4. Rename the first sheet to **"Credentials"** (or update `SHEET_NAME` in the Apps Script)

### Step 1.2: Set Up Columns

Add the following headers in **Row 1**:

| Column A | Column B | Column C | Column D (Optional) | Column E (Optional) |
|----------|----------|----------|---------------------|---------------------|
| username | password | site_content_url | tableau_server_url | api_version |

### Step 1.3: Add Your Credentials

Starting from **Row 2**, add your credentials:

**Example:**

| username | password | site_content_url | tableau_server_url | api_version |
|----------|----------|------------------|-------------------|-------------|
| cca49542@gmail.com | Tableau@126 | cca49542-0680f1e1d6 | https://prod-in-a.online.tableau.com | 3.21 |
| user2@company.com | SecurePass456 | site-xyz-123 | https://prod-in-a.online.tableau.com | 3.21 |

**Notes:**
- Columns D and E are optional (will use defaults if not provided)
- Default server URL: `https://prod-in-a.online.tableau.com`
- Default API version: `3.21`

---

## 2. Google Apps Script Deployment

### Step 2.1: Open Apps Script Editor

1. In your Google Sheet, go to **Extensions > Apps Script**
2. Delete any default code

### Step 2.2: Copy the Script

1. Open the file `google_apps_script_for_credentials.js` (provided in this repo)
2. Copy the entire contents
3. Paste it into the Apps Script editor
4. (Optional) Verify `SHEET_NAME` matches your sheet name (line 25)

### Step 2.3: Deploy as Web App

1. Click **Deploy** (top right) → **New deployment**
2. Click the gear icon ⚙️ next to "Select type" → Choose **Web app**
3. Configure deployment:
   - **Description**: Tableau Credentials API
   - **Execute as**: **Me** (your account)
   - **Who has access**: **Anyone** (or "Anyone with the link" if you want more control)
4. Click **Deploy**
5. **Authorize** the script (you'll see a permission prompt)
   - Click "Review permissions"
   - Choose your Google account
   - Click "Advanced" → "Go to [Project name] (unsafe)"
   - Click "Allow"
6. **Copy the Web App URL** (you'll need this for configuration)

**Example URL:**
```
https://script.google.com/macros/s/AKfycbxXXXXXXXXXXXXXXX/exec
```

### Step 2.4: Test the Deployment

1. In Apps Script, go to **Run** → Select `testCredentialsLookup`
2. Check the **Execution log** (bottom of the screen)
3. You should see: `✓ Test PASSED - Found credentials for: cca49542@gmail.com`

---

## 3. Configuration

### Step 3.1: Update `google_sheets_config.json`

Open `google_sheets_config.json` and update the `tableau_credentials` section:

```json
{
  "tableau_credentials": {
    "enabled": true,
    "apps_script_url": "YOUR_WEB_APP_URL_HERE",
    "sheet_url": "YOUR_GOOGLE_SHEET_URL",
    "timeout_seconds": 10,
    "retry_attempts": 2,
    "cache_duration_seconds": 300
  }
}
```

**Replace:**
- `YOUR_WEB_APP_URL_HERE` → The URL from Step 2.3
- `YOUR_GOOGLE_SHEET_URL` → Your Google Sheet URL (for reference only)

**Example:**
```json
{
  "tableau_credentials": {
    "enabled": true,
    "apps_script_url": "https://script.google.com/macros/s/AKfycbxXXXXXXXXXXXXXXX/exec",
    "sheet_url": "https://docs.google.com/spreadsheets/d/1abcDEF123456/edit",
    "timeout_seconds": 10,
    "retry_attempts": 2,
    "cache_duration_seconds": 300
  }
}
```

---

## 4. Usage in Python

### Option 1: Direct Lookup (New Way) ⭐

```python
from services.tableau_credentials_service import get_tableau_credentials

# Fetch credentials by username
username = "cca49542@gmail.com"
success, credentials, error = get_tableau_credentials(username)

if success:
    print(f"Username: {credentials['username']}")
    print(f"Password: {credentials['password']}")
    print(f"Site Content URL: {credentials['site_content_url']}")
    print(f"Server URL: {credentials['tableau_server_url']}")
else:
    print(f"Error: {error}")
```

### Option 2: Smart Fallback (Recommended)

This method tries Google Sheets first, then falls back to `pass_config.json`:

```python
from services.tableau_credentials_service import load_tableau_config

# Load config with automatic fallback
config = load_tableau_config(username="cca49542@gmail.com")

print(f"Loaded credentials for: {config['username']}")
# Use config as before...
```

### Option 3: Update Existing Code

Find where you currently load `pass_config.json`:

**Before:**
```python
import json
with open('pass_config.json', 'r') as f:
    config = json.load(f)
```

**After:**
```python
from services.tableau_credentials_service import load_tableau_config

# Replace with your username (or get from environment/user input)
config = load_tableau_config(username="cca49542@gmail.com")
```

---

## 5. Testing

### Test 1: Connection Test

```bash
cd "C:\Users\achoud85\Downloads\New folder (3)\New folder"
python -c "from services.tableau_credentials_service import tableau_credentials_service; print(tableau_credentials_service.test_connection())"
```

Expected output:
```
(True, {'success': True, 'message': '...', 'timestamp': '...'}, None)
```

### Test 2: Credentials Lookup

```bash
python -c "from services.tableau_credentials_service import get_tableau_credentials; print(get_tableau_credentials('cca49542@gmail.com'))"
```

Expected output:
```
(True, {'username': 'cca49542@gmail.com', 'password': 'Tableau@126', ...}, None)
```

### Test 3: Run Full Test Suite

```bash
python services/tableau_credentials_service.py
```

---

## 6. Security Considerations

### ✅ Benefits
- **No hardcoded passwords** in your codebase
- **Centralized credential management** (one sheet for all users)
- **Easy credential rotation** (just update the sheet, no code changes)
- **In-memory caching** (5-minute cache, no persistent storage)
- **Automatic fallback** to `pass_config.json` if Google Sheets is unavailable

### ⚠️ Security Best Practices

1. **Restrict Sheet Access**
   - Only share the Google Sheet with authorized users
   - Use "Specific people" sharing (not "Anyone with the link")

2. **Restrict Apps Script Access**
   - Consider deploying with "Only myself" access
   - Or use "Anyone with the link" and keep the URL secret

3. **Use Personal Access Tokens (PATs)**
   - Instead of passwords, add a `token_name` and `token_secret` column
   - Update the Apps Script and Python code to support PATs

4. **Enable 2FA on Google Account**
   - Protect the Google account that owns the sheet

5. **Monitor Access**
   - Regularly review who has access to the sheet
   - Check Apps Script execution logs for unusual activity

6. **Add .gitignore Rules**
   ```bash
   echo "pass_config.json" >> .gitignore
   echo "google_sheets_config.json" >> .gitignore
   ```

### 🔒 Advanced: Using Environment Variables

For the Apps Script URL (semi-sensitive):

```python
import os
from dotenv import load_dotenv

load_dotenv()

# In .env file:
# CREDENTIALS_APPS_SCRIPT_URL=https://script.google.com/...

# Update the config dynamically
tableau_credentials_service.apps_script_url = os.getenv('CREDENTIALS_APPS_SCRIPT_URL')
```

---

## 7. Troubleshooting

### Error: "Credentials service is disabled"
- Check `google_sheets_config.json` → `"enabled": true`

### Error: "Apps Script URL not configured"
- Update `apps_script_url` in `google_sheets_config.json`

### Error: "Credentials not found for username"
- Verify the username matches exactly (case-insensitive)
- Check the Google Sheet has the username in Column A
- Verify `SHEET_NAME` in Apps Script matches your sheet name

### Error: "Connection error - unable to reach Apps Script endpoint"
- Check your internet connection
- Verify the Apps Script URL is correct
- Ensure the Apps Script is deployed (not just saved)

### Error: "Request timeout after 10 seconds"
- Increase `timeout_seconds` in config
- Check if Google Apps Script is experiencing issues

---

## 8. Migration Path

If you want to keep `pass_config.json` as a backup but transition to Google Sheets:

1. **Phase 1**: Keep both systems (automatic fallback)
   - Set `"enabled": true` in config
   - Keep `pass_config.json` as fallback

2. **Phase 2**: Test thoroughly
   - Verify credentials loading works
   - Test with multiple users

3. **Phase 3**: Remove `pass_config.json`
   - Once confident, delete or move `pass_config.json` to a secure location
   - Update code to handle missing fallback gracefully

---

## 9. Next Steps

1. ✅ Set up Google Sheet
2. ✅ Deploy Apps Script
3. ✅ Update configuration
4. ✅ Test the system
5. 🔜 Update your application code to use the new service
6. 🔜 Add more users to the sheet
7. 🔜 Consider migrating to Personal Access Tokens

---

## Support

For issues or questions:
- Check the logs: `master_debug.log`
- Enable debug logging in `google_sheets_config.json`
- Review Apps Script execution logs in Google Apps Script editor

---

**Created**: 2024  
**Version**: 1.0  
**Author**: Tableau Credentials Management System

