// Tableau Analysis Assistant Chrome Extension - Background Script

// Enhanced logging system for background script
const BackgroundLogger = {
  log: function(level, message, data = null) {
    const timestamp = new Date().toISOString();
    const prefix = `[${timestamp}][${level.toUpperCase()}][BACKGROUND]`;
    
    const consoleMethod = level === 'error' ? 'error' : level === 'warn' ? 'warn' : 'log';
    
    if (data) {
      console[consoleMethod](prefix, message, data);
    } else {
      console[consoleMethod](prefix, message);
    }
    
    // Store in extension storage for debugging
    try {
      chrome.storage.local.get(['backgroundLogs'], (result) => {
        const logs = result.backgroundLogs || [];
        logs.push({
          timestamp: timestamp,
          level: level.toUpperCase(),
          message: message,
          data: data
        });
        
        // Keep only last 100 logs
        const recentLogs = logs.slice(-100);
        chrome.storage.local.set({ backgroundLogs: recentLogs });
      });
    } catch (error) {
      console.warn('[BACKGROUND] Failed to store log:', error);
    }
  },

  debug: function(message, data = null) { this.log('debug', message, data); },
  info: function(message, data = null) { this.log('info', message, data); },
  warn: function(message, data = null) { this.log('warn', message, data); },
  error: function(message, data = null) { this.log('error', message, data); }
};

BackgroundLogger.info('Background script initialization started');

chrome.runtime.onInstalled.addListener((details) => {
  BackgroundLogger.info('Extension installed', {
    reason: details.reason,
    previousVersion: details.previousVersion
  });
  
  console.log('[Tableau Assistant] Extension installed:', details);
  // Extension installed - no notification needed
});

// Handle tab updates to inject on Tableau pages
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  BackgroundLogger.debug('Tab updated', {
    tabId: tabId,
    status: changeInfo.status,
    url: tab.url ? tab.url.substring(0, 100) + '...' : 'no url'
  });
  
  if (changeInfo.status === 'complete' && tab.url && isTableauUrl(tab.url)) {
    BackgroundLogger.info('Tableau page detected', {
      tabId: tabId,
      url: tab.url
    });
    
    console.log('[Tableau Assistant] Tableau page detected:', tab.url);
    
    // Note: network-interceptor.js is now injected at document_start via manifest.json
    // This ensures it loads before Tableau's scripts and can capture early API calls
    
    // Inject content script as fallback (manifest.json also injects it at document_end)
    BackgroundLogger.info('Injecting content script (fallback)', { tabId: tabId });
    
    chrome.scripting.executeScript({
      target: { tabId: tabId },
      files: ['content-script.js']
    }).then(() => {
      BackgroundLogger.info('Content script injected successfully', { tabId: tabId });
    }).catch(err => {
      // Script might already be injected via manifest, which is fine
      if (!err.message.includes('already injected')) {
        BackgroundLogger.warn('Failed to inject script', {
          tabId: tabId,
          error: err.message
        });
        console.warn('[Tableau Assistant] Failed to inject script:', err);
      } else {
        BackgroundLogger.debug('Content script already injected', { tabId: tabId });
      }
    });
  }
});

// Check if URL is a Tableau page
function isTableauUrl(url) {
  if (!url) return false;
  
  return (
    url.includes('tableau.com') ||
    url.includes('tableauusercontent.com') ||
    url.includes('tableauonline.com') ||
    url.includes('tableauserver.com') ||
    url.includes('tableau.uberinternal.com') ||
    url.includes('/tableau/') ||
    url.includes('/views/') ||
    url.includes('/t/') && url.includes('/authoring/')
  );
}

