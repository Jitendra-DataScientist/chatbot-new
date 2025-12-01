# Tableau Analysis Assistant - Chrome Extension

A Chrome browser extension that injects an AI-powered Analysis Assistant into any Tableau dashboard, providing guided insights and predictions with true viewport positioning.

## 🚀 Features

- **True Viewport Positioning**: Button stays at bottom-right of browser window, not constrained by iframes
- **Universal Tableau Support**: Works on any Tableau dashboard (tableau.com, Tableau Server, Tableau Online)
- **Chart Selection**: Choose specific charts for targeted analysis
- **Real-time Insights**: AI-powered analysis of your dashboard data
- **Seamless Integration**: Floats over existing dashboards without interference

## 📋 Prerequisites

1. **Flask Backend**: Your existing Flask application must be running on `http://localhost:8502`
2. **Chrome Browser**: Version 88 or later (Manifest V3 support)

## 🛠️ Installation

### Method 1: Load Unpacked Extension (Development)

1. **Download/Clone the Extension**:
   ```bash
   # The chrome-extension folder contains all necessary files
   ```

2. **Open Chrome Extensions Page**:
   - Go to `chrome://extensions/`
   - Enable "Developer mode" (toggle in top-right)

3. **Load the Extension**:
   - Click "Load unpacked"
   - Select the `chrome-extension` folder
   - The extension should appear in your extensions list

4. **Pin the Extension** (Optional):
   - Click the Extensions icon (puzzle piece) in Chrome toolbar
   - Click the pin icon next to "Tableau Analysis Assistant"

### Method 2: Install from Chrome Web Store (Future)

*This extension can be packaged and published to the Chrome Web Store for easier distribution.*

## 🚦 Usage

### Initial Setup

1. **Start your Flask backend**:
   ```bash
   python app.py  # Should run on http://localhost:8502
   ```

2. **Navigate to a Tableau dashboard**:
   - Any Tableau site (tableau.com, your server, etc.)
   - The extension auto-detects Tableau pages

3. **Activate the Assistant**:
   - Click the extension icon in Chrome toolbar, OR
   - Look for the floating "Analysis Assistant" button (bottom-right corner)

### Using the Assistant

1. **Open the Interface**:
   - Click the floating button or use the popup
   - The chat interface opens at bottom-right of your screen

2. **Select a Chart**:
   - Choose from available charts in your dashboard
   - Charts are automatically detected from your backend

3. **Ask Questions**:
   - Type questions about the selected chart
   - Get AI-powered insights and analysis

4. **Navigate**:
   - Use the "Back" button to switch between charts
   - Minimize/maximize as needed

## 🎛️ Extension Components

### Files Structure
```
chrome-extension/
├── manifest.json          # Extension configuration
├── content-script.js       # Main assistant logic
├── content-style.css       # UI styling with viewport positioning  
├── background.js          # Background tasks and tab management
├── popup.html             # Extension popup interface
├── popup.js              # Popup functionality
├── icons/                # Extension icons
└── README.md             # This file
```

### Key Features

- **Viewport Positioning**: Uses `position: fixed` with maximum z-index for true screen positioning
- **Tableau Detection**: Automatically detects Tableau pages and injects the assistant
- **Backend Communication**: Maintains connection with your Flask API
- **Error Handling**: Graceful fallbacks and connection retry logic
- **Responsive Design**: Adapts to different screen sizes

## 🔧 Configuration

### Backend URL
The extension connects to `http://localhost:8502` by default. To change this:

1. Edit `content-script.js`
2. Update the `backendUrl` in `extensionState`:
   ```javascript
   let extensionState = {
     backendUrl: 'https://your-backend-url.com',
     // ... rest of config
   };
   ```

### Permissions
The extension requests these permissions:
- `activeTab`: Access current tab content
- `storage`: Save user preferences
- `scripting`: Inject content scripts
- `host_permissions`: Access to Tableau domains and your backend

## 🐛 Troubleshooting

### Common Issues

1. **Assistant doesn't appear**:
   - Check if you're on a Tableau page
   - Refresh the page and try again
   - Check browser console for errors

2. **Backend connection fails**:
   - Ensure Flask app is running on correct port
   - Check CORS settings in your Flask app
   - Verify firewall isn't blocking localhost:8502

3. **Charts don't load**:
   - Verify your Flask backend `/api/get_worksheets` endpoint
   - Check network tab for failed requests
   - Ensure backend has access to Tableau data

4. **Permission errors**:
   - Make sure extension has all required permissions
   - Try reloading the extension in `chrome://extensions/`

### Debug Tools

The extension provides a debug interface:
```javascript
// In browser console on Tableau page:
window.tableauAnalysisAssistant.getState()  // View current state
window.tableauAnalysisAssistant.showChat()  // Force show chat
window.tableauAnalysisAssistant.debug.loadCharts()  // Reload charts
```

## 🔒 Security & Privacy

- Extension only activates on Tableau pages
- All data communication is between your browser and your own Flask backend
- No data is sent to external services
- Extension runs with minimal required permissions

## 🤝 Compatibility

### Supported Tableau Platforms
- ✅ Tableau Online (tableau.com)
- ✅ Tableau Server (on-premise)
- ✅ Tableau Public
- ✅ Embedded Tableau dashboards

### Browser Support
- ✅ Chrome 88+ (Manifest V3)
- ✅ Microsoft Edge 88+ (Chromium-based)
- ❌ Firefox (uses different extension format)
- ❌ Safari (uses different extension format)

## 🚀 Development

### Building for Production

1. **Create Extension Package**:
   ```bash
   # Zip the chrome-extension folder
   cd chrome-extension
   zip -r tableau-assistant-extension.zip .
   ```

2. **Chrome Web Store Publishing**:
   - Upload the zip file to Chrome Developer Dashboard
   - Fill out store listing details
   - Submit for review

### Local Development

1. **Make Changes**: Edit files in `chrome-extension/`
2. **Reload Extension**: Go to `chrome://extensions/` and click reload
3. **Test**: Navigate to Tableau page and test functionality

## 📝 Migration from .trex Extension

This Chrome extension replaces the iframe-based `.trex` extension and provides:

- ✅ True viewport positioning (no iframe limitations)
- ✅ Works on any Tableau site (not just local extensions)
- ✅ Better user experience and reliability
- ✅ No need to add `.trex` files to Tableau

Your existing Flask backend works unchanged - only the client interface is different.

## 📞 Support

For issues or questions:
1. Check the troubleshooting section above
2. Review browser console errors
3. Verify backend is running and accessible
4. Test with the debug tools provided

## 📄 License

This extension is designed to work with your existing Tableau Analysis Assistant Flask application.
