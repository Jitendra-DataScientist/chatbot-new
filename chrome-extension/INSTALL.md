# Quick Installation Guide

## 🚀 Install the Chrome Extension

### Step 1: Prepare Your Backend
Make sure your Flask application is running:
```bash
cd /path/to/your/tableau-agent
python app.py
```
Verify it's accessible at: http://localhost:8502

### Step 2: Install Extension in Chrome

1. **Open Chrome Extensions**:
   - Type `chrome://extensions/` in your address bar
   - OR: Chrome Menu → More Tools → Extensions

2. **Enable Developer Mode**:
   - Toggle "Developer mode" switch (top-right corner)

3. **Load the Extension**:
   - Click "Load unpacked" button
   - Navigate to and select the `chrome-extension` folder
   - The extension should appear in your list with a "TA" icon

### Step 3: Pin the Extension (Recommended)
1. Click the Extensions icon (🧩) in Chrome toolbar
2. Find "Tableau Analysis Assistant"
3. Click the pin icon to keep it visible

## ✅ Test the Installation

### Quick Test:
1. **Navigate to any Tableau dashboard**
   - tableau.com, your Tableau Server, etc.

2. **Look for the floating button**
   - Should appear at bottom-right corner of screen
   - Says "Analysis Assistant"

3. **Click the extension icon**
   - Should show green "Backend connected" status
   - "Toggle Assistant" button should be enabled

### If Something's Wrong:
- ✅ Backend running on localhost:8502?
- ✅ On a Tableau page?
- ✅ Extension loaded properly?
- ✅ Check browser console for errors (F12)

## 🎯 Using the Assistant

1. **Open any Tableau dashboard**
2. **Click the floating button** or extension icon
3. **Select a chart** from the list
4. **Ask questions** about your data!

## 🔧 Troubleshooting

### Extension not visible:
```bash
# Check these in order:
1. Extension loaded in chrome://extensions/?
2. On a Tableau page? (not just tableau.com homepage)
3. Backend running and accessible?
4. Try refreshing the page
```

### Backend connection issues:
```bash
# Test backend directly:
curl http://localhost:8502/api/state

# Should return JSON with connection info
```

### No charts available:
```bash
# Check backend logs for errors
# Verify Tableau workbook is properly connected
# Try refreshing connection in extension popup
```

## 📱 Next Steps

Once installed:
- The extension automatically detects Tableau pages
- Button appears on every dashboard
- Your existing Flask backend handles all the AI logic
- No need to modify existing `.trex` configuration

**Enjoy your new viewport-positioned Analysis Assistant!** 🎉