// Handle messages from content scripts
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  BackgroundLogger.info('Message received from content script', {
    action: request.action,
    tabId: sender.tab?.id,
    url: sender.tab?.url?.substring(0, 100) + '...'
  });
  
  console.log('[Tableau Assistant] Message received:', request);
  
  // Proxy network requests to avoid mixed-content/CORS from content scripts
  if (request.action === 'proxyFetch') {
    const { url, method = 'GET', headers = {}, body = null, timeoutMs = 300000 } = request;
    BackgroundLogger.info('Proxy fetch requested', { url, method });

    (async () => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

        const fetchOptions = {
          method,
          headers,
          signal: controller.signal
        };
        if (body !== null && body !== undefined) {
          fetchOptions.body = typeof body === 'string' ? body : JSON.stringify(body);
        }

        const response = await fetch(url, fetchOptions);
        clearTimeout(timeoutId);

        const contentType = response.headers.get('content-type') || '';
        const responseText = await response.text();

        sendResponse({
          success: true,
          ok: response.ok,
          status: response.status,
          statusText: response.statusText,
          headers: Object.fromEntries(response.headers.entries()),
          body: responseText,
          contentType
        });
      } catch (error) {
        BackgroundLogger.error('Proxy fetch failed', { url, error: error.message });
        sendResponse({ success: false, error: error.message });
      }
    })();

    return true; // Keep message channel open for async response
  }

  if (request.action === 'checkBackendConnection') {
    BackgroundLogger.info('Testing backend connection');
    
    // Test connection to Flask backend (health check only)
    fetch('http://localhost:8502/api/get_worksheets?connection_key=health_check')
      .then(response => {
        BackgroundLogger.info('Backend connection response received', {
          status: response.status,
          ok: response.ok
        });
        return response.json();
      })
      .then(data => {
        BackgroundLogger.info('Backend connection successful', { data: data });
        sendResponse({ success: true, data: data });
      })
      .catch(error => {
        BackgroundLogger.error('Backend connection failed', {
          error: error.message
        });
        sendResponse({ success: false, error: error.message });
      });
    
    return true; // Keep message channel open for async response
  }
  
  if (request.action === 'logError') {
    console.error('[Tableau Assistant] Error from content script:', request.error);
  }
  
  if (request.action === 'logDebug') {
    console.log('[Tableau Assistant] Debug from content script:', request.message, request.data);
  }
  
  if (request.action === 'networkRequestCaptured') {
    BackgroundLogger.info('Network request captured by interceptor', {
      url: request.url,
      status: request.status,
      method: request.method,
      requestId: request.requestId
    });
    console.log('[Tableau Assistant] Network request captured:', request.url);
  }
});

// Handle extension icon click
chrome.action.onClicked.addListener((tab) => {
  if (isTableauUrl(tab.url)) {
    // Send message to content script to show/hide assistant
    chrome.tabs.sendMessage(tab.id, { action: 'toggleAssistant' })
      .catch(err => {
        console.warn('[Tableau Assistant] Could not communicate with content script:', err);
        
        // Try to inject content script if not present
        chrome.scripting.executeScript({
          target: { tabId: tab.id },
          files: ['content-script.js']
        }).then(() => {
          // Wait a moment then try again
          setTimeout(() => {
            chrome.tabs.sendMessage(tab.id, { action: 'toggleAssistant' });
          }, 500);
        });
      });
  } else {
    // Not on a Tableau page - log only
    console.log('[Tableau Assistant] Not on a Tableau page:', tab.url);
  }
});

// Periodic health check for the backend connection
setInterval(async () => {
  try {
    // Use a simple endpoint that doesn't create state confusion
    const response = await fetch('http://localhost:8502/');
    if (response.ok) {
      console.log('[Tableau Assistant] Backend health check: OK');
    } else {
      console.warn('[Tableau Assistant] Backend health check failed:', response.status);
    }
  } catch (error) {
    console.warn('[Tableau Assistant] Backend not reachable:', error.message);
  }
}, 60000); // Check every minute

// Handle storage changes
chrome.storage.onChanged.addListener((changes, namespace) => {
  console.log('[Tableau Assistant] Storage changed:', changes, namespace);
});

// Clean up when extension is disabled/uninstalled
chrome.runtime.onSuspend.addListener(() => {
  console.log('[Tableau Assistant] Extension suspending');
});

console.log('[Tableau Assistant] Background script loaded');
