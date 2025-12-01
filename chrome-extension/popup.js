// Tableau Analysis Assistant Chrome Extension - Popup Script

// Enhanced logging system for popup
const PopupLogger = {
  log: function(level, message, data = null) {
    const timestamp = new Date().toISOString();
    const prefix = `[${timestamp}][${level.toUpperCase()}][POPUP]`;
    
    const consoleMethod = level === 'error' ? 'error' : level === 'warn' ? 'warn' : 'log';
    
    if (data) {
      console[consoleMethod](prefix, message, data);
    } else {
      console[consoleMethod](prefix, message);
    }
  },

  debug: function(message, data = null) { this.log('debug', message, data); },
  info: function(message, data = null) { this.log('info', message, data); },
  warn: function(message, data = null) { this.log('warn', message, data); },
  error: function(message, data = null) { this.log('error', message, data); }
};

PopupLogger.info('Popup script initializing');

document.addEventListener('DOMContentLoaded', async () => {
  PopupLogger.info('DOM content loaded, setting up popup');
  
  const statusDiv = document.getElementById('status');
  const pageStatusDiv = document.getElementById('page-status');
  const backendDot = document.getElementById('backend-dot');
  const backendText = document.getElementById('backend-text');
  const toggleBtn = document.getElementById('toggle-btn');
  const refreshBtn = document.getElementById('refresh-btn');
  const helpBtn = document.getElementById('help-btn');

  PopupLogger.debug('UI elements referenced', {
    statusDiv: !!statusDiv,
    pageStatusDiv: !!pageStatusDiv,
    backendDot: !!backendDot,
    backendText: !!backendText,
    toggleBtn: !!toggleBtn,
    refreshBtn: !!refreshBtn,
    helpBtn: !!helpBtn
  });

  let currentTab = null;
  let backendConnected = false;
  let isTableauPage = false;

  // Get current tab
  async function getCurrentTab() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    return tab;
  }

  // Check if current page is a Tableau page
  function isTableauUrl(url) {
    if (!url) return false;
    
    return (
      url.includes('tableau.com') ||
      url.includes('tableauusercontent.com') ||
      url.includes('tableauonline.com') ||
      url.includes('tableauserver.com') ||
      url.includes('/tableau/') ||
      url.includes('/views/')
    );
  }

  // Check backend connection
  async function checkBackendConnection() {
    PopupLogger.info('Checking backend connection');
    
    try {
      backendDot.className = 'status-dot checking';
      backendText.textContent = 'Checking backend connection...';
      
      PopupLogger.debug('Sending request to backend API');
      const response = await fetch('http://localhost:8502/api/state');
      
      PopupLogger.debug('Backend response received', {
        status: response.status,
        ok: response.ok
      });
      
      if (response.ok) {
        const data = await response.json();
        backendConnected = true;
        backendDot.className = 'status-dot connected';
        backendText.textContent = 'Backend connected';
        
        PopupLogger.info('Backend connection successful', { data: data });
        return true;
      } else {
        throw new Error(`HTTP ${response.status}`);
      }
    } catch (error) {
      backendConnected = false;
      backendDot.className = 'status-dot';
      backendText.textContent = 'Backend not available';
      
      PopupLogger.error('Backend connection failed', {
        error: error.message
      });
      return false;
    }
  }

  // Update page status
  async function updatePageStatus() {
    currentTab = await getCurrentTab();
    isTableauPage = isTableauUrl(currentTab.url);
    
    if (isTableauPage) {
      pageStatusDiv.textContent = 'Tableau dashboard detected ✓';
      statusDiv.className = 'status active';
    } else {
      pageStatusDiv.textContent = 'Not on a Tableau page';
      statusDiv.className = 'status inactive';
    }
    
    updateButtons();
  }

  // Update button states
  function updateButtons() {
    const canUse = isTableauPage && backendConnected;
    
    toggleBtn.disabled = !canUse;
    refreshBtn.disabled = !backendConnected;
    
    if (canUse) {
      toggleBtn.textContent = 'Toggle Assistant';
      toggleBtn.className = 'primary';
    } else if (!isTableauPage) {
      toggleBtn.textContent = 'Navigate to Tableau First';
      toggleBtn.className = '';
    } else if (!backendConnected) {
      toggleBtn.textContent = 'Backend Not Available';
      toggleBtn.className = '';
    }
  }

  // Toggle assistant
  async function toggleAssistant() {
    if (!currentTab || !isTableauPage) {
      alert('Please navigate to a Tableau dashboard first.');
      return;
    }

    try {
      await chrome.tabs.sendMessage(currentTab.id, { action: 'toggleAssistant' });
      window.close(); // Close popup after successful toggle
    } catch (error) {
      console.warn('Could not communicate with content script:', error);
      
      // Try to inject content script if not present
      try {
        await chrome.scripting.executeScript({
          target: { tabId: currentTab.id },
          files: ['content-script.js']
        });
        
        // Wait a moment then try again
        setTimeout(async () => {
          try {
            await chrome.tabs.sendMessage(currentTab.id, { action: 'toggleAssistant' });
            window.close();
          } catch (retryError) {
            alert('Failed to activate assistant. Please refresh the page and try again.');
          }
        }, 1000);
      } catch (injectError) {
        alert('Failed to inject assistant. Please refresh the page and try again.');
      }
    }
  }

  // Refresh backend connection
  async function refreshConnection() {
    statusDiv.className = 'status loading';
    await checkBackendConnection();
    updateButtons();
  }

  // Show help
  function showHelp() {
    const helpText = `
Tableau Analysis Assistant Help

SETUP:
1. Make sure your Flask backend is running on http://localhost:8502
2. Navigate to any Tableau dashboard
3. Click the "Toggle Assistant" button or use the floating button

USAGE:
- The assistant will appear as a floating button in the bottom-right corner
- Click it to open the chat interface
- Select a chart from the list, then ask questions about your data

TROUBLESHOOTING:
- If the assistant doesn't appear, try refreshing the page
- Make sure your backend server is running
- Check the browser console for any error messages

Backend URL: http://localhost:8502
Extension Version: 1.0.0
    `;
    
    alert(helpText);
  }

  // Event listeners
  toggleBtn.addEventListener('click', toggleAssistant);
  refreshBtn.addEventListener('click', refreshConnection);
  helpBtn.addEventListener('click', showHelp);

  // Initial setup
  await updatePageStatus();
  await checkBackendConnection();
  updateButtons();

  // Auto-refresh every 30 seconds
  setInterval(async () => {
    await checkBackendConnection();
    updateButtons();
  }, 30000);
});
