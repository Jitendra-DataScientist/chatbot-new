// Tableau Analysis Assistant Chrome Extension - Content Script
(function() {
  'use strict';

  // Prevent multiple instances
  if (window.tableauAnalysisAssistantLoaded) {
    return;
  }
  window.tableauAnalysisAssistantLoaded = true;

  // Debug flag - set to true for verbose logging
  const DEBUG = true;

  // Extension state management - MUST be declared early for ContentLogger
  let extensionState = {
    ready: false,
    connected: false,
    backendUrl: 'http://localhost:8502',
    context: {
      dashboardName: null,
      workbookName: null,
      worksheetNames: [],
      filters: {},
      selection: [],
      selectedData: null,
      activeWorksheet: null
    },
    currentUrl: window.location.href
  };

  // UI elements (declare early to avoid TDZ when early-return paths reference it)
  let ui = null;

  // Proxy fetch via background to avoid mixed-content/CORS from content script
  async function proxyFetch(url, options = {}) {
    const { method = 'GET', headers = {}, body = null, timeoutMs = 300000 } = options;
    return new Promise((resolve, reject) => {
      try {
        chrome.runtime.sendMessage(
          {
            action: 'proxyFetch',
            url,
            method,
            headers,
            body,
            timeoutMs
          },
          (response) => {
            const lastError = chrome.runtime.lastError;
            if (lastError) {
              return reject(new Error(lastError.message));
            }
            if (!response) {
              return reject(new Error('No response from background'));
            }
            if (response.success === false) {
              return reject(new Error(response.error || 'Proxy fetch failed'));
            }
            const resp = {
              ok: !!response.ok,
              status: response.status,
              statusText: response.statusText,
              headers: response.headers || {},
              contentType: response.contentType || '',
              text: async () => response.body || '',
              json: async () => {
                try { return JSON.parse(response.body || 'null'); }
                catch (e) { throw new Error('Failed to parse JSON: ' + e.message); }
              }
            };
            resolve(resp);
          }
        );
      } catch (err) {
        reject(err);
      }
    });
  }

  // ============================================================================
  // EXPORT STATUS POLLING (for Chrome Extension)
  // ============================================================================
  let exportProgressPoller = null;
  let currentConnectionKey = null;

  function pollExportStatus() {
    if (!currentConnectionKey) {
      stopExportStatusPolling();
      return;
    }
    
    const url = `${extensionState.backendUrl}/api/export-status?connection_key=${encodeURIComponent(currentConnectionKey)}`;
    
    proxyFetch(url)
      .then(response => response.json())
      .then(status => {
        if (status.in_progress) {
          // Disable chat input
          const chatInput = document.getElementById('tableau-chat-input');
          const chatSubmit = document.getElementById('tableau-chat-submit');
          if (chatInput) chatInput.disabled = true;
          if (chatSubmit) chatSubmit.disabled = true;
          
          // Show progress message in chat
          updateExportProgress(status);
        } else {
          // Export complete or not started
          if (status.stage === 'complete') {
            updateExportProgress({
              ...status,
              message: status.message || 'Dashboard data ready! You can now ask questions.'
            });
            
            // Re-enable chat after brief delay
            setTimeout(async () => {
              hideExportProgress();
              
              // Load and display workbook summary before enabling chat
              await loadAndDisplayWorkbookSummary();
              
              // Then enable chat
              const chatInput = document.getElementById('tableau-chat-input');
              const chatSubmit = document.getElementById('tableau-chat-submit');
              if (chatInput) chatInput.disabled = false;
              if (chatSubmit) chatSubmit.disabled = false;
            }, 2000);
          } else if (status.stage === 'failed') {
            updateExportProgress({
              ...status,
              message: status.message || 'Export failed. You may still ask questions.'
            });
            setTimeout(() => hideExportProgress(), 5000);
          }
          
          // Stop polling if not in progress
          if (!status.in_progress) {
            stopExportStatusPolling();
          }
        }
      })
      .catch(err => {
        ContentLogger.warn('Export status poll error:', err);
        stopExportStatusPolling();
      });
  }

  let lastExportMessageId = null;
  let connectionSummaryMessageId = null;
  
  function updateExportProgress(status) {
    const stageText = {
      'starting': '🚀 Starting',
      'downloading': '📥 Downloading',
      'parsing': '🔍 Parsing',
      'extracting': '📊 Extracting Data',
      'exporting': '💾 Exporting',
      'complete': '✅ Complete',
      'failed': '❌ Failed'
    }[status.stage] || status.stage;
    
    let message = `${stageText}: ${status.message || ''}`;
    
    if (status.datasources_processed > 0 || status.total_datasources > 0) {
      message += `\nData sources: ${status.datasources_processed}/${status.total_datasources}`;
      if (status.current_item) {
        message += ` (${status.current_item})`;
      }
    }
    
    // Update existing message or create new one
    if (lastExportMessageId) {
      updateMessageById(lastExportMessageId, message);
    } else {
      lastExportMessageId = `export-progress-${Date.now()}`;
      appendMessage(message, 'bot', null, lastExportMessageId);  // FIX: pass messageId in correct position
    }
  }
  
  function hideExportProgress() {
    if (lastExportMessageId) {
      const element = document.getElementById(lastExportMessageId);
      if (element) {
        // Fade out animation
        element.style.transition = 'opacity 0.3s ease-out';
        element.style.opacity = '0';
        setTimeout(() => {
          element.remove();
        }, 300);
      }
      lastExportMessageId = null;
    }
    
    // Also hide the connection summary message ("loading data in background...")
    if (connectionSummaryMessageId) {
      const summaryElement = document.getElementById(connectionSummaryMessageId);
      if (summaryElement) {
        summaryElement.style.transition = 'opacity 0.3s ease-out';
        summaryElement.style.opacity = '0';
        setTimeout(() => {
          summaryElement.remove();
        }, 300);
      }
      connectionSummaryMessageId = null;
    }
  }

  function startExportStatusPolling(connectionKey) {
    currentConnectionKey = connectionKey;
    
    // Stop any existing poller
    stopExportStatusPolling();
    
    ContentLogger.info('Starting export status polling', { connectionKey });
    
    // Start polling immediately
    pollExportStatus();
    
    // Then poll every 1 second
    exportProgressPoller = setInterval(pollExportStatus, 1000);
  }

  function stopExportStatusPolling() {
    if (exportProgressPoller) {
      clearInterval(exportProgressPoller);
      exportProgressPoller = null;
      ContentLogger.info('Stopped export status polling');
    }
  }

  // Enhanced logging system for content script
  const ContentLogger = {
    log: function(level, message, data = null, event_type = 'GENERAL') {
      const timestamp = new Date().toISOString();
      const prefix = `[${timestamp}][${level.toUpperCase()}][CONTENT]`;
      
      const consoleMethod = level === 'error' ? 'error' : level === 'warn' ? 'warn' : 'log';
      
      if (data) {
        console[consoleMethod](prefix, message, data);
      } else {
        console[consoleMethod](prefix, message);
      }
      
      // Send to backend for enhanced logging
      this.sendToBackend(level, message, data, event_type);
      
      // Also send to background script for centralized logging
      try {
        chrome.runtime.sendMessage({
          action: 'logDebug',
          level: level,
          message: message,
          data: data,
          timestamp: timestamp,
          url: window.location.href
        }).catch(() => {}); // Ignore if background script not available
      } catch (error) {
        // Ignore messaging errors
      }
    },

    sendToBackend: async function(level, message, data = null, event_type = 'GENERAL') {
      try {
        // Check if extensionState is available (avoid temporal dead zone issues)
        const backendUrl = (typeof extensionState !== 'undefined' && extensionState?.backendUrl) 
          ? extensionState.backendUrl 
          : 'http://localhost:8502';
          
        const tableauContext = (typeof extensionState !== 'undefined' && extensionState?.context) 
          ? extensionState.context 
          : {};

        const logData = {
          event_type: event_type,
          level: level,
          message: message,
          data: {
            ...data,
            timestamp_client: new Date().toISOString(),
            page_title: document.title,
            tableau_context: tableauContext
          },
          url: window.location.href
        };

        await proxyFetch(`${backendUrl}/api/extension/log`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(logData)
        });
      } catch (error) {
        // Silently fail - don't want logging to break the extension
        console.warn('[ContentLogger] Failed to send log to backend:', error);
      }
    },

    debug: function(message, data = null, event_type = 'DEBUG') { this.log('debug', message, data, event_type); },
    info: function(message, data = null, event_type = 'INFO') { this.log('info', message, data, event_type); },
    warn: function(message, data = null, event_type = 'WARNING') { this.log('warn', message, data, event_type); },
    error: function(message, data = null, event_type = 'ERROR') { this.log('error', message, data, event_type); },
    
    // Specialized logging methods
    logChatRequest: function(message, selectedChart, connectionKey) {
      this.log('info', `Chat request: "${message}"`, {
        selected_chart: selectedChart,
        connection_key: connectionKey,
        message_length: message.length
      }, 'CHAT_REQUEST');
    },
    
    logChatResponse: function(response, success = true, error = null) {
      this.log(success ? 'info' : 'error', `Chat response received`, {
        response_preview: response.substring(0, 100),
        full_response: response,
        success: success,
        error: error
      }, success ? 'CHAT_RESPONSE' : 'CHAT_ERROR');
    },
    
    logChartSelection: function(chartName, chartData = null) {
      this.log('info', `Chart selected: ${chartName}`, {
        chart_name: chartName,
        chart_data: chartData
      }, 'CHART_SELECTION');
    },
    
    logConnectionEvent: function(event, success = true, details = null) {
      this.log(success ? 'info' : 'error', `Connection ${event}`, {
        event: event,
        success: success,
        details: details
      }, `CONNECTION_${event.toUpperCase()}`);
    },
    
    logNetworkRequest: function(method, url, status, requestData = null, responseData = null, error = null) {
      this.log(error ? 'error' : 'info', `${method} ${url}`, {
        method: method,
        url: url,
        status_code: status,
        request_data: requestData,
        response_data: responseData,
        error: error
      }, 'NETWORK_REQUEST');
    }
  };
  
  // Enhanced debug logging for connection issues
  function debugLogNetwork(message, data = null) {
    ContentLogger.debug(`Network: ${message}`, data);
    
    if (DEBUG) {
      console.log(`[Tableau Assistant Network] ${message}`, data || '');
      // Also send to background script for centralized logging
      chrome.runtime.sendMessage({
        action: 'logDebug',
        message: `Network: ${message}`,
        data: data
      }).catch(() => {}); // Ignore if background script not available
    }
  }

  function debugLog(message, data = null) {
    ContentLogger.debug(message, data);
    
    if (DEBUG) {
      console.log(`[Tableau Assistant] ${message}`, data || '');
    }
  }

  ContentLogger.info('Content script initializing');
  debugLog('Content script initializing...');

  // Debug: Check if FeedbackManager is available
  if (typeof FeedbackManager !== 'undefined') {
    debugLog('FeedbackManager is available');
  } else {
    debugLog('WARNING: FeedbackManager is not available - feedback functionality will be disabled');
  }


  // Network request capture state - Defined early to prevent ReferenceError on non-dashboard pages
  let networkRequestsState = {
    captureEnabled: false,
    captureStartTime: null,
    captureTimeoutMs: 10000, // Capture for 10 seconds after assistant opens
    requests: [],
    totalCaptured: 0
  };

  /**
   * Extract first name from captured getSessionInfo API response
   * @returns {string|null} First name or null if not found
   */
  function getUserFirstName() {
    try {
      // Search for getSessionInfo in captured network requests
      const sessionInfoRequest = networkRequestsState.requests.find(req => 
        req.url && req.url.includes('getSessionInfo')
      );
      
      if (!sessionInfoRequest) {
        debugLog('getSessionInfo API not yet captured');
        return null;
      }
      
      // Extract displayName from response body (nested under result.user)
      const responseBody = sessionInfoRequest.response_body;
      if (!responseBody || !responseBody.result || !responseBody.result.user || !responseBody.result.user.displayName) {
        debugLog('displayName not found in getSessionInfo response');
        return null;
      }
      
      const displayName = responseBody.result.user.displayName;
      debugLog('Found displayName:', displayName);
      
      // Extract first name (first word before space)
      const nameParts = displayName.trim().split(/\s+/);
      if (nameParts.length === 0 || !nameParts[0]) {
        return null;
      }
      
      // Capitalize first letter of first name
      const firstName = nameParts[0].charAt(0).toUpperCase() + nameParts[0].slice(1).toLowerCase();
      debugLog('Extracted first name:', firstName);
      
      return firstName;
    } catch (error) {
      debugLog('Error extracting first name:', error);
      return null;
    }
  }

  // Listen for intercepted network requests from network-interceptor.js
  window.addEventListener('message', (event) => {
    // Verify message source (must be from same window)
    if (event.source !== window) {
      return;
    }
    
    // Check for our intercepted request message type
    if (event.data && event.data.type === 'TABLEAU_API_INTERCEPTED' && event.data.source === 'network-interceptor') {
      // Always capture requests - the network interceptor only sends relevant ones
      const requestData = event.data.payload;
      
      debugLog('Network request intercepted from page context', {
        url: requestData.url,
        status: requestData.status,
        method: requestData.method,
        id: requestData.id
      });
      
      ContentLogger.info('Network request intercepted and captured', {
        url: requestData.url,
        status: requestData.status,
        method: requestData.method,
        response_size: requestData.response_size_bytes,
        duration_ms: requestData.duration_ms
      }, 'NETWORK_REQUEST_INTERCEPTED');
      
      // Store the request
      networkRequestsState.requests.push(requestData);
      networkRequestsState.totalCaptured++;
      
      // Notify background script (optional, for logging)
      try {
        chrome.runtime.sendMessage({
          action: 'networkRequestCaptured',
          url: requestData.url,
          status: requestData.status,
          method: requestData.method,
          requestId: requestData.id
        }).catch(() => {}); // Ignore if background script not available
      } catch (error) {
        // Ignore messaging errors
      }
      
      debugLog(`Total network requests captured: ${networkRequestsState.requests.length}`);
    }
  });
  
  debugLog('Network request message listener registered');

  // Check if we're on a valid Tableau page
  function isTableauPage() {
    const url = window.location.href;
    const hostname = window.location.hostname;
    
    ContentLogger.debug('Checking if page is Tableau page', {
      url: url,
      hostname: hostname
    });
    
    // Only match actual Tableau server hostnames, not any page with "tableau" in URL
    const isTableau = (
      hostname.includes('tableau.com') ||
      hostname.includes('tableauusercontent.com') ||
      hostname.includes('tableauonline.com') ||
      hostname.includes('tableauserver.com') ||
      hostname.includes('tableau.uberinternal.com') ||
      hostname.includes('online.tableau.com') || // Tableau Cloud domains
      // Check for Tableau DOM elements (more reliable than URL patterns)
      document.querySelector('[data-tb-app]') ||
      document.querySelector('.tb-container') ||
      document.querySelector('#tabZoneContainer') ||
      window.tableau
    );
    
    ContentLogger.info(`Page is ${isTableau ? '' : 'NOT '}a Tableau page`);
    return isTableau;
  }

  // Check if we're on a valid dashboard/workbook page
  function isDashboardPage() {
    const url = window.location.href;
    const hostname = window.location.hostname;
    
    ContentLogger.debug('Checking if page is dashboard page', {
      url: url,
      hostname: hostname
    });
    
    // Check for different dashboard URL patterns
    const isDashboard = (
      // Uber internal Tableau URLs - if /views/ is present, it's a dashboard
      // Site can be explicit (/site/SITENAME/) or implicit (missing = Default site)
      (hostname === 'tableau.uberinternal.com' && url.includes('/views/')) ||
      
      // Tableau Cloud URLs (view mode) - https://prod-in-a.online.tableau.com/#/site/sitename/views/...
      (hostname.includes('online.tableau.com') &&
       (url.includes('/#/site/') && url.includes('/views/'))) ||
      
      // Tableau Cloud URLs (authoring mode) - https://prod-in-a.online.tableau.com/t/sitename/authoring/...
      (hostname.includes('online.tableau.com') &&
       (url.includes('/t/') && url.includes('/authoring/'))) ||
      
      // Generic tableau.com patterns
      (hostname.includes('tableau.com') &&
       (url.includes('/views/') || (url.includes('/t/') && url.includes('/authoring/'))))
    );
    
    ContentLogger.info(`Page is ${isDashboard ? '' : 'NOT '}a valid dashboard page`);
    return isDashboard;
  }

  if (!isTableauPage()) {
    ContentLogger.info('Not a Tableau page, exiting content script');
    debugLog('Not a Tableau page, exiting');
    return;
  }

  if (!isDashboardPage()) {
    ContentLogger.info('Not a dashboard page, showing limited functionality');
    debugLog('Not a dashboard page, showing navigation message');
    // Initialize UI with navigation message
    initializeUI();
    
    // Show navigation message instead of full functionality
    setTimeout(() => {
      if (ui && ui.chatLog) {
        appendMessage('Please navigate to a workbook dashboard to use the Analytics Assistant.', 'bot status');
        appendMessage('The Analytics Assistant works with dashboard URLs like:', 'bot status');
        appendMessage('• tableau.uberinternal.com/#/site/CODS/views/[WorkbookName]/[DashboardName]', 'bot status');
        appendMessage('• prod-in-a.online.tableau.com/#/site/[sitename]/views/[WorkbookName]/[DashboardName]', 'bot status');
        appendMessage('• prod-in-a.online.tableau.com/t/[sitename]/authoring/[WorkbookName]/[DashboardName]', 'bot status');
      }
    }, 1000);
    return;
  }

  ContentLogger.info('Valid dashboard page detected, initializing full assistant');
  debugLog('Valid dashboard page detected, initializing full assistant');

  let flaskConnectionState = {
    initialized: false,
    sessionId: null,
    connected: false,
    workbookName: null,
    dashboardName: null,
    connectionKey: null
  };

  let navigationState = {
    currentPage: 'chart-selection',
    selectedChart: null,
    welcomeShown: false,
    userDataLogged: false
  };

  let chartSelectionState = {
    availableCharts: [],
    selectedChart: null,
    chartsLoaded: false,
    loading: false
  };

  // Create the assistant UI
  function createAssistantUI() {
    debugLog('Creating assistant UI');

    // Main container
    const container = document.createElement('div');
    container.id = 'tableau-analysis-assistant';

    // Launcher button
    const launcher = document.createElement('button');
    launcher.id = 'tableau-analysis-launcher';
    launcher.textContent = 'Analysis Assistant';
    launcher.setAttribute('aria-label', 'Open analysis assistant');

    // Chat interface
    const chatbot = document.createElement('div');
    chatbot.id = 'tableau-analysis-chatbot';
    chatbot.setAttribute('role', 'dialog');
    chatbot.setAttribute('aria-label', 'Chat assistant');
    chatbot.setAttribute('aria-modal', 'false');

    // Chat header
    const chatHeader = document.createElement('div');
    chatHeader.className = 'tableau-chat-header';
    
    const chatTitle = document.createElement('div');
    chatTitle.className = 'tableau-chat-title';
    chatTitle.textContent = 'Analysis Assistant';
    
    const chatActions = document.createElement('div');
    chatActions.className = 'tableau-chat-actions';
    // Initialize feedback system (with error handling)
    let feedbackBtn = null;
    try {
      if (typeof FeedbackManager !== 'undefined') {
        const feedbackManager = new FeedbackManager({
          productName: 'Tableau Analysis Assistant',
          backendUrl: extensionState.backendUrl,
          apiEndpoint: '/api/feedback',
          logger: ContentLogger,
          onSubmitSuccess: (data, response) => {
            ContentLogger.info('Feedback submitted successfully', { data, response });
          },
          onSubmitError: (error, data) => {
            ContentLogger.error('Feedback submission failed', { error: error.message, data });
          }
        });
        
        // Create feedback button
        feedbackBtn = feedbackManager.createFeedbackButton();
        ContentLogger.info('Feedback system initialized successfully');
      } else {
        ContentLogger.warn('FeedbackManager not available, skipping feedback button');
      }
    } catch (error) {
      ContentLogger.error('Failed to initialize feedback system', { error: error.message });
    }
    
    const minBtn = document.createElement('button');
    minBtn.id = 'tableau-min-btn';
    minBtn.textContent = '−';
    minBtn.title = 'Minimize';

    if (feedbackBtn) {
      chatActions.appendChild(feedbackBtn);
    }
    
    chatActions.appendChild(minBtn);
    chatHeader.appendChild(chatTitle);
    chatHeader.appendChild(chatActions);

    // Chat log
    const chatLog = document.createElement('div');
    chatLog.id = 'tableau-chat-log';
    chatLog.className = 'tableau-chat-log';
    chatLog.setAttribute('aria-live', 'polite');

    // Chart buttons container
    const chartButtonsContainer = document.createElement('div');
    chartButtonsContainer.id = 'tableau-chart-buttons-container';
    chartButtonsContainer.style.display = 'none'; // Hidden by default - no longer showing chart selection on open

    // Chat form
    const chatForm = document.createElement('form');
    chatForm.id = 'tableau-chat-form';
    chatForm.className = 'tableau-chat-form';
    chatForm.setAttribute('autocomplete', 'off');

    const chatInput = document.createElement('input');
    chatInput.id = 'tableau-chat-input';
    chatInput.type = 'text';
    chatInput.name = 'message';
    chatInput.placeholder = 'Connecting to backend...';
    chatInput.disabled = true;

    const submitBtn = document.createElement('button');
    submitBtn.type = 'submit';
    submitBtn.textContent = 'Send';
    submitBtn.disabled = true;

    chatForm.appendChild(chatInput);
    chatForm.appendChild(submitBtn);

    // Add input event listener to enable/disable submit button based on text
    chatInput.addEventListener('input', () => {
      const hasText = chatInput.value.trim().length > 0;
      submitBtn.disabled = !hasText || chatInput.disabled;
      if (hasText && !chatInput.disabled) {
        submitBtn.style.background = '#0b72e7';
        submitBtn.style.cursor = 'pointer';
      } else {
        submitBtn.style.background = '#ccc';
        submitBtn.style.cursor = 'not-allowed';
      }
    });

    // Assemble the chat interface
    chatbot.appendChild(chatHeader);
    chatbot.appendChild(chatLog);
    chatbot.appendChild(chartButtonsContainer);
    chatbot.appendChild(chatForm);

    // Assemble the main container
    container.appendChild(launcher);
    container.appendChild(chatbot);

    // Add to page
    document.body.appendChild(container);

    debugLog('Assistant UI created and added to page');

    return {
      container,
      launcher,
      chatbot,
      chatLog,
      chatForm,
      chatInput,
      submitBtn,
      minBtn,
      chartButtonsContainer
    };
  }

  // UI elements (already declared above)

  // Initialize UI
  function initializeUI() {
    ui = createAssistantUI();
    
    // Event listeners
    ui.launcher.addEventListener('click', () => {
      debugLog('Launcher clicked');
      showChat();
    });

    ui.minBtn.addEventListener('click', () => {
      debugLog('Minimize button clicked');
      hideChat();
    });

    ui.chatForm.addEventListener('submit', handleChatSubmit);

    debugLog('UI initialized with event listeners');
  }

  // Chat visibility controls
  function showChat() {
    debugLog('Showing chat interface');
    ui.launcher.style.display = 'none';
    ui.chatbot.classList.add('visible');
    ui.chatInput.focus();

    // Initialize connections when chat is opened
    if (!flaskConnectionState.initialized) {
      initializeFlaskConnection();
    }
    
    // Enable network request capture when assistant opens
    if (!networkRequestsState.captureEnabled) {
      debugLog('Enabling network request capture for 10 seconds...');
      ContentLogger.info('Network request capture enabled', {
        capture_duration_ms: networkRequestsState.captureTimeoutMs
      }, 'NETWORK_CAPTURE_ENABLED');
      
      networkRequestsState.captureEnabled = true;
      networkRequestsState.captureStartTime = new Date().toISOString();
      // Don't clear requests - keep all captured data from page load
      
      // Disable capture after timeout
      setTimeout(() => {
        debugLog('Network request capture timeout reached, disabling capture');
        ContentLogger.info('Network request capture disabled after timeout', {
          total_captured: networkRequestsState.requests.length,
          capture_duration_ms: networkRequestsState.captureTimeoutMs
        }, 'NETWORK_CAPTURE_DISABLED');
        
        networkRequestsState.captureEnabled = false;
      }, networkRequestsState.captureTimeoutMs);
    }
    
    // Extract and log user data on first chat open
    if (!navigationState.userDataLogged) {
      ContentLogger.info('User data extraction scheduled (will run in 2 seconds)', {
        userDataLogged: navigationState.userDataLogged,
        timestamp: new Date().toISOString()
      }, 'USER_DATA_EXTRACTION_SCHEDULED');
      
      setTimeout(async () => {
        try {
          debugLog('Extracting user data from frontend...');
          ContentLogger.info('User data extraction timer triggered - starting extraction now', {}, 'USER_DATA_EXTRACTION_TRIGGERED');
          
          const userData = await extractAllUserData();
          
          ContentLogger.info('User data extracted, now sending to backend', {
            data_size: JSON.stringify(userData).length
          }, 'USER_DATA_EXTRACTED');
          
          const result = await sendUserDataToBackend(userData);
          
          if (result && result.success) {
            navigationState.userDataLogged = true;
            ContentLogger.info('User data logging flow completed successfully', {
              userDataLogged: navigationState.userDataLogged
            }, 'USER_DATA_FLOW_COMPLETE');
          } else {
            ContentLogger.error('User data backend response failed', {
              result: result
            }, 'USER_DATA_BACKEND_FAILED');
          }
        } catch (error) {
          debugLog('Error extracting/sending user data:', error);
          ContentLogger.error('Error in user data extraction/send flow', {
            error: error.message,
            stack: error.stack
          }, 'USER_DATA_FLOW_ERROR');
        }
      }, 2000); // Delay to ensure page is fully loaded
    } else {
      ContentLogger.info('User data already logged, skipping extraction', {
        userDataLogged: navigationState.userDataLogged
      }, 'USER_DATA_ALREADY_LOGGED');
    }
  }

  function hideChat() {
    debugLog('Hiding chat interface');
    ui.chatbot.classList.remove('visible');
    ui.launcher.style.display = 'flex';
  }

  // Enhanced message handling with static chart support
  // 🆕 PHASE 5: Render applied filter information
  function renderAppliedFilters(container, filterInfo) {
    try {
      if (!filterInfo || !filterInfo.filters_applied || !filterInfo.dashboard_filters || filterInfo.dashboard_filters.length === 0) {
        return; // No filters to display
      }

      ContentLogger.debug('[PHASE5] Rendering applied filters', {
        filterCount: filterInfo.filter_count,
        filters: filterInfo.dashboard_filters
      });

      const filterDiv = document.createElement('div');
      filterDiv.className = 'applied-filters-info';
      filterDiv.style.cssText = `
        margin: 12px 0 0 0;
        padding: 10px 14px;
        background: #eff6ff;
        border-left: 3px solid #3b82f6;
        border-radius: 4px;
        font-size: 12px;
        line-height: 1.5;
        color: #1e40af;
      `;

      const filterTitle = document.createElement('div');
      filterTitle.innerHTML = `<strong>🔍 Dashboard Filters Applied:</strong>`;
      filterTitle.style.marginBottom = '6px';
      filterDiv.appendChild(filterTitle);

      const filterList = document.createElement('div');
      filterList.style.cssText = `
        margin-left: 8px;
        font-size: 11px;
        color: #1e3a8a;
      `;

      filterInfo.dashboard_filters.forEach(filter => {
        const filterItem = document.createElement('div');
        filterItem.textContent = `• ${filter}`;
        filterItem.style.marginBottom = '2px';
        filterList.appendChild(filterItem);
      });

      filterDiv.appendChild(filterList);
      container.appendChild(filterDiv);

      ContentLogger.info('[PHASE5] Applied filters rendered successfully');

    } catch (error) {
      ContentLogger.warn('[PHASE5] Error rendering filter info', { error: error.message });
    }
  }

  function appendMessage(text, type, data = null, messageId = null) {
    const div = document.createElement('div');
    div.className = `tableau-msg ${type}`;

    // Add ID if provided for later updates
    if (messageId) {
      div.id = messageId;
    }

    // Handle different message types
    if (type.includes('error')) {
      div.classList.add('error');
    } else if (type.includes('status')) {
      div.classList.add('status');
    }

    // Handle 7-layer interpretive analysis with collapsible sections
    if (data && (data.analysis_type === 'interpretive_multi_metric' || data.analysis_type === 'interpretive')) {
      debugLog('Rendering 7-layer interpretive analysis', data);
      render7LayerAnalysis(div, text, data);
    }
    // Handle SHAP visualizations (tables and graphs)
    else if (data && data.shap_visualizations && data.shap_visualizations.length > 0) {
      debugLog('Rendering SHAP visualizations', data.shap_visualizations);
      renderShapVisualizations(div, text, data);
    }
    // Handle enhanced response with visualization
    else if (data && data.visualization && data.visualization.chart_image) {
      debugLog('Rendering static chart visualization', data.visualization);
      renderStaticVisualization(div, text, data);
    }
    // Handle markdown formatting for status messages and bot responses
    else if ((type.includes('status') || type.includes('bot')) && (text.includes('**') || text.includes('\n'))) {
      // Convert markdown to HTML
      text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
      text = text.replace(/\*(.*?)\*/g, '<em>$1</em>');
      text = text.replace(/\n/g, '<br>');
      text = text.replace(/#{1,6}\s+(.*)/g, '<strong>$1</strong>'); // Headers
      div.innerHTML = text;
    } else {
      div.textContent = text;
    }

    // 🆕 PHASE 5: Add filter information if available
    if (data && type.includes('bot') && !type.includes('error')) {
      const filterInfo = {
        filters_applied: data.filters_applied || false,
        dashboard_filters: data.dashboard_filters || [],
        filter_count: data.filter_count || 0
      };
      renderAppliedFilters(div, filterInfo);
    }

    ui.chatLog.appendChild(div);
    ui.chatLog.scrollTop = ui.chatLog.scrollHeight;

    debugLog(`Message appended: [${type}] ${text}`);
  }

  // Update message by ID for loading indicators
  function updateMessageById(messageId, newText, newType = null) {
    const element = document.getElementById(messageId);
    if (element) {
      // Update class if new type provided
      if (newType) {
        element.className = `tableau-msg ${newType}`;
        if (newType.includes('error')) {
          element.classList.add('error');
        } else if (newType.includes('status')) {
          element.classList.add('status');
        }
      }
      
      // Handle markdown formatting for status messages
      if (element.classList.contains('status') && newText.includes('**')) {
        newText = newText.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        newText = newText.replace(/\n/g, '<br>');
        element.innerHTML = newText;
      } else {
        element.textContent = newText;
      }
      
      ui.chatLog.scrollTop = ui.chatLog.scrollHeight;
      debugLog(`Message updated: [${messageId}] ${newText}`);
    }
  }

  // Render static chart visualization in chat
  function renderStaticVisualization(container, text, responseData) {
    try {
      const visualization = responseData.visualization;
      
      // Create message text container
      const textDiv = document.createElement('div');
      textDiv.className = 'message-text';
      textDiv.innerHTML = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
      container.appendChild(textDiv);
      
      // Create visualization container
      const vizContainer = document.createElement('div');
      vizContainer.className = 'static-chart-container';
      vizContainer.style.cssText = `
        margin: 12px 0;
        background: white;
        border-radius: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        overflow: hidden;
        border: 1px solid #e5e7eb;
      `;
      
      // Create chart title
      if (visualization.title) {
        const titleDiv = document.createElement('div');
        titleDiv.className = 'chart-title';
        titleDiv.textContent = visualization.title;
        titleDiv.style.cssText = `
          padding: 12px 16px 8px;
          font-weight: 600;
          color: #1f2937;
          border-bottom: 1px solid #e5e7eb;
          background: #f9fafb;
          font-size: 14px;
        `;
        vizContainer.appendChild(titleDiv);
      }
      
      // Create static chart image container
      const chartDiv = document.createElement('div');
      chartDiv.className = 'static-chart';
      chartDiv.style.cssText = `
        padding: 16px;
        background: white;
        text-align: center;
      `;
      
      // Create and add the chart image
      const chartImg = document.createElement('img');
      chartImg.src = `data:image/png;base64,${visualization.chart_image}`;
      chartImg.alt = visualization.description || 'Chart visualization';
      chartImg.style.cssText = `
        max-width: 100%;
        height: auto;
        border-radius: 4px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        cursor: pointer;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
      `;
      
      // Add click handler to open modal
      chartImg.onclick = function() {
        openChartModal(visualization.chart_image, visualization.title || 'Chart', visualization.description);
      };
      
      // Add error handling for image loading
      chartImg.onerror = function() {
        chartDiv.innerHTML = '<div style="color: #dc2626; padding: 20px;">Chart image could not be loaded</div>';
      };
      
      // Add hover hint
      const hoverHint = document.createElement('div');
      hoverHint.textContent = 'Click to view full size';
      hoverHint.style.cssText = `
        position: absolute;
        bottom: 8px;
        right: 8px;
        background: rgba(0,0,0,0.7);
        color: white;
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 12px;
        opacity: 0;
        transition: opacity 0.2s ease;
        pointer-events: none;
      `;
      
      // Make chart container relative for absolute positioning
      chartDiv.style.position = 'relative';
      
      // Show hint on hover
      chartImg.onmouseenter = function() {
        this.style.transform = 'scale(1.02)';
        this.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        hoverHint.style.opacity = '1';
      };
      
      chartImg.onmouseleave = function() {
        this.style.transform = 'scale(1)';
        this.style.boxShadow = '0 1px 3px rgba(0,0,0,0.1)';
        hoverHint.style.opacity = '0';
      };
      
      chartDiv.appendChild(chartImg);
      chartDiv.appendChild(hoverHint);
      vizContainer.appendChild(chartDiv);
      
      // Add insights if available
      if (visualization.insights && visualization.insights.length > 0) {
        const insightsDiv = document.createElement('div');
        insightsDiv.className = 'chart-insights';
        insightsDiv.style.cssText = `
          padding: 12px 16px;
          background: #f0f9ff;
          border-top: 1px solid #e0e7ff;
          font-size: 13px;
          color: #1e40af;
          line-height: 1.4;
        `;
        
        const insightsTitle = document.createElement('div');
        insightsTitle.textContent = '💡 Key Insights:';
        insightsTitle.style.fontWeight = '600';
        insightsTitle.style.marginBottom = '8px';
        insightsDiv.appendChild(insightsTitle);
        
        visualization.insights.forEach(insight => {
          const insightItem = document.createElement('div');
          insightItem.textContent = `• ${insight}`;
          insightItem.style.marginBottom = '4px';
          insightsDiv.appendChild(insightItem);
        });
        
        vizContainer.appendChild(insightsDiv);
      }
      
      container.appendChild(vizContainer);
      
      // Add suggested actions if available
      if (responseData.suggested_actions && responseData.suggested_actions.length > 0) {
        addSuggestedActions(container, responseData.suggested_actions);
      }
      
      debugLog('Static chart visualization rendered successfully');
      
    } catch (error) {
      debugLog('Error rendering static chart visualization:', error);
      
      // Fallback to text message
      const errorDiv = document.createElement('div');
      errorDiv.textContent = text + ' (Visualization could not be rendered)';
      errorDiv.style.color = '#dc2626';
      container.appendChild(errorDiv);
    }
  }

  // Render SHAP visualizations (tables and graphs)
  
  // Render 7-layer interpretive analysis with collapsible sections
  function render7LayerAnalysis(container, text, responseData) {
    try {
      debugLog('Rendering 7-layer analysis', responseData);
      
      const analysisType = responseData.analysis_type;
      
      if (analysisType === 'interpretive_multi_metric') {
        // Multi-metric: render each metric's analysis separately
        const analyses = responseData.seven_layer_analyses || [];
        
        for (const analysis of analyses) {
          // Metric header
          const metricHeader = document.createElement('h3');
          metricHeader.style.cssText = `
            margin: 16px 0 8px 0;
            font-size: 16px;
            font-weight: 600;
            color: #1e293b;
            border-bottom: 2px solid #0b72e7;
            padding-bottom: 6px;
          `;
          metricHeader.textContent = analysis.metric;
          container.appendChild(metricHeader);
          
          // Executive summary (always visible)
          const summaryDiv = document.createElement('div');
          summaryDiv.style.cssText = `
            margin: 12px 0;
            padding: 12px;
            background: #f8fafc;
            border-left: 4px solid #0b72e7;
            border-radius: 4px;
            font-size: 14px;
            line-height: 1.6;
            color: #334155;
          `;
          summaryDiv.innerHTML = analysis.executive_summary.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
          container.appendChild(summaryDiv);
          
          // Collapsible sections
          renderCollapsibleSections(container, analysis.sections);
        }
      } else if (analysisType === 'interpretive') {
        // Single metric: legacy format
        const sections = responseData.seven_layer_sections || [];
        
        // Executive summary from text
        const summaryDiv = document.createElement('div');
        summaryDiv.style.cssText = `
          margin: 12px 0;
          padding: 12px;
          background: #f8fafc;
          border-left: 4px solid #0b72e7;
          border-radius: 4px;
          font-size: 14px;
          line-height: 1.6;
          color: #334155;
        `;
        summaryDiv.innerHTML = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
        container.appendChild(summaryDiv);
        
        // Collapsible sections
        renderCollapsibleSections(container, sections);
      }
    } catch (error) {
      debugLog('Error rendering 7-layer analysis:', error);
      // Fallback to plain text
      container.innerHTML = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
    }
  }
  
  // Helper function to render sections as navigation buttons
  function renderCollapsibleSections(container, sections) {
    if (!sections || sections.length === 0) return;
    
    // Create wrapper for Deep Dive button and sections
    const deepDiveWrapper = document.createElement('div');
    deepDiveWrapper.style.cssText = `
      margin-top: 12px;
    `;
    
    // Create Deep Dive toggle button
    const deepDiveButton = document.createElement('button');
    deepDiveButton.type = 'button';
    deepDiveButton.className = 'deep-dive-toggle-button';
    deepDiveButton.style.cssText = `
      width: 100%;
      padding: 12px 16px;
      background: linear-gradient(135deg, #0b72e7 0%, #0958b8 100%);
      border: none;
      border-radius: 6px;
      text-align: left;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 14px;
      font-weight: 600;
      color: white;
      transition: all 0.2s ease;
      box-shadow: 0 2px 4px rgba(11, 114, 231, 0.2);
    `;
    
    // Store collapsed state
    let isExpanded = false;
    
    // Function to update button appearance
    const updateButtonAppearance = () => {
      const chevron = isExpanded ? '▲' : '▼';
      deepDiveButton.innerHTML = `
        <span style="display: flex; align-items: center; gap: 8px;">
          <span>🔍</span>
          <span>Deep Dive</span>
        </span>
        <span style="font-size: 16px; transition: transform 0.2s ease;">${chevron}</span>
      `;
    };
    
    updateButtonAppearance();
    
    // Hover effect for Deep Dive button
    deepDiveButton.addEventListener('mouseenter', function() {
      this.style.background = 'linear-gradient(135deg, #0958b8 0%, #073d80 100%)';
      this.style.boxShadow = '0 4px 8px rgba(11, 114, 231, 0.3)';
    });
    
    deepDiveButton.addEventListener('mouseleave', function() {
      this.style.background = 'linear-gradient(135deg, #0b72e7 0%, #0958b8 100%)';
      this.style.boxShadow = '0 2px 4px rgba(11, 114, 231, 0.2)';
    });
    
    // Create sections container (initially hidden)
    const sectionsContainer = document.createElement('div');
    sectionsContainer.style.cssText = `
      margin-top: 8px;
      display: none;
      flex-direction: column;
      gap: 8px;
      overflow: hidden;
      transition: all 0.3s ease;
    `;
    
    sections.forEach(section => {
      // Create section button
      const sectionButton = document.createElement('button');
      sectionButton.type = 'button';
      sectionButton.className = 'seven-layer-navigation-button';
      sectionButton.style.cssText = `
        width: 100%;
        padding: 12px 16px;
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        text-align: left;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: space-between;
        font-size: 13px;
        font-weight: 500;
        color: #475569;
        transition: all 0.2s ease;
      `;
      sectionButton.innerHTML = `
        <span>${section.title}</span>
        <span style="color: #94a3b8; font-size: 16px;">→</span>
      `;
      
      // Hover effect
      sectionButton.addEventListener('mouseenter', function() {
        this.style.background = '#f8fafc';
        this.style.borderColor = '#cbd5e1';
      });
      
      sectionButton.addEventListener('mouseleave', function() {
        this.style.background = 'white';
        this.style.borderColor = '#e2e8f0';
      });
      
      // Click handler - navigate to detail view
      sectionButton.addEventListener('click', function() {
        showSectionDetailView(container, section, sections);
      });
      
      sectionsContainer.appendChild(sectionButton);
    });
    
    // Toggle functionality for Deep Dive button
    deepDiveButton.addEventListener('click', function() {
      isExpanded = !isExpanded;
      
      if (isExpanded) {
        // Expand - show sections
        sectionsContainer.style.display = 'flex';
      } else {
        // Collapse - hide sections
        sectionsContainer.style.display = 'none';
      }
      
      updateButtonAppearance();
    });
    
    // Append elements
    deepDiveWrapper.appendChild(deepDiveButton);
    deepDiveWrapper.appendChild(sectionsContainer);
    container.appendChild(deepDiveWrapper);
  }
  
  // Show section detail view
  function showSectionDetailView(parentContainer, section, allSections) {
    // Close any existing detail views before opening a new one
    const existingDetailViews = document.querySelectorAll('.seven-layer-detail-view');
    existingDetailViews.forEach(view => view.remove());
    
    // Create detail view overlay
    const detailView = document.createElement('div');
    detailView.className = 'seven-layer-detail-view';
    detailView.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.5);
      z-index: 2147483649;
      display: flex;
      align-items: center;
      justify-content: center;
      animation: fadeIn 0.2s ease-out;
    `;
    
    // Create detail content container
    const detailContent = document.createElement('div');
    detailContent.className = 'seven-layer-detail-content';
    detailContent.style.cssText = `
      background: white;
      border-radius: 8px;
      width: 90%;
      max-width: 900px;
      max-height: 85vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
      animation: slideUp 0.3s ease-out;
    `;
    
    // Header with back button
    const header = document.createElement('div');
    header.style.cssText = `
      padding: 16px 20px;
      border-bottom: 1px solid #e2e8f0;
      display: flex;
      align-items: center;
      gap: 12px;
      background: #f8fafc;
      border-radius: 8px 8px 0 0;
    `;
    
    const backButton = document.createElement('button');
    backButton.type = 'button';
    backButton.style.cssText = `
      background: white;
      border: 1px solid #e2e8f0;
      border-radius: 6px;
      padding: 8px 12px;
      cursor: pointer;
      font-size: 14px;
      color: #475569;
      display: flex;
      align-items: center;
      gap: 6px;
      font-weight: 500;
      transition: all 0.2s ease;
    `;
    backButton.innerHTML = `<span>←</span><span>Close</span>`;
    
    backButton.addEventListener('mouseenter', function() {
      this.style.background = '#f1f5f9';
      this.style.borderColor = '#cbd5e1';
    });
    
    backButton.addEventListener('mouseleave', function() {
      this.style.background = 'white';
      this.style.borderColor = '#e2e8f0';
    });
    
    backButton.addEventListener('click', function() {
      detailView.style.animation = 'fadeOut 0.2s ease-out';
      setTimeout(() => detailView.remove(), 200);
    });
    
    const title = document.createElement('h3');
    title.textContent = section.title;
    title.style.cssText = `
      margin: 0;
      font-size: 16px;
      font-weight: 600;
      color: #1e293b;
      flex: 1;
    `;
    
    header.appendChild(backButton);
    header.appendChild(title);
    
    // Content area with scrolling
    const contentArea = document.createElement('div');
    contentArea.style.cssText = `
      padding: 20px;
      overflow-y: auto;
      flex: 1;
    `;
    
    // Add description if available
    if (section.description) {
      const descriptionDiv = document.createElement('div');
      descriptionDiv.style.cssText = `
        padding: 12px 16px;
        margin-bottom: 16px;
        background: #f0f9ff;
        border-left: 3px solid #0b72e7;
        border-radius: 4px;
        font-size: 13px;
        color: #475569;
        font-style: italic;
      `;
      descriptionDiv.textContent = section.description;
      contentArea.appendChild(descriptionDiv);
    }
    
    // Check if content contains tables and wrap them for horizontal scrolling
    const contentWrapper = document.createElement('div');
    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = section.content.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
    
    // Check if there are tables in the content
    const tables = tempDiv.querySelectorAll('table');
    if (tables.length > 0) {
      // Content has tables - wrap each table in a scrollable container
      const children = Array.from(tempDiv.childNodes);
      children.forEach(child => {
        if (child.nodeType === 1 && (child.tagName === 'TABLE' || child.querySelector('table'))) {
          // This is a table or contains a table - wrap it
          const tableWrapper = document.createElement('div');
          tableWrapper.style.cssText = `
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            margin: 12px 0;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
          `;
          tableWrapper.appendChild(child.cloneNode(true));
          contentWrapper.appendChild(tableWrapper);
        } else {
          // Regular content - add as is
          contentWrapper.appendChild(child.cloneNode(true));
        }
      });
    } else {
      // No tables - add content directly
      contentWrapper.innerHTML = tempDiv.innerHTML;
    }
    
    contentWrapper.style.cssText = `
      font-size: 14px;
      line-height: 1.6;
      color: #475569;
    `;
    
    contentArea.appendChild(contentWrapper);
    
    // Navigation footer (optional - for next/previous sections)
    const footer = document.createElement('div');
    footer.style.cssText = `
      padding: 16px 20px;
      border-top: 1px solid #e2e8f0;
      display: flex;
      justify-content: space-between;
      background: #f8fafc;
      border-radius: 0 0 8px 8px;
    `;
    
    // Find current section index
    const currentIndex = allSections.findIndex(s => s.title === section.title);
    const hasPrevious = currentIndex > 0;
    const hasNext = currentIndex < allSections.length - 1;
    
    if (hasPrevious) {
      const prevButton = document.createElement('button');
      prevButton.type = 'button';
      prevButton.style.cssText = `
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        padding: 8px 16px;
        cursor: pointer;
        font-size: 13px;
        color: #475569;
        font-weight: 500;
        transition: all 0.2s ease;
      `;
      prevButton.innerHTML = '← Previous';
      prevButton.addEventListener('mouseenter', function() {
        this.style.background = '#f1f5f9';
      });
      prevButton.addEventListener('mouseleave', function() {
        this.style.background = 'white';
      });
      prevButton.addEventListener('click', function() {
        detailView.remove();
        showSectionDetailView(parentContainer, allSections[currentIndex - 1], allSections);
      });
      footer.appendChild(prevButton);
    } else {
      footer.appendChild(document.createElement('div'));
    }
    
    if (hasNext) {
      const nextButton = document.createElement('button');
      nextButton.type = 'button';
      nextButton.style.cssText = `
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        padding: 8px 16px;
        cursor: pointer;
        font-size: 13px;
        color: #475569;
        font-weight: 500;
        transition: all 0.2s ease;
      `;
      nextButton.innerHTML = 'Next →';
      nextButton.addEventListener('mouseenter', function() {
        this.style.background = '#f1f5f9';
      });
      nextButton.addEventListener('mouseleave', function() {
        this.style.background = 'white';
      });
      nextButton.addEventListener('click', function() {
        detailView.remove();
        showSectionDetailView(parentContainer, allSections[currentIndex + 1], allSections);
      });
      footer.appendChild(nextButton);
    } else {
      footer.appendChild(document.createElement('div'));
    }
    
    // Assemble detail view
    detailContent.appendChild(header);
    detailContent.appendChild(contentArea);
    detailContent.appendChild(footer);
    detailView.appendChild(detailContent);
    
    // Close on overlay click
    detailView.addEventListener('click', function(e) {
      if (e.target === detailView) {
        detailView.style.animation = 'fadeOut 0.2s ease-out';
        setTimeout(() => detailView.remove(), 200);
      }
    });
    
    // Add to document
    document.body.appendChild(detailView);
  }

  function renderShapVisualizations(container, text, responseData) {
    try {
      const shapViz = responseData.shap_visualizations;
      
      // Create message text container
      const textDiv = document.createElement('div');
      textDiv.className = 'message-text';
      textDiv.innerHTML = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
      container.appendChild(textDiv);
      
      // Loop through each period/transition
      shapViz.forEach((periodData, periodIndex) => {
        // Create period container
        const periodContainer = document.createElement('div');
        periodContainer.className = 'shap-period-container';
        periodContainer.style.cssText = `
          margin: 16px 0;
          background: white;
          border-radius: 8px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.1);
          overflow: hidden;
          border: 1px solid #e5e7eb;
        `;
        
        // Create period header
        const periodHeader = document.createElement('div');
        periodHeader.className = 'shap-period-header';
        periodHeader.innerHTML = `<strong>${periodData.period_label}</strong> (${periodData.sentiment})`;
        periodHeader.style.cssText = `
          padding: 12px 16px;
          font-weight: 600;
          color: #1f2937;
          background: #f9fafb;
          border-bottom: 1px solid #e5e7eb;
          font-size: 14px;
        `;
        periodContainer.appendChild(periodHeader);
        
        // SECTION 1: Current Period/Transition Analysis (all features with graph toggles)
        const currentSectionContainer = document.createElement('div');
        currentSectionContainer.className = 'shap-analysis-section';
        currentSectionContainer.style.cssText = 'border-bottom: 2px solid #e5e7eb;';
        
        const currentSectionTitle = document.createElement('div');
        currentSectionTitle.textContent = '📊 Top Impactful Features';
        currentSectionTitle.style.cssText = `
          padding: 12px 16px;
          font-weight: 600;
          color: #1f2937;
          background: #fefce8;
          font-size: 14px;
        `;
        currentSectionContainer.appendChild(currentSectionTitle);
        
        periodData.features.forEach((featureData, featureIndex) => {
          if (featureData.current_table_html) {
            const featureDiv = document.createElement('div');
            featureDiv.style.cssText = 'padding: 16px; border-bottom: 1px solid #f0f0f0;';
            
            const featureTitle = document.createElement('h4');
            featureTitle.textContent = featureData.feature_name;
            featureTitle.style.cssText = 'margin: 0 0 12px 0; font-size: 15px; font-weight: 600; color: #374151;';
            featureDiv.appendChild(featureTitle);
            
            // Add one-line summary if available
            if (featureData.summary && featureData.summary.trim() !== '') {
              const summaryDiv = document.createElement('div');
              summaryDiv.textContent = featureData.summary;
              summaryDiv.style.cssText = `
                margin: 0 0 12px 0;
                font-size: 13px;
                font-style: italic;
                color: #6b7280;
                line-height: 1.5;
                padding: 8px 12px;
                background: #f9fafb;
                border-left: 3px solid #3b82f6;
                border-radius: 4px;
              `;
              featureDiv.appendChild(summaryDiv);
            }
            
            const tableDiv = document.createElement('div');
            tableDiv.innerHTML = featureData.current_table_html;
            tableDiv.style.cssText = 'margin-bottom: 12px;';
            featureDiv.appendChild(tableDiv);
            
            // Add graph button if graph exists - opens directly in popup
            if (featureData.current_graph_base64) {
              const graphBtn = document.createElement('button');
              graphBtn.innerHTML = 'View Graph';
              graphBtn.style.cssText = `
                background: #3b82f6;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 13px;
                font-weight: 500;
                margin-bottom: 12px;
                transition: background 0.2s;
              `;
              graphBtn.onmouseover = () => graphBtn.style.background = '#2563eb';
              graphBtn.onmouseout = () => graphBtn.style.background = '#3b82f6';
              
              graphBtn.onclick = () => openChartModal(featureData.current_graph_base64.split(',')[1], `${featureData.feature_name} - Top Contributors`, '');
              
              featureDiv.appendChild(graphBtn);
            }
            
            currentSectionContainer.appendChild(featureDiv);
          }
        });
        periodContainer.appendChild(currentSectionContainer);
        
        // Check if temporal sections have data
        const hasMomData = periodData.features.some(f => f.mom_table_html && f.mom_table_html.trim() !== "");
        const hasQoqData = periodData.features.some(f => f.qoq_table_html && f.qoq_table_html.trim() !== "");
        const hasYoyData = periodData.features.some(f => f.yoy_table_html && f.yoy_table_html.trim() !== "");
        
        // Only show temporal comparison buttons if any data exists
        if (hasMomData || hasQoqData || hasYoyData) {
          // Create temporal buttons container
          const temporalButtonsContainer = document.createElement('div');
          temporalButtonsContainer.style.cssText = `
            padding: 12px 16px;
            background: #f9fafb;
            display: flex;
            gap: 8px;
            flex-wrap: nowrap;
          `;
          
          // Create MoM button if data exists
          if (hasMomData) {
            const momBtn = document.createElement('button');
            momBtn.innerHTML = 'MoM';
            momBtn.style.cssText = `
              background: #dbeafe;
              color: #1e40af;
              border: 1px solid #93c5fd;
              padding: 8px 8px;
              border-radius: 6px;
              cursor: pointer;
              font-size: 12px;
              font-weight: 500;
              transition: background 0.2s;
              flex: 1;
              min-width: 0;
              white-space: nowrap;
              text-align: center;
              overflow: hidden;
              text-overflow: ellipsis;
            `;
            momBtn.onmouseover = () => momBtn.style.background = '#bfdbfe';
            momBtn.onmouseout = () => momBtn.style.background = '#dbeafe';
            momBtn.onclick = () => openTemporalComparisonPopup('mom', periodData.period_label, periodData.features);
            
            temporalButtonsContainer.appendChild(momBtn);
          }
          
          // Create QoQ button if data exists
          if (hasQoqData) {
            const qoqBtn = document.createElement('button');
            qoqBtn.innerHTML = 'QoQ';
            qoqBtn.style.cssText = `
              background: #dcfce7;
              color: #166534;
              border: 1px solid #86efac;
              padding: 8px 8px;
              border-radius: 6px;
              cursor: pointer;
              font-size: 12px;
              font-weight: 500;
              transition: background 0.2s;
              flex: 1;
              min-width: 0;
              white-space: nowrap;
              text-align: center;
              overflow: hidden;
              text-overflow: ellipsis;
            `;
            qoqBtn.onmouseover = () => qoqBtn.style.background = '#bbf7d0';
            qoqBtn.onmouseout = () => qoqBtn.style.background = '#dcfce7';
            qoqBtn.onclick = () => openTemporalComparisonPopup('qoq', periodData.period_label, periodData.features);
            
            temporalButtonsContainer.appendChild(qoqBtn);
          }
          
          // Create YoY button if data exists
          if (hasYoyData) {
            const yoyBtn = document.createElement('button');
            yoyBtn.innerHTML = 'YoY';
            yoyBtn.style.cssText = `
              background: #fce7f3;
              color: #9f1239;
              border: 1px solid #fbcfe8;
              padding: 8px 8px;
              border-radius: 6px;
              cursor: pointer;
              font-size: 12px;
              font-weight: 500;
              transition: background 0.2s;
              flex: 1;
              min-width: 0;
              white-space: nowrap;
              text-align: center;
              overflow: hidden;
              text-overflow: ellipsis;
            `;
            yoyBtn.onmouseover = () => yoyBtn.style.background = '#fbcfe8';
            yoyBtn.onmouseout = () => yoyBtn.style.background = '#fce7f3';
            yoyBtn.onclick = () => openTemporalComparisonPopup('yoy', periodData.period_label, periodData.features);
            
            temporalButtonsContainer.appendChild(yoyBtn);
          }
          
          periodContainer.appendChild(temporalButtonsContainer);
        }
        
        container.appendChild(periodContainer);
      });
      
      debugLog('SHAP visualizations rendered successfully');
      
    } catch (error) {
      debugLog('Error rendering SHAP visualizations:', error);
      
      // Fallback to text message
      const errorDiv = document.createElement('div');
      errorDiv.textContent = text + ' (Visualizations could not be rendered)';
      errorDiv.style.color = '#dc2626';
      container.appendChild(errorDiv);
    }
  }

  // Chart Modal Functions
  function openChartModal(base64Image, title, description) {
    try {
      // Remove existing modal if any
      const existingModal = document.getElementById('tableauChartModal');
      if (existingModal) {
        existingModal.remove();
      }
      
      // Create modal structure
      const modal = document.createElement('div');
      modal.id = 'tableauChartModal';
      modal.className = 'tableau-chart-modal';
      modal.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.8);
        display: flex;
        justify-content: center;
        align-items: center;
        z-index: 10000;
        backdrop-filter: blur(4px);
      `;
      
      const modalContent = document.createElement('div');
      modalContent.className = 'tableau-chart-modal-content';
      modalContent.style.cssText = `
        background: white;
        border-radius: 12px;
        box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
        max-width: 90vw;
        max-height: 90vh;
        overflow: hidden;
        position: relative;
      `;
      
      // Modal header
      const modalHeader = document.createElement('div');
      modalHeader.className = 'tableau-chart-modal-header';
      modalHeader.style.cssText = `
        padding: 16px 20px;
        border-bottom: 1px solid #e5e7eb;
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: #f9fafb;
      `;
      
      const modalTitle = document.createElement('h3');
      modalTitle.textContent = title;
      modalTitle.style.cssText = `
        margin: 0;
        font-size: 18px;
        font-weight: 600;
        color: #1f2937;
      `;
      
      const modalActions = document.createElement('div');
      modalActions.style.cssText = `
        display: flex;
        gap: 8px;
        align-items: center;
      `;
      
      // Download button
      const downloadBtn = document.createElement('button');
      downloadBtn.innerHTML = '📥 Download';
      downloadBtn.style.cssText = `
        background: #3b82f6;
        color: white;
        border: none;
        padding: 8px 16px;
        border-radius: 6px;
        font-size: 14px;
        cursor: pointer;
        transition: background 0.2s ease;
      `;
      downloadBtn.onmouseover = () => downloadBtn.style.background = '#2563eb';
      downloadBtn.onmouseout = () => downloadBtn.style.background = '#3b82f6';
      downloadBtn.onclick = () => downloadChartFromModal(base64Image, title);
      
      // Close button
      const closeBtn = document.createElement('button');
      closeBtn.innerHTML = '✕';
      closeBtn.style.cssText = `
        background: #f3f4f6;
        color: #6b7280;
        border: none;
        padding: 8px 12px;
        border-radius: 6px;
        font-size: 16px;
        cursor: pointer;
        transition: background 0.2s ease;
      `;
      closeBtn.onmouseover = () => closeBtn.style.background = '#e5e7eb';
      closeBtn.onmouseout = () => closeBtn.style.background = '#f3f4f6';
      closeBtn.onclick = closeChartModal;
      
      modalActions.appendChild(downloadBtn);
      modalActions.appendChild(closeBtn);
      modalHeader.appendChild(modalTitle);
      modalHeader.appendChild(modalActions);
      
      // Modal body with image
      const modalBody = document.createElement('div');
      modalBody.style.cssText = `
        padding: 20px;
        text-align: center;
        max-height: calc(90vh - 120px);
        overflow: auto;
      `;
      
      const fullSizeImg = document.createElement('img');
      fullSizeImg.src = `data:image/png;base64,${base64Image}`;
      fullSizeImg.alt = description || 'Full size chart';
      fullSizeImg.style.cssText = `
        max-width: 100%;
        height: auto;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
      `;
      
      modalBody.appendChild(fullSizeImg);
      modalContent.appendChild(modalHeader);
      modalContent.appendChild(modalBody);
      modal.appendChild(modalContent);
      
      // Add to document
      document.body.appendChild(modal);
      
      // Event listeners
      modal.onclick = (e) => {
        if (e.target === modal) closeChartModal();
      };
      
      document.addEventListener('keydown', handleModalKeyPress);
      
      debugLog('Chart modal opened successfully');
      
    } catch (error) {
      debugLog('Error opening chart modal:', error);
    }
  }
  
  function closeChartModal() {
    const modal = document.getElementById('tableauChartModal');
    if (modal) {
      modal.remove();
      document.removeEventListener('keydown', handleModalKeyPress);
      debugLog('Chart modal closed');
    }
  }
  
  function handleModalKeyPress(e) {
    if (e.key === 'Escape') {
      closeChartModal();
    }
  }
  
  function downloadChartFromModal(base64Image, title) {
    try {
      // Convert base64 to blob
      const byteCharacters = atob(base64Image);
      const byteNumbers = new Array(byteCharacters.length);
      for (let i = 0; i < byteCharacters.length; i++) {
        byteNumbers[i] = byteCharacters.charCodeAt(i);
      }
      const byteArray = new Uint8Array(byteNumbers);
      const blob = new Blob([byteArray], { type: 'image/png' });
      
      // Create download link
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      
      // Generate filename with timestamp
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const filename = `TableauChart-${title.replace(/[^a-zA-Z0-9]/g, '')}-${timestamp}.png`;
      
      link.href = url;
      link.download = filename;
      link.style.display = 'none';
      
      // Trigger download
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      
      // Clean up
      URL.revokeObjectURL(url);
      
      debugLog(`Chart downloaded as: ${filename}`);
      
    } catch (error) {
      debugLog('Error downloading chart:', error);
      alert('Error downloading chart. Please try again.');
    }
  }

  // Open temporal comparison popup (MoM/QoQ/YoY)
  function openTemporalComparisonPopup(type, periodLabel, features) {
    try {
      // Remove existing modal if any
      const existingModal = document.getElementById('tableauTemporalModal');
      if (existingModal) {
        existingModal.remove();
      }
      
      // Determine title and background color based on type
      let title, titleBg;
      if (type === 'mom') {
        title = '📈 Month-over-Month Changes';
        titleBg = '#dbeafe';
      } else if (type === 'qoq') {
        title = '📊 Quarter-over-Quarter Changes';
        titleBg = '#dcfce7';
      } else if (type === 'yoy') {
        title = '📈 Year-over-Year Changes';
        titleBg = '#fce7f3';
      }
      
      // Create modal structure
      const modal = document.createElement('div');
      modal.id = 'tableauTemporalModal';
      modal.className = 'tableau-temporal-modal';
      modal.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.8);
        display: flex;
        justify-content: center;
        align-items: center;
        z-index: 2147483648;
        backdrop-filter: blur(4px);
      `;
      
      const modalContent = document.createElement('div');
      modalContent.className = 'tableau-temporal-modal-content';
      modalContent.style.cssText = `
        background: white;
        border-radius: 12px;
        box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
        max-width: 90vw;
        width: 800px;
        max-height: 90vh;
        overflow: hidden;
        position: relative;
        display: flex;
        flex-direction: column;
      `;
      
      // Modal header
      const modalHeader = document.createElement('div');
      modalHeader.className = 'tableau-temporal-modal-header';
      modalHeader.style.cssText = `
        padding: 16px 20px;
        border-bottom: 1px solid #e5e7eb;
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: ${titleBg};
      `;
      
      const modalTitle = document.createElement('h3');
      modalTitle.textContent = `${title} - ${periodLabel}`;
      modalTitle.style.cssText = `
        margin: 0;
        font-size: 18px;
        font-weight: 600;
        color: #1f2937;
      `;
      
      // Close button
      const closeBtn = document.createElement('button');
      closeBtn.innerHTML = '✕';
      closeBtn.style.cssText = `
        background: #f3f4f6;
        color: #6b7280;
        border: none;
        padding: 8px 12px;
        border-radius: 6px;
        font-size: 16px;
        cursor: pointer;
        transition: background 0.2s ease;
      `;
      closeBtn.onmouseover = () => closeBtn.style.background = '#e5e7eb';
      closeBtn.onmouseout = () => closeBtn.style.background = '#f3f4f6';
      closeBtn.onclick = closeTemporalModal;
      
      modalHeader.appendChild(modalTitle);
      modalHeader.appendChild(closeBtn);
      
      // Modal body with scrollable content
      const modalBody = document.createElement('div');
      modalBody.style.cssText = `
        padding: 20px;
        overflow-y: auto;
        flex: 1;
      `;
      
      // Add each feature
      features.forEach((featureData, index) => {
        let tableHtml, graphBase64;
        
        // Get the appropriate data based on type
        if (type === 'mom') {
          tableHtml = featureData.mom_table_html;
          graphBase64 = featureData.mom_graph_base64;
        } else if (type === 'qoq') {
          tableHtml = featureData.qoq_table_html;
          graphBase64 = featureData.qoq_graph_base64;
        } else if (type === 'yoy') {
          tableHtml = featureData.yoy_table_html;
          graphBase64 = featureData.yoy_graph_base64;
        }
        
        // Only show if data exists
        if (tableHtml && tableHtml.trim() !== "") {
          const featureDiv = document.createElement('div');
          featureDiv.style.cssText = `
            margin-bottom: 24px;
            padding-bottom: 24px;
            border-bottom: ${index < features.length - 1 ? '2px solid #e5e7eb' : 'none'};
          `;
          
          const featureTitle = document.createElement('h4');
          featureTitle.textContent = featureData.feature_name;
          featureTitle.style.cssText = 'margin: 0 0 12px 0; font-size: 16px; font-weight: 600; color: #374151;';
          featureDiv.appendChild(featureTitle);
          
          const tableDiv = document.createElement('div');
          tableDiv.innerHTML = tableHtml;
          tableDiv.style.cssText = 'margin-bottom: 12px;';
          featureDiv.appendChild(tableDiv);
          
          // Add graph button if graph exists
          if (graphBase64) {
            const graphBtn = document.createElement('button');
            graphBtn.innerHTML = 'View Graph';
            graphBtn.style.cssText = `
              background: #3b82f6;
              color: white;
              border: none;
              padding: 8px 16px;
              border-radius: 6px;
              cursor: pointer;
              font-size: 13px;
              font-weight: 500;
              margin-bottom: 12px;
              transition: background 0.2s;
            `;
            graphBtn.onmouseover = () => graphBtn.style.background = '#2563eb';
            graphBtn.onmouseout = () => graphBtn.style.background = '#3b82f6';
            
            // Create graph container (initially hidden)
            const graphContainer = document.createElement('div');
            graphContainer.style.cssText = 'display: none; margin-top: 12px;';
            
            const graph = document.createElement('img');
            graph.src = graphBase64;
            graph.alt = `${featureData.feature_name} - ${title}`;
            graph.style.cssText = 'max-width: 100%; height: auto; border-radius: 8px; cursor: pointer;';
            
            // Click on graph opens full-screen modal
            graph.onclick = () => {
              openChartModal(graphBase64.split(',')[1], `${featureData.feature_name} - ${title}`, '');
            };
            
            graphContainer.appendChild(graph);
            
            // Toggle graph visibility in modal
            graphBtn.onclick = () => {
              if (graphContainer.style.display === 'none') {
                graphContainer.style.display = 'block';
                graphBtn.innerHTML = 'Hide Graph';
              } else {
                graphContainer.style.display = 'none';
                graphBtn.innerHTML = 'View Graph';
              }
            };
            
            featureDiv.appendChild(graphBtn);
            featureDiv.appendChild(graphContainer);
          }
          
          modalBody.appendChild(featureDiv);
        }
      });
      
      modalContent.appendChild(modalHeader);
      modalContent.appendChild(modalBody);
      modal.appendChild(modalContent);
      
      // Add to document
      document.body.appendChild(modal);
      
      // Event listeners
      modal.onclick = (e) => {
        if (e.target === modal) closeTemporalModal();
      };
      
      document.addEventListener('keydown', handleTemporalModalKeyPress);
      
      debugLog(`Temporal comparison modal opened: ${type}`);
      
    } catch (error) {
      debugLog('Error opening temporal comparison modal:', error);
    }
  }
  
  function closeTemporalModal() {
    const modal = document.getElementById('tableauTemporalModal');
    if (modal) {
      modal.remove();
      document.removeEventListener('keydown', handleTemporalModalKeyPress);
      debugLog('Temporal comparison modal closed');
    }
  }
  
  function handleTemporalModalKeyPress(e) {
    if (e.key === 'Escape') {
      closeTemporalModal();
    }
  }

  // Add suggested actions buttons
  function addSuggestedActions(container, actions) {
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'suggested-actions';
    actionsDiv.style.cssText = `
      margin: 12px 0 0;
      padding: 12px;
      background: #f8fafc;
      border-radius: 6px;
      border: 1px solid #e2e8f0;
    `;
    
    const actionsTitle = document.createElement('div');
    actionsTitle.textContent = '💭 Try asking:';
    actionsTitle.style.cssText = `
      font-size: 13px;
      font-weight: 600;
      color: #475569;
      margin-bottom: 8px;
    `;
    actionsDiv.appendChild(actionsTitle);
    
    const buttonsContainer = document.createElement('div');
    buttonsContainer.style.cssText = `
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    `;
    
    actions.forEach(action => {
      const button = document.createElement('button');
      button.textContent = action;
      button.className = 'suggested-action-btn';
      button.style.cssText = `
        background: white;
        border: 1px solid #cbd5e1;
        border-radius: 4px;
        padding: 6px 12px;
        font-size: 12px;
        color: #475569;
        cursor: pointer;
        transition: all 0.2s;
      `;
      
      button.addEventListener('mouseenter', () => {
        button.style.background = '#f1f5f9';
        button.style.borderColor = '#94a3b8';
      });
      
      button.addEventListener('mouseleave', () => {
        button.style.background = 'white';
        button.style.borderColor = '#cbd5e1';
      });
      
      button.addEventListener('click', () => {
        if (ui.chatInput) {
          ui.chatInput.value = action;
          ui.chatInput.focus();
        }
      });
      
      buttonsContainer.appendChild(button);
    });
    
    actionsDiv.appendChild(buttonsContainer);
    container.appendChild(actionsDiv);
  }

  // Flask backend communication
  async function initializeFlaskConnection() {
    debugLog('Starting Flask backend connection initialization...');
    
    if (flaskConnectionState.initialized) {
      debugLog('Flask connection already initialized');
      return flaskConnectionState.connected;
    }

    try {
      debugLog('Initializing Flask backend connection...');
      
      // Create loading indicator for initialization
      const initLoadingId = `init-loading-${Date.now()}`;
      appendMessage('🔄 Connecting to analytics backend...', 'bot status', null, initLoadingId);

      // Extract Tableau context
      const tableauContext = extractTableauContext();
      
      // Update status
      updateMessageById(initLoadingId, '🔄 Extracting workbook information...');
      
      debugLog('Sending initialization request to Flask backend...');
      debugLogNetwork('POST request to /api/tableau/initialize', {
        url: `${extensionState.backendUrl}/api/tableau/initialize`,
        context: tableauContext
      });
      
      updateMessageById(initLoadingId, '🔄 Establishing secure connection...');
      
      const response = await proxyFetch(`${extensionState.backendUrl}/api/tableau/initialize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          tableauContext: tableauContext,
          clientTimestamp: new Date().toISOString(),
          source: 'chrome_extension'
        })
      });
      
      debugLogNetwork('Initialization response received', {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const result = await response.json();
      debugLog('Flask initialization response:', result);

      if (result.success) {
        flaskConnectionState.initialized = true;
        flaskConnectionState.connected = true;
        flaskConnectionState.workbookName = result.workbook_name;
        flaskConnectionState.dashboardName = result.dashboard_name;
        flaskConnectionState.connectionKey = result.connection_key || result.workbook_name;
        
        // Update loading message with success
        updateMessageById(initLoadingId, '✅ Connection established successfully!');
        
        // Start export status polling immediately
        if (flaskConnectionState.connectionKey) {
          startExportStatusPolling(flaskConnectionState.connectionKey);
        }
        
        ContentLogger.logConnectionEvent('initialization', true, {
          connectionKey: flaskConnectionState.connectionKey,
          workbookName: flaskConnectionState.workbookName,
          dashboardName: flaskConnectionState.dashboardName,
          cached: result.cached,
          availableViews: result.available_views
        });

        // Small delay before showing results
        setTimeout(() => {
          // Remove loading message
          const loadingElement = document.getElementById(initLoadingId);
          if (loadingElement) {
            loadingElement.remove();
          }
          
          // Show workbook summary instead of generic connection messages
          if (result.workbook_summary) {
            // Use the intelligent workbook summary from the backend
            // Only display summary lines if they're not empty
            if (result.workbook_summary.summary_line1 && result.workbook_summary.summary_line1.trim()) {
              appendMessage(result.workbook_summary.summary_line1, 'bot');
            }
            if (result.workbook_summary.summary_line2 && result.workbook_summary.summary_line2.trim()) {
              // Track this message so it can be removed when export completes
              connectionSummaryMessageId = `connection-summary-${Date.now()}`;
              appendMessage(result.workbook_summary.summary_line2, 'bot', null, connectionSummaryMessageId);
            }
          } else {
            // Fallback to basic connection message
            const cacheStatus = result.cached ? ' (cached)' : ' (new connection)';
            appendMessage(`📊 Connected to workbook: ${result.workbook_name}${cacheStatus}`, 'bot');
            if (result.available_views) {
              appendMessage(`🔍 Found ${result.available_views} worksheets ready for analysis.`, 'bot');
            }
          }

          debugLog('Flask backend initialized successfully');
          
          // Load available charts after backend initialization
          setTimeout(async () => {
            const chartLoadingId = `chart-loading-${Date.now()}`;
            appendMessage('🔄 Loading available charts...', 'bot status', null, chartLoadingId);
            await loadAvailableCharts();
            const chartsElement = document.getElementById(chartLoadingId);
            if (chartsElement) {
              chartsElement.remove();
            }
          }, 1000);
          
        }, 300);
        
        return true;
      } else {
        throw new Error(result.error || 'Unknown initialization error');
      }

    } catch (error) {
      debugLog('Flask initialization error:', error);
      flaskConnectionState.initialized = true;
      flaskConnectionState.connected = false;
      
      // Update loading message to show error
      updateMessageById(initLoadingId, `❌ Connection failed: ${error.message}`, 'bot error');
      
      ContentLogger.logConnectionEvent('initialization', false, {
        error: error.message,
        stack: error.stack
      });
      
      return false;
    }
  }

  // Comprehensive user data extraction from all available frontend sources
  async function extractAllUserData() {
    debugLog('Extracting comprehensive user data from frontend...');
    ContentLogger.info('=== STARTING COMPREHENSIVE USER DATA EXTRACTION ===', {
      timestamp: new Date().toISOString(),
      url: window.location.href
    }, 'USER_DATA_EXTRACTION_START');
    
    const userData = {
      extraction_timestamp: new Date().toISOString(),
      extraction_source: 'chrome_extension_content_script',
      
      // Basic page information
      page_info: {
        url: window.location.href,
        title: document.title,
        hostname: window.location.hostname,
        pathname: window.location.pathname,
        protocol: window.location.protocol,
        referrer: document.referrer
      },
      
      // Browser information
      browser_info: {
        user_agent: navigator.userAgent,
        language: navigator.language,
        languages: navigator.languages,
        platform: navigator.platform,
        vendor: navigator.vendor,
        screen_width: window.screen.width,
        screen_height: window.screen.height,
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone
      },
      
      // DOM-based user information extraction
      dom_extracted_data: extractUserDataFromDOM(),
      
      // Tableau API data (if available)
      tableau_api_data: await extractTableauAPIUserData(),
      
      // Cookies (non-sensitive)
      cookies: extractCookieData(),
      
      // Session/Local Storage (non-sensitive)
      storage_data: extractStorageData(),
      
      // Meta tags
      meta_tags: extractMetaTags(),
      
      // User profile elements from page
      user_profile_elements: extractUserProfileElements(),
      
      // Tableau-specific user context
      tableau_context: extractTableauContext(),
      
      // NEW: Intercepted network requests from Tableau API
      intercepted_network_requests: {
        capture_enabled: networkRequestsState.captureEnabled,
        capture_start_time: networkRequestsState.captureStartTime,
        capture_duration_seconds: networkRequestsState.captureTimeoutMs / 1000,
        total_requests_captured: networkRequestsState.requests.length,
        requests: networkRequestsState.requests.map(req => ({
          id: req.id,
          url: req.url,
          method: req.method,
          status: req.status,
          timestamp: req.timestamp,
          response_headers: req.response_headers,
          response_body: req.response_body,
          response_size_bytes: req.response_size_bytes,
          duration_ms: req.duration_ms
        }))
      }
    };
    
    debugLog('User data extraction complete', { data_points: Object.keys(userData).length });
    ContentLogger.info('=== USER DATA EXTRACTION COMPLETE ===', {
      total_top_level_keys: Object.keys(userData).length,
      extraction_timestamp: userData.extraction_timestamp,
      has_dom_data: !!userData.dom_extracted_data,
      has_tableau_api: !!userData.tableau_api_data,
      has_cookies: !!userData.cookies,
      has_storage: !!userData.storage_data,
      has_meta_tags: !!userData.meta_tags,
      has_profile_elements: !!userData.user_profile_elements,
      has_network_requests: !!userData.intercepted_network_requests,
      network_requests_captured: userData.intercepted_network_requests.total_requests_captured
    }, 'USER_DATA_EXTRACTION_COMPLETE');
    
    return userData;
  }

  // Extract user data from DOM elements
  function extractUserDataFromDOM() {
    ContentLogger.debug('Extracting DOM user data...', {}, 'DOM_EXTRACTION_START');
    
    const domData = {
      page_title: document.title,
      body_classes: document.body ? document.body.className : null,
      html_lang: document.documentElement.lang
    };
    
    try {
      // Look for common user profile selectors
      const userSelectors = [
        '[data-user-name]',
        '[data-username]',
        '[data-user-email]',
        '[data-user-id]',
        '.user-name',
        '.username',
        '.user-email',
        '.user-profile',
        '[aria-label*="user"]',
        '[aria-label*="profile"]',
        '[class*="user"]',
        '[class*="profile"]',
        '[id*="user"]',
        '[id*="profile"]'
      ];
      
      const foundElements = {};
      userSelectors.forEach(selector => {
        try {
          const elements = document.querySelectorAll(selector);
          if (elements.length > 0) {
            const elementData = [];
            elements.forEach((el, idx) => {
              if (idx < 5) { // Limit to first 5 to avoid excessive data
                elementData.push({
                  text: el.textContent?.trim().substring(0, 200),
                  id: el.id,
                  className: el.className,
                  tag: el.tagName,
                  attributes: {
                    'data-user-name': el.getAttribute('data-user-name'),
                    'data-username': el.getAttribute('data-username'),
                    'data-user-email': el.getAttribute('data-user-email'),
                    'data-user-id': el.getAttribute('data-user-id'),
                    'aria-label': el.getAttribute('aria-label'),
                    'title': el.getAttribute('title')
                  }
                });
              }
            });
            if (elementData.length > 0) {
              foundElements[selector] = elementData;
            }
          }
        } catch (e) {
          // Silently continue if selector fails
        }
      });
      
      domData.user_profile_selectors = foundElements;
      
      // Extract from headers (h1, h2, etc that might contain user names)
      const headers = document.querySelectorAll('h1, h2, h3, .header, header');
      const headerTexts = [];
      headers.forEach((h, idx) => {
        if (idx < 10) {
          const text = h.textContent?.trim();
          if (text && text.length < 100) {
            headerTexts.push(text);
          }
        }
      });
      domData.header_texts = headerTexts;
      
      ContentLogger.debug('DOM extraction complete', {
        found_selectors: Object.keys(domData.user_profile_selectors || {}).length,
        headers_found: headerTexts.length
      }, 'DOM_EXTRACTION_COMPLETE');
      
    } catch (e) {
      domData.extraction_error = e.message;
      ContentLogger.error('DOM extraction error', { error: e.message }, 'DOM_EXTRACTION_ERROR');
    }
    
    return domData;
  }

  // Extract user data from Tableau API
  async function extractTableauAPIUserData() {
    ContentLogger.debug('Extracting Tableau API user data...', {}, 'TABLEAU_API_EXTRACTION_START');
    
    const apiData = {
      tableau_available: typeof window.tableau !== 'undefined',
      extensions_available: false,
      user_info: null
    };
    
    try {
      if (window.tableau && window.tableau.extensions) {
        apiData.extensions_available = true;
        
        // Try to get dashboard content
        if (window.tableau.extensions.dashboardContent) {
          const dashboard = window.tableau.extensions.dashboardContent.dashboard;
          
          if (dashboard) {
            apiData.dashboard_info = {
              name: dashboard.name,
              worksheet_count: dashboard.worksheets?.length
            };
            
            // Try to get workbook info which might contain user context
            if (dashboard.workbook) {
              apiData.workbook_info = {
                name: dashboard.workbook.name,
                active_sheet: dashboard.workbook.activeSheet?.name
              };
            }
          }
        }
        
        // Try to access settings which might contain user info
        if (window.tableau.extensions.settings) {
          apiData.has_settings = true;
        }
        
        // Try to get environment info
        if (window.tableau.extensions.environment) {
          const env = window.tableau.extensions.environment;
          apiData.environment = {
            context: env.context,
            mode: env.mode,
            locale: env.locale,
            version: env.version
          };
        }
      }
    } catch (e) {
      apiData.extraction_error = e.message;
    }
    
    return apiData;
  }

  // Extract cookie data (only non-sensitive cookies)
  function extractCookieData() {
    const cookieData = {
      cookie_count: 0,
      cookies: []
    };
    
    try {
      const cookies = document.cookie.split(';');
      cookieData.cookie_count = cookies.length;
      
      cookies.forEach(cookie => {
        const [name, value] = cookie.trim().split('=');
        // Only store cookie names and value lengths, not actual values for privacy
        if (name) {
          cookieData.cookies.push({
            name: name.trim(),
            value_length: value ? value.length : 0,
            // Store actual value only for Tableau-related or user-related cookies
            value: (name.toLowerCase().includes('user') || 
                   name.toLowerCase().includes('tableau') ||
                   name.toLowerCase().includes('name') ||
                   name.toLowerCase().includes('email')) ? value : '[REDACTED]'
          });
        }
      });
    } catch (e) {
      cookieData.extraction_error = e.message;
    }
    
    return cookieData;
  }

  // Extract storage data
  function extractStorageData() {
    const storageData = {
      local_storage: {},
      session_storage: {}
    };
    
    try {
      // Local Storage
      if (window.localStorage) {
        const localKeys = Object.keys(localStorage);
        storageData.local_storage.key_count = localKeys.length;
        storageData.local_storage.keys = [];
        
        localKeys.forEach(key => {
          // Only store user-related keys
          if (key.toLowerCase().includes('user') || 
              key.toLowerCase().includes('profile') ||
              key.toLowerCase().includes('name') ||
              key.toLowerCase().includes('email') ||
              key.toLowerCase().includes('tableau')) {
            try {
              const value = localStorage.getItem(key);
              storageData.local_storage.keys.push({
                key: key,
                value: value,
                value_type: typeof value
              });
            } catch (e) {
              // Some keys might not be accessible
            }
          }
        });
      }
      
      // Session Storage
      if (window.sessionStorage) {
        const sessionKeys = Object.keys(sessionStorage);
        storageData.session_storage.key_count = sessionKeys.length;
        storageData.session_storage.keys = [];
        
        sessionKeys.forEach(key => {
          // Only store user-related keys
          if (key.toLowerCase().includes('user') || 
              key.toLowerCase().includes('profile') ||
              key.toLowerCase().includes('name') ||
              key.toLowerCase().includes('email') ||
              key.toLowerCase().includes('tableau')) {
            try {
              const value = sessionStorage.getItem(key);
              storageData.session_storage.keys.push({
                key: key,
                value: value,
                value_type: typeof value
              });
            } catch (e) {
              // Some keys might not be accessible
            }
          }
        });
      }
    } catch (e) {
      storageData.extraction_error = e.message;
    }
    
    return storageData;
  }

  // Extract meta tags that might contain user info
  function extractMetaTags() {
    const metaData = {
      tags: []
    };
    
    try {
      const metaTags = document.querySelectorAll('meta');
      metaTags.forEach(meta => {
        const name = meta.getAttribute('name') || meta.getAttribute('property');
        const content = meta.getAttribute('content');
        
        if (name && content) {
          // Store all meta tags as they might contain useful context
          metaData.tags.push({
            name: name,
            content: content.substring(0, 500) // Limit length
          });
        }
      });
    } catch (e) {
      metaData.extraction_error = e.message;
    }
    
    return metaData;
  }

  // Extract user profile elements (avatars, names, etc.)
  function extractUserProfileElements() {
    const profileData = {
      found_elements: []
    };
    
    try {
      // Look for common profile elements
      const profileSelectors = [
        'img[alt*="profile"]',
        'img[alt*="avatar"]',
        'img[alt*="user"]',
        '.avatar',
        '.profile-image',
        '.user-avatar',
        '[data-tb-test-id*="user"]',
        '[data-tb-test-id*="profile"]',
        'button[aria-label*="user"]',
        'button[aria-label*="profile"]',
        'a[href*="/user"]',
        'a[href*="/profile"]'
      ];
      
      profileSelectors.forEach(selector => {
        try {
          const elements = document.querySelectorAll(selector);
          elements.forEach((el, idx) => {
            if (idx < 3) { // Limit to first 3
              const elementInfo = {
                selector: selector,
                tag: el.tagName,
                id: el.id,
                className: el.className,
                text: el.textContent?.trim().substring(0, 100),
                aria_label: el.getAttribute('aria-label'),
                title: el.getAttribute('title'),
                alt: el.getAttribute('alt'),
                href: el.getAttribute('href')
              };
              
              // For images, get src
              if (el.tagName === 'IMG') {
                elementInfo.src_length = el.src?.length || 0;
              }
              
              profileData.found_elements.push(elementInfo);
            }
          });
        } catch (e) {
          // Silently continue
        }
      });
      
      // Try to find user name in navigation or header
      const navElements = document.querySelectorAll('nav, header, .navigation, .header, .toolbar');
      navElements.forEach((nav, idx) => {
        if (idx < 5) {
          const text = nav.textContent?.trim();
          if (text && text.length < 500) {
            profileData.found_elements.push({
              selector: 'navigation/header',
              tag: nav.tagName,
              text: text,
              className: nav.className
            });
          }
        }
      });
      
    } catch (e) {
      profileData.extraction_error = e.message;
    }
    
    return profileData;
  }

  // Send extracted user data to backend
  async function sendUserDataToBackend(userData) {
    try {
      debugLog('Sending user data to backend for logging...');
      ContentLogger.info('=== SENDING USER DATA TO BACKEND ===', {
        url: `${extensionState.backendUrl}/api/user/log_frontend_data`,
        data_size_chars: JSON.stringify(userData).length,
        top_level_keys: Object.keys(userData).length
      }, 'USER_DATA_SEND_START');
      
      const response = await proxyFetch(`${extensionState.backendUrl}/api/user/log_frontend_data`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(userData)
      });
      
      ContentLogger.info('Backend response received', {
        status: response.status,
        ok: response.ok,
        statusText: response.statusText
      }, 'USER_DATA_SEND_RESPONSE');
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      const result = await response.json();
      debugLog('User data logged successfully to backend', result);
      ContentLogger.info('=== USER DATA SUCCESSFULLY LOGGED TO BACKEND ===', {
        total_users: result.total_users,
        data_points: result.data_points_captured,
        file_path: result.file_path,
        success: result.success
      }, 'USER_DATA_LOGGED');
      
      return result;
      
    } catch (error) {
      debugLog('Failed to send user data to backend:', error);
      ContentLogger.error('=== FAILED TO SEND USER DATA TO BACKEND ===', { 
        error: error.message,
        stack: error.stack 
      }, 'USER_DATA_SEND_ERROR');
      return null;
    }
  }

  // Extract context from Tableau page
  function extractTableauContext() {
    const context = {
      dashboardName: null,
      workbookName: null,
      workbookId: null,
      viewContentUrl: null,
      siteContentUrl: null,
      worksheetNames: [],
      url: window.location.href,
      timestamp: new Date().toISOString()
    };

    // Try to extract from URL
    const url = window.location.href;
    
    // Extract site content URL (site slug)
    const siteMatch = url.match(/\/#\/site\/([^\/]+)/) || url.match(/\/t\/([^\/]+)/);
    if (siteMatch) {
      context.siteContentUrl = decodeURIComponent(siteMatch[1]);
    } else if (window.location.hostname === 'tableau.uberinternal.com') {
      // For uberinternal.com, missing site parameter means "Default" site
      context.siteContentUrl = 'Default';
    }

    // Extract workbook name from URL patterns (name-based)
    const workbookMatch = url.match(/\/workbook\/([^\/\?]+)/) || 
                         url.match(/\/views\/([^\/\?]+)/) ||
                         url.match(/\/#\/site\/[^\/]+\/views\/([^\/\?]+)/) ||  // Tableau Cloud view mode
                         url.match(/\/t\/[^\/]+\/views\/([^\/\?]+)/) ||
                         url.match(/\/t\/[^\/]+\/authoring\/([^\/\?#]+)/);
    
    if (workbookMatch) {
      context.workbookName = decodeURIComponent(workbookMatch[1]);
    }

    // Extract workbookId from numeric-ID URL pattern: /workbooks/{id}/views
    const workbookIdMatch = url.match(/\/(?:#\/site\/[^\/]+\/)?workbooks\/([^\/\?#]+)\/views/i);
    if (workbookIdMatch) {
      context.workbookId = decodeURIComponent(workbookIdMatch[1]);
    }

    // Extract dashboard name from URL patterns
    const dashboardMatch = url.match(/\/#\/site\/[^\/]+\/views\/[^\/]+\/([^\/\?#]+)/) ||  // Tableau Cloud view mode
                          url.match(/\/t\/[^\/]+\/authoring\/[^\/]+\/([^\/\?#]+)/) ||     // Tableau Cloud authoring mode
                          url.match(/\/views\/[^\/]+\/([^\/\?#]+)/) ||                   // Standard view mode
                          url.match(/\/dashboard\/([^\/\?#]+)/) ||
                          url.match(/\/([^\/\?#]+)(?:[\?#]|$)/);
    
    if (dashboardMatch) {
      context.dashboardName = decodeURIComponent(dashboardMatch[1]);
    }

    // Extract view contentUrl: "<workbook>/<view>" if available
    const viewPathMatch = url.match(/\/#\/site\/[^\/]+\/(?:views|authoring)\/([^\/]+)\/([^\?#]+)/) ||
                         url.match(/\/t\/[^\/]+\/(?:views|authoring)\/([^\/]+)\/([^\?#]+)/) ||
                         url.match(/\/views\/([^\/]+)\/([^\?#]+)/);
    if (viewPathMatch) {
      const wb = decodeURIComponent(viewPathMatch[1]);
      const vw = decodeURIComponent(viewPathMatch[2]);
      context.viewContentUrl = `${wb}/${vw}`;
      // If workbookName still empty, set it from path
      if (!context.workbookName) context.workbookName = wb;
    }

    // Try to extract from DOM/title with improved heuristics
    if (!context.workbookName) {
      const cleaned = cleanTitleForWorkbook(document.title || '');
      if (cleaned) {
        context.workbookName = cleaned;
      } else {
        const domName = extractWorkbookNameFromDom();
        if (domName) {
          context.workbookName = domName;
        }
      }
    }

    // Try to extract from Tableau API if available
    if (window.tableau && window.tableau.extensions) {
      try {
        const dashboard = window.tableau.extensions.dashboardContent.dashboard;
        if (dashboard) {
          context.dashboardName = dashboard.name;
          context.worksheetNames = dashboard.worksheets.map(ws => ws.name);
          
          if (dashboard.workbook) {
            context.workbookName = dashboard.workbook.name;
          }
        }
      } catch (e) {
        debugLog('Could not access Tableau Extensions API:', e);
      }
    }

    debugLog('Extracted Tableau context:', context);
    return context;
  }

  // Remove Tableau brand suffixes and generic tokens, but preserve actual workbook suffixes like "- Copy"
  function cleanTitleForWorkbook(title) {
    if (!title || typeof title !== 'string') return '';
    let t = title.trim();
    // Remove common Tableau branding suffixes
    t = t.replace(/\s*[-|—–]\s*Tableau(?:\s*Cloud)?\s*$/i, '');
    t = t.replace(/\s*\|\s*Tableau(?:\s*Cloud)?\s*$/i, '');
    // Some titles may include extra context like "Workbooks" or "Views"; drop those if they are trailing after a dash
    t = t.replace(/\s*[-|—–]\s*(Workbooks|Views)\s*$/i, '');
    // Trim again
    t = t.trim();
    // Avoid returning overly generic titles
    if (/^(Home|Workbooks|Views|Tableau)$/i.test(t)) return '';
    return t;
  }

  // Try DOM-based extraction for workbook name on workbook listing/views pages
  function extractWorkbookNameFromDom() {
    try {
      const candidates = [];
      const pushText = (el) => {
        if (!el) return;
        const txt = (el.getAttribute && el.getAttribute('title')) || el.textContent || '';
        const cleaned = cleanTitleForWorkbook(txt);
        if (cleaned) candidates.push(cleaned);
      };

      const selectors = [
        'h1',
        'header h1',
        'div[role="heading"]',
        '[data-testid*="workbook"][title]',
        '[data-testid*="workbook"]',
        '.tab-title',
        '.tabToolbar .tab-text',
        'main h1',
        'main [role="heading"]'
      ];

      selectors.forEach(sel => {
        const el = document.querySelector(sel);
        if (el) pushText(el);
      });

      // Meta tags
      const og = document.querySelector('meta[property="og:title"]')?.getAttribute('content') || '';
      const tw = document.querySelector('meta[name="twitter:title"]')?.getAttribute('content') || '';
      if (og) candidates.push(cleanTitleForWorkbook(og));
      if (tw) candidates.push(cleanTitleForWorkbook(tw));

      // Filter unique and non-empty
      const unique = Array.from(new Set(candidates.filter(Boolean)));
      // Prefer the longest reasonable candidate (likely includes full workbook name)
      unique.sort((a, b) => b.length - a.length);
      return unique[0] || '';
    } catch (e) {
      debugLog('DOM workbook name extraction failed', { error: e.message });
      return '';
    }
  }

  // Load available charts
  async function loadAvailableCharts() {
    debugLog('Loading available charts from backend...');
    
    try {
      if (!flaskConnectionState.connected) {
        debugLog('Flask backend not connected, cannot load charts');
        return false;
      }

      const connectionKey = flaskConnectionState.connectionKey || 
                           flaskConnectionState.workbookName ||
                           'default_workbook';
      
      debugLog('Using connection key for charts request', { connectionKey });
      
      const response = await proxyFetch(`${extensionState.backendUrl}/api/get_worksheets?connection_key=${encodeURIComponent(connectionKey)}`);
      
      if (!response.ok) {
        const errorText = await response.text();
        debugLog('Charts request failed', { status: response.status, error: errorText });
        throw new Error(`HTTP ${response.status}: ${response.statusText} - ${errorText}`);
      }

      const data = await response.json();
      debugLog('Charts loaded from backend:', data);

      if (data.success && data.worksheets) {
        chartSelectionState.availableCharts = data.worksheets;
        chartSelectionState.chartsLoaded = true;
        
        // REMOVED: No longer creating chart buttons on initial load
        // createChartButtons();
        
        debugLog(`Successfully loaded ${data.worksheets.length} charts (will show dropdown when needed)`);
        // COMMENTED OUT FOR SIMPLIFIED DISPLAY
        // appendMessage(`Found ${data.worksheets.length} charts available for analysis`, 'bot status');
        
        // Load and display workbook summary before enabling chat
        await loadAndDisplayWorkbookSummary();
        
        // Enable chat input now that backend is connected and charts are loaded
        if (ui.chatInput) {
          ui.chatInput.disabled = false;
          ui.chatInput.placeholder = 'Ask me anything about your data...';
          debugLog('Chat input enabled - ready for questions');
        }
        
        // Submit button will be enabled via input event listener when user types
        
        return true;
      } else {
        throw new Error(data.error || 'Failed to load charts');
      }

    } catch (error) {
      debugLog('Failed to load charts:', error);
      appendMessage(`Could not load chart information: ${error.message}`, 'bot error');
      return false;
    }
  }

  // Load and display workbook data summary
  async function loadAndDisplayWorkbookSummary() {
    debugLog('Loading workbook data summary...');
    
    try {
      const workbookName = flaskConnectionState.workbookName || flaskConnectionState.connectionKey;
      
      if (!workbookName) {
        debugLog('No workbook name available, skipping summary');
        return false;
      }
      
      const response = await proxyFetch(`${extensionState.backendUrl}/api/get_workbook_summary?workbook=${encodeURIComponent(workbookName)}`);
      
      if (!response.ok) {
        debugLog('Failed to load workbook summary', { status: response.status });
        // If CSV not loaded yet, backend will return 400
        if (response.status === 400) {
          debugLog('CSV data not loaded yet, will retry...');
          return false;
        }
        return false;
      }
      
      const result = await response.json();
      debugLog('Workbook summary loaded:', result);
      
      if (result.success && result.summary) {
        displayWorkbookSummary(result.summary);
        return true;
      }
      
      return false;
      
    } catch (error) {
      debugLog('Error loading workbook summary:', error);
      return false;
    }
  }
  
  // Display workbook summary with column type buttons
  function displayWorkbookSummary(summary) {
    debugLog('Displaying workbook summary');
    
    // Check if summary already exists - prevent duplicates
    if (document.querySelector('.workbook-summary-container')) {
      debugLog('Workbook summary already displayed, skipping duplicate');
      return;
    }
    
    // Create summary container
    const summaryContainer = document.createElement('div');
    summaryContainer.className = 'workbook-summary-container';
    summaryContainer.style.cssText = `
      margin: 12px 0;
      padding: 16px;
      background: linear-gradient(135deg, #f8fafc 0%, #e0f2fe 100%);
      border-radius: 8px;
      border-left: 4px solid #0b72e7;
    `;
    
    // Data description
    const descDiv = document.createElement('div');
    descDiv.style.cssText = `
      font-size: 12px;
      color: #334155;
      margin-bottom: 12px;
      line-height: 1.5;
    `;
    descDiv.textContent = summary.data_description || 'Workbook data loaded and ready for analysis.';
    summaryContainer.appendChild(descDiv);
    
    // Column type buttons - First row (Numerical, Categorical, Calculated)
    const firstRowContainer = document.createElement('div');
    firstRowContainer.style.cssText = `
      display: flex;
      flex-direction: row;
      gap: 8px;
      flex-wrap: nowrap;
      margin-bottom: 8px;
    `;
    
    // Numerical columns button
    const numBtn = createColumnTypeButton(
      'Numerical', 
      '#dbeafe', 
      '#1e40af',
      () => showColumnDetails('Numerical Columns', summary.numerical_columns, 'numerical')
    );
    firstRowContainer.appendChild(numBtn);
    
    // Categorical columns button
    const catBtn = createColumnTypeButton(
      'Categorical', 
      '#dcfce7', 
      '#166534',
      () => showColumnDetails('Categorical Columns', summary.categorical_columns, 'categorical')
    );
    firstRowContainer.appendChild(catBtn);
    
    // Calculated columns button
    const calcBtn = createColumnTypeButton(
      'Calculated', 
      '#fef3c7', 
      '#92400e',
      () => showColumnDetails('Calculated Columns', summary.calculated_columns, 'calculated')
    );
    firstRowContainer.appendChild(calcBtn);
    
    // Second row container (Description button)
    const secondRowContainer = document.createElement('div');
    secondRowContainer.style.cssText = `
      display: flex;
      flex-direction: row;
      gap: 8px;
    `;
    
    // Field Descriptions button
    const descBtn = createColumnTypeButton(
      'Description', 
      '#f3e8ff', 
      '#6b46c1',
      () => fetchAndShowFieldDescriptions(flaskConnectionState.workbookName)
    );
    secondRowContainer.appendChild(descBtn);
    
    // Add both rows to summary container
    summaryContainer.appendChild(firstRowContainer);
    summaryContainer.appendChild(secondRowContainer);
    
    // Append at bottom of current messages (after welcome message)
    ui.chatLog.appendChild(summaryContainer);
    ui.chatLog.scrollTop = ui.chatLog.scrollHeight;
    
    debugLog('Workbook summary displayed successfully');
  }
  
  // Create column type button
  function createColumnTypeButton(label, bgColor, textColor, onClick) {
    const btn = document.createElement('button');
    btn.textContent = label;
    btn.type = 'button';
    btn.style.cssText = `
      padding: 8px 16px;
      background: ${bgColor};
      color: ${textColor};
      border: 1px solid ${textColor}33;
      border-radius: 6px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 500;
      transition: all 0.2s ease;
      flex: 1;
      min-width: 100px;
    `;
    
    btn.addEventListener('mouseenter', function() {
      this.style.transform = 'translateY(-2px)';
      this.style.boxShadow = '0 4px 8px rgba(0,0,0,0.1)';
    });
    
    btn.addEventListener('mouseleave', function() {
      this.style.transform = 'translateY(0)';
      this.style.boxShadow = 'none';
    });
    
    btn.addEventListener('click', onClick);
    
    return btn;
  }
  
  // Show column details modal
  function showColumnDetails(title, columns, type) {
    debugLog('Showing column details', { title, type, columnCount: Object.keys(columns || {}).length });
    
    // Create modal
    const modal = document.createElement('div');
    modal.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.7);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 2147483650;
      backdrop-filter: blur(4px);
    `;
    
    // Modal content
    const modalContent = document.createElement('div');
    modalContent.style.cssText = `
      background: white;
      border-radius: 12px;
      max-width: 800px;
      width: 90%;
      max-height: 80vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3);
    `;
    
    // Header
    const header = document.createElement('div');
    header.style.cssText = `
      padding: 16px 20px;
      border-bottom: 1px solid #e5e7eb;
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #f9fafb;
      border-radius: 12px 12px 0 0;
    `;
    
    const headerTitle = document.createElement('h3');
    headerTitle.textContent = title;
    headerTitle.style.cssText = `
      margin: 0;
      font-size: 18px;
      font-weight: 600;
      color: #1f2937;
    `;
    
    const closeBtn = document.createElement('button');
    closeBtn.textContent = '✕';
    closeBtn.style.cssText = `
      background: #f3f4f6;
      border: none;
      padding: 8px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 16px;
      color: #6b7280;
    `;
    closeBtn.onclick = () => modal.remove();
    
    header.appendChild(headerTitle);
    header.appendChild(closeBtn);
    
    // Content
    const content = document.createElement('div');
    content.style.cssText = `
      padding: 20px;
      overflow-y: auto;
      flex: 1;
    `;
    
    if (!columns || Object.keys(columns).length === 0) {
      content.innerHTML = '<p style="color: #6b7280; text-align: center;">No columns of this type found.</p>';
    } else {
      Object.entries(columns).forEach(([colName, colData]) => {
        const colDiv = document.createElement('div');
        colDiv.style.cssText = `
          margin-bottom: 20px;
          padding: 16px;
          background: #f9fafb;
          border-radius: 8px;
          border: 1px solid #e5e7eb;
        `;
        
        // Column name
        const nameDiv = document.createElement('div');
        nameDiv.style.cssText = `
          font-weight: 600;
          font-size: 15px;
          color: #1f2937;
          margin-bottom: 12px;
        `;
        nameDiv.textContent = colName;
        colDiv.appendChild(nameDiv);
        
        // Column details
        const detailsDiv = document.createElement('div');
        detailsDiv.style.cssText = `
          font-size: 13px;
          color: #6b7280;
          line-height: 1.6;
        `;
        
        if (type === 'calculated') {
          // Show formula for calculated columns
          detailsDiv.innerHTML = `
            <strong>Formula:</strong> <code style="background: white; padding: 2px 6px; border-radius: 4px;">${colData.formula || 'N/A'}</code><br>
            <strong>Data Type:</strong> ${colData.datatype || 'N/A'}
          `;
        } else if (type === 'descriptions') {
          // Show field description
          detailsDiv.innerHTML = `
            <div style="background: white; padding: 12px; border-radius: 6px; border: 1px solid #e5e7eb;">
              <strong>Description:</strong><br>
              <span style="color: #374151; line-height: 1.6; font-style: italic;">${colData.description || 'No description available'}</span>
            </div>
          `;
        } else {
          // Show statistics for numerical/categorical
          const stats = colData.stats || colData;
          const statsHTML = Object.entries(stats)
            .map(([key, value]) => {
              const displayValue = typeof value === 'number' ? value.toFixed(2) : value;
              return `<strong>${key}:</strong> ${displayValue}`;
            })
            .join('<br>');
          detailsDiv.innerHTML = statsHTML;
        }
        
        colDiv.appendChild(detailsDiv);
        content.appendChild(colDiv);
      });
    }
    
    modalContent.appendChild(header);
    modalContent.appendChild(content);
    modal.appendChild(modalContent);
    
    // Close on overlay click
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.remove();
    });
    
    // Close on Escape key
    const escHandler = (e) => {
      if (e.key === 'Escape') {
        modal.remove();
        document.removeEventListener('keydown', escHandler);
      }
    };
    document.addEventListener('keydown', escHandler);
    
    document.body.appendChild(modal);
  }
  
  // Fetch and show field descriptions
  async function fetchAndShowFieldDescriptions(workbookName) {
    debugLog('Fetching field descriptions', { workbookName });
    
    try {
      // Validate workbook name
      if (!workbookName) {
        throw new Error('No workbook name available. Please ensure you are connected to a workbook first.');
      }
      
      debugLog('Using workbook name for API request', { finalWorkbookName: workbookName });
      
      // Show loading modal first
      const loadingModal = showLoadingModal('Loading Field Descriptions...');
      
      // Fetch field descriptions from API
      const response = await proxyFetch(`${extensionState.backendUrl}/api/get_field_descriptions?workbook=${encodeURIComponent(workbookName)}`);
      
      const data = await response.json();
      
      // Remove loading modal
      if (loadingModal && loadingModal.parentNode) {
        loadingModal.remove();
      }
      
      if (!data.success) {
        throw new Error(data.error || 'Failed to fetch field descriptions');
      }
      
      debugLog('Field descriptions fetched successfully', { 
        totalDescriptions: data.total_descriptions,
        workbookName: data.workbook_name 
      });
      
      // Show field descriptions modal
      showColumnDetails('Field Descriptions', data.field_descriptions, 'descriptions');
      
    } catch (error) {
      debugLog('Error fetching field descriptions', { error: error.message });
      
      // Remove loading modal if it exists
      const loadingModal = document.querySelector('[data-loading-modal]');
      if (loadingModal) {
        loadingModal.remove();
      }
      
      // Show error modal
      showErrorModal('Field Descriptions Error', `Failed to load field descriptions: ${error.message}`);
    }
  }
  
  // Show loading modal
  function showLoadingModal(message) {
    const modal = document.createElement('div');
    modal.setAttribute('data-loading-modal', 'true');
    modal.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.7);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 2147483651;
      backdrop-filter: blur(4px);
    `;
    
    const content = document.createElement('div');
    content.style.cssText = `
      background: white;
      border-radius: 12px;
      padding: 32px;
      text-align: center;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3);
    `;
    
    const spinner = document.createElement('div');
    spinner.style.cssText = `
      width: 32px;
      height: 32px;
      border: 3px solid #f3f4f6;
      border-top: 3px solid #6b46c1;
      border-radius: 50%;
      animation: spin 1s linear infinite;
      margin: 0 auto 16px auto;
    `;
    
    const text = document.createElement('div');
    text.textContent = message;
    text.style.cssText = `
      font-size: 16px;
      color: #374151;
      font-weight: 500;
    `;
    
    content.appendChild(spinner);
    content.appendChild(text);
    modal.appendChild(content);
    
    // Add spinner animation
    if (!document.querySelector('#spinner-animation')) {
      const style = document.createElement('style');
      style.id = 'spinner-animation';
      style.textContent = `
        @keyframes spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
      `;
      document.head.appendChild(style);
    }
    
    document.body.appendChild(modal);
    return modal;
  }
  
  // Show error modal
  function showErrorModal(title, message) {
    const modal = document.createElement('div');
    modal.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.7);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 2147483651;
      backdrop-filter: blur(4px);
    `;
    
    const content = document.createElement('div');
    content.style.cssText = `
      background: white;
      border-radius: 12px;
      max-width: 500px;
      width: 90%;
      padding: 24px;
      text-align: center;
      box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3);
    `;
    
    const titleEl = document.createElement('h3');
    titleEl.textContent = title;
    titleEl.style.cssText = `
      margin: 0 0 16px 0;
      font-size: 18px;
      color: #dc2626;
      font-weight: 600;
    `;
    
    const messageEl = document.createElement('p');
    messageEl.textContent = message;
    messageEl.style.cssText = `
      margin: 0 0 24px 0;
      color: #6b7280;
      line-height: 1.5;
    `;
    
    const closeBtn = document.createElement('button');
    closeBtn.textContent = 'Close';
    closeBtn.style.cssText = `
      background: #dc2626;
      color: white;
      border: none;
      padding: 12px 24px;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 500;
    `;
    closeBtn.onclick = () => modal.remove();
    
    content.appendChild(titleEl);
    content.appendChild(messageEl);
    content.appendChild(closeBtn);
    modal.appendChild(content);
    
    // Close on overlay click
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.remove();
    });
    
    document.body.appendChild(modal);
  }

  // Create chart selection buttons
  function createChartButtons() {
    debugLog('Creating chart selection buttons...');
    
    // Clear existing buttons
    ui.chartButtonsContainer.innerHTML = '';

    if (chartSelectionState.availableCharts.length === 0) {
      ui.chartButtonsContainer.innerHTML = '<div style="color: #666; font-size: 14px; text-align: center; padding: 10px;">No charts available</div>';
      return;
    }

    // Add header
    const header = document.createElement('div');
    header.style.cssText = 'font-size: 12px; font-weight: 600; color: #495057; margin-bottom: 8px; text-align: center;';
    header.textContent = `Select a Chart to Analyze (${chartSelectionState.availableCharts.length} available)`;
    ui.chartButtonsContainer.appendChild(header);

    // Create button grid
    const buttonGrid = document.createElement('div');
    buttonGrid.style.cssText = `
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 6px;
      margin-top: 8px;
    `;

    chartSelectionState.availableCharts.forEach((chart, index) => {
      const button = createChartButton(chart, index);
      buttonGrid.appendChild(button);
    });

    ui.chartButtonsContainer.appendChild(buttonGrid);
    debugLog(`Created ${chartSelectionState.availableCharts.length} chart buttons`);
  }

  function createChartButton(chart, index) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'tableau-chart-selection-button';
    button.textContent = chart.name || `Chart ${index + 1}`;
    button.title = `Click to analyze: ${chart.name}`;

    // Click handler
    button.addEventListener('click', async () => {
      await selectChart(chart, button);
    });

    return button;
  }

  async function selectChart(chart, buttonElement) {
    debugLog('Chart selected:', chart);
    ContentLogger.logChartSelection(chart.name, {
      chartId: chart.id,
      connectionKey: flaskConnectionState.connectionKey,
      workbookName: flaskConnectionState.workbookName
    });
    
    // Update selection state
    chartSelectionState.selectedChart = chart;
    navigationState.selectedChart = chart;
    
    // Update button appearance
    document.querySelectorAll('.tableau-chart-selection-button').forEach(btn => {
      btn.classList.remove('selected');
    });
    buttonElement.classList.add('selected');
    
    // Navigate to chat mode
    navigateToChatPage();
    
    // Trigger automatic analysis for the selected chart
    try {
      await triggerAutoAnalysis(chart);
    } catch (error) {
      debugLog('Error triggering auto-analysis:', error);
      appendMessage(`❌ Auto-analysis failed: ${error.message}`, 'bot error');
    }
    
    debugLog('Chart selection completed', { selected: chart.name });
    ContentLogger.info('Chart selection completed', {
      selectedChart: chart.name,
      connectionKey: flaskConnectionState.connectionKey,
      workbookName: flaskConnectionState.workbookName
    }, 'CHART_SELECTION_COMPLETED');
  }

  // Show inline chart selection dropdown for analysis queries
  function showChartSelectionDropdown(availableCharts, originalQuestion) {
    debugLog('Showing chart selection dropdown', {
      charts_count: availableCharts.length,
      original_question: originalQuestion
    });
    
    // Create dropdown message
    const dropdownContainer = document.createElement('div');
    dropdownContainer.className = 'chart-selection-dropdown-container';
    dropdownContainer.style.cssText = `
      margin: 12px 0;
      padding: 16px;
      background: #f8f9fa;
      border-radius: 8px;
      border: 1px solid #dee2e6;
    `;
    
    // Header
    const header = document.createElement('div');
    header.textContent = '📊 Select a chart to analyze:';
    header.style.cssText = `
      font-weight: 600;
      margin-bottom: 12px;
      color: #495057;
      font-size: 14px;
    `;
    dropdownContainer.appendChild(header);
    
    // Dropdown select
    const select = document.createElement('select');
    select.className = 'chart-selection-dropdown';
    select.style.cssText = `
      width: 100%;
      padding: 10px;
      border: 1px solid #ced4da;
      border-radius: 6px;
      font-size: 14px;
      background: white;
      cursor: pointer;
      margin-bottom: 12px;
    `;
    
    // Add placeholder option
    const placeholderOption = document.createElement('option');
    placeholderOption.value = '';
    placeholderOption.textContent = '-- Choose a chart --';
    placeholderOption.disabled = true;
    placeholderOption.selected = true;
    select.appendChild(placeholderOption);
    
    // Add chart options
    availableCharts.forEach((chart, index) => {
      const option = document.createElement('option');
      option.value = index;
      option.textContent = chart.name || chart;
      select.appendChild(option);
    });
    
    dropdownContainer.appendChild(select);
    
    // Confirm button
    const confirmButton = document.createElement('button');
    confirmButton.textContent = 'Analyze Selected Chart';
    confirmButton.disabled = true;
    confirmButton.style.cssText = `
      padding: 10px 16px;
      background: #0b72e7;
      color: white;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-size: 14px;
      font-weight: 500;
      width: 100%;
      opacity: 0.5;
    `;
    
    // Enable button when chart is selected
    select.addEventListener('change', () => {
      if (select.value) {
        confirmButton.disabled = false;
        confirmButton.style.opacity = '1';
        confirmButton.style.cursor = 'pointer';
      }
    });
    
    // Handle chart selection
    confirmButton.addEventListener('click', async () => {
      const selectedIndex = parseInt(select.value);
      const selectedChart = availableCharts[selectedIndex];
      
      debugLog('Chart selected from dropdown', {
        chart: selectedChart,
        original_question: originalQuestion
      });
      
      // Disable button to prevent double-click
      confirmButton.disabled = true;
      confirmButton.textContent = 'Processing...';
      
      // Remove dropdown
      dropdownContainer.remove();
      
      // Handle the chart selection
      await handleChartSelectionForAnalysis(selectedChart, originalQuestion);
    });
    
    dropdownContainer.appendChild(confirmButton);
    
    // Append to chat log
    ui.chatLog.appendChild(dropdownContainer);
    ui.chatLog.scrollTop = ui.chatLog.scrollHeight;
  }

  // Handle chart selection and check cache
  async function handleChartSelectionForAnalysis(chart, originalQuestion) {
    debugLog('Handling chart selection for analysis', {
      chart: chart,
      question: originalQuestion
    });
    
    // Update selection state
    const chartObject = typeof chart === 'string' ? 
      chartSelectionState.availableCharts.find(c => c.name === chart) : chart;
    
    chartSelectionState.selectedChart = chartObject;
    
    // Show status message
    const statusId = `chart-check-${Date.now()}`;
    appendMessage(`📊 Selected: ${chartObject.name || chart}`, 'bot status', null, statusId);
    
    // Check if cache exists for this chart
    try {
      const cacheCheckResponse = await proxyFetch(`${extensionState.backendUrl}/api/check-cache`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chart_name: chartObject.name || chart })
      });
      
      const cacheData = await cacheCheckResponse.json();
      debugLog('Cache check result', cacheData);
      
      if (cacheData.cache_exists) {
        // Cache exists, proceed with question immediately
        updateMessageById(statusId, `✓ ${chartObject.name || chart} - Using cached analysis`, 'bot status');
        
        // Small delay for user to see the status, then remove and proceed
        setTimeout(() => {
          const statusElement = document.getElementById(statusId);
          if (statusElement) {
            statusElement.remove();
          }
          resubmitQuestion(originalQuestion);
        }, 800);
      } else {
        // Cache doesn't exist, trigger silent AUTO_ANALYSIS first
        updateMessageById(statusId, `⏳ Preparing analysis for ${chartObject.name || chart}...`, 'bot status');
        await triggerSilentAutoAnalysis(chartObject, statusId);
        
        // After cache is created, remove status and proceed with original question
        setTimeout(() => {
          const statusElement = document.getElementById(statusId);
          if (statusElement) {
            statusElement.remove();
          }
          resubmitQuestion(originalQuestion);
        }, 800);
      }
      
    } catch (error) {
      debugLog('Error checking cache', error);
      updateMessageById(statusId, `❌ Error: ${error.message}`, 'bot error');
    }
  }

  // Trigger silent AUTO_ANALYSIS (cache-only mode)
  async function triggerSilentAutoAnalysis(chart, statusId) {
    debugLog('Triggering silent AUTO_ANALYSIS (cache-only)', {
      chart: chart.name || chart
    });
    
    try {
      const connectionKey = flaskConnectionState.connectionKey || flaskConnectionState.workbookName;
      
      const requestBody = {
        message: 'AUTO_ANALYSIS',
        context: extensionState.context,
        tableauReady: extensionState.ready,
        timestamp: new Date().toISOString(),
        selected_chart: chart.name || chart,
        chart_context: {
          chart_id: chart.id,
          chart_name: chart.name || chart,
          chart_type: chart.type || 'unknown'
        },
        connection_key: connectionKey,
        source: 'chrome_extension',
        auto_analysis: true,
        cache_only: true  // NEW FLAG - silent mode
      };
      
      debugLog('Sending silent AUTO_ANALYSIS request', requestBody);
      
      const response = await proxyFetch(`${extensionState.backendUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      const data = await response.json();
      debugLog('Silent AUTO_ANALYSIS completed', {
        success: data.success,
        cache_created: data.cache_created
      });
      
      if (data.success) {
        updateMessageById(statusId, `✓ Analysis prepared for ${chart.name || chart}`, 'bot status');
      } else {
        updateMessageById(statusId, `⚠️ Analysis preparation had issues`, 'bot status');
      }
      
    } catch (error) {
      debugLog('Silent AUTO_ANALYSIS error', error);
      updateMessageById(statusId, `❌ Failed to prepare analysis: ${error.message}`, 'bot error');
      throw error;
    }
  }

  // Resubmit the original question after chart is selected and cached
  async function resubmitQuestion(question) {
    debugLog('🚨🚨🚨 NEW CODE RUNNING - RESUBMIT WITH CHART 🚨🚨🚨', { 
      question, 
      selected_chart: chartSelectionState.selectedChart 
    });
    
    debugLog('Resubmitting original question with selected chart', { 
      question, 
      selected_chart: chartSelectionState.selectedChart 
    });
    
    // Don't show the question again in chat - it's already there
    // Directly call the backend with the selected chart
    
    try {
      // Extract chart name - selectedChart might be object or string
      const chartName = typeof chartSelectionState.selectedChart === 'string' 
        ? chartSelectionState.selectedChart 
        : chartSelectionState.selectedChart?.name;
      
      const requestData = {
        message: question,
        selected_chart: chartName, // Pass ONLY the chart NAME as string
        connection_key: flaskConnectionState.connectionKey,
        context: extensionState.context,
        source: 'chrome_extension'
      };
      
      debugLog('Sending request with selected chart', requestData);
      
      // Add new loading indicator (previous status messages should already be removed by caller)
      const loadingId = `loading-resubmit-${Date.now()}`;
      appendMessage('🔄 Processing your request...', 'bot status', null, loadingId);
      
      const response = await proxyFetch(`${extensionState.backendUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestData)
      });
      
      const data = await response.json();
      
      debugLog('Chat response with selected chart:', data);
      
      // Remove loading indicator
      const loadingElement = document.getElementById(loadingId);
      if (loadingElement) {
        loadingElement.remove();
      }
      
      // Check if still requires chart selection (shouldn't happen, but handle it)
      if (data.requires_chart_selection) {
        debugLog('Still requires chart selection - showing dropdown again');
        showChartSelectionDropdown(data.available_charts, question);
        return;
      }
      
      // Display the response (appendMessage will handle visualization if present)
      const messageType = data.error ? 'bot error' : 'bot';
      const messageText = data.reply || 'No response received';
      
      // Pass the full response data - appendMessage will automatically render visualization if present
      appendMessage(messageText, messageType, data);
      
      // IMPORTANT: Clear the selected chart so next analysis question shows dropdown
      chartSelectionState.selectedChart = null;
      debugLog('Cleared selected chart state for next analysis question');
      
    } catch (error) {
      debugLog('Error resubmitting question:', error);
      appendMessage(`❌ Error: ${error.message}`, 'bot error');
      
      // Clear selected chart on error too
      chartSelectionState.selectedChart = null;
    }
  }

  // Trigger automatic analysis when chart is selected
  async function triggerAutoAnalysis(chart) {
    debugLog(`Triggering auto-analysis for chart: ${chart.name}`);
    
    try {
      // Add loading indicator
      const analysisLoadingId = `analysis-loading-${Date.now()}`;
      appendMessage('🔍 Analyzing selected chart...', 'bot status', null, analysisLoadingId);
      
      // Check backend connection
      if (!flaskConnectionState.connected) {
        updateMessageById(analysisLoadingId, '❌ Backend not connected. Please refresh the page.', 'bot error');
        return;
      }
      
      const connectionKey = flaskConnectionState.connectionKey || flaskConnectionState.workbookName;
      if (!connectionKey) {
        updateMessageById(analysisLoadingId, '❌ No connection key available. Please refresh the page.', 'bot error');
        return;
      }
      
      const updateStatus = (message) => {
        updateMessageById(analysisLoadingId, `🔍 ${message}`);
      };
      
      updateStatus('Extracting most impacting columns...');
      
      // Prepare auto-analysis request
      const requestBody = {
        message: 'AUTO_ANALYSIS', // Special message to trigger auto-analysis
        context: extensionState.context,
        tableauReady: extensionState.ready,
        timestamp: new Date().toISOString(),
        selected_chart: chart.name,
        chart_context: {
          chart_id: chart.id,
          chart_name: chart.name,
          chart_type: chart.type || 'unknown'
        },
        connection_key: connectionKey,
        source: 'chrome_extension',
        auto_analysis: true // Flag to indicate this is auto-analysis
      };
      
      updateStatus('Requesting comprehensive analysis...');
      
      // Send auto-analysis request
      const response = await proxyFetch(`${extensionState.backendUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      const data = await response.json();
      debugLog('Auto-analysis response:', data);
      
      // Remove loading indicator
      updateMessageById(analysisLoadingId, '✅ Analysis complete!');
      
      setTimeout(() => {
        // Remove the loading message
        const loadingElement = document.getElementById(analysisLoadingId);
        if (loadingElement) {
          loadingElement.remove();
        }
        
        // Show auto-analysis results
        if (data.auto_analysis) {
          appendMessage('🎯 **Chart Analysis**', 'bot');
          appendMessage(data.auto_analysis, 'bot', data);
        } else if (data.reply) {
          appendMessage(data.reply, data.error ? 'bot error' : 'bot', data);
        } else {
          appendMessage('Analysis completed. Ready for your questions!', 'bot');
        }
      }, 500);
      
    } catch (error) {
      debugLog('Auto-analysis error:', error);
      updateMessageById(analysisLoadingId, `❌ Analysis failed: ${error.message}`, 'bot error');
    }
  }

  function navigateToChatPage() {
    debugLog('Navigating to chat page...');
    
    navigationState.currentPage = 'chat';
    
    // Hide chart selection container
    ui.chartButtonsContainer.style.display = 'none';
    
    // Clear chat log
    ui.chatLog.innerHTML = '';
    
    // Create chat interface header
    createChatInterface();
    
    // Enable input field
    if (ui.chatInput) {
      ui.chatInput.disabled = false;
      ui.chatInput.placeholder = `Ask questions about "${navigationState.selectedChart.name}"...`;
      ui.chatInput.focus();
    }
    
    // Submit button state is managed by input event listener
    
    debugLog('Navigation to chat page completed');
  }

  function createChatInterface() {
    debugLog('Creating chat interface with back button...');
    
    // Create chat header with back button
    const chatHeader = document.createElement('div');
    chatHeader.className = 'tableau-chat-page-header';
    
    // Create back button
    const backButton = document.createElement('button');
    backButton.type = 'button';
    backButton.innerHTML = '← Back';
    backButton.style.cssText = `
      background: transparent;
      border: 1px solid #dee2e6;
      border-radius: 6px;
      padding: 6px 12px;
      font-size: 12px;
      cursor: pointer;
      color: #495057;
      transition: all 0.2s ease;
    `;
    
    backButton.addEventListener('click', () => {
      navigateBackToChartSelection();
    });
    
    // Create chart name display
    const chartName = document.createElement('span');
    chartName.textContent = navigationState.selectedChart.name;
    chartName.style.cssText = 'font-size: 14px; color: #0b72e7;';
    
    chatHeader.appendChild(backButton);
    chatHeader.appendChild(chartName);
    
    // Insert header at the top of chat log
    ui.chatLog.parentNode.insertBefore(chatHeader, ui.chatLog);
  }

  function navigateBackToChartSelection() {
    debugLog('Navigating back to chart selection...');
    
    navigationState.currentPage = 'chart-selection';
    navigationState.selectedChart = null;
    chartSelectionState.selectedChart = null;
    
    // Remove chat header
    const chatHeader = document.querySelector('.tableau-chat-page-header');
    if (chatHeader) {
      chatHeader.remove();
    }
    
    // Show chart selection container
    ui.chartButtonsContainer.style.display = 'block';
    
    // Clear selection and disable input
    clearChartSelection();
    
    // Clear chat log
    ui.chatLog.innerHTML = '';
  }

  function clearChartSelection() {
    debugLog('Clearing chart selection');
    
    // Clear selection state
    chartSelectionState.selectedChart = null;
    
    // Reset all buttons
    document.querySelectorAll('.tableau-chart-selection-button').forEach(button => {
      button.classList.remove('selected');
    });
    
    // Keep input field enabled (chart selection is optional)
    if (ui.chatInput) {
      ui.chatInput.disabled = false;
      ui.chatInput.placeholder = 'Ask questions about your data (selecting a chart is optional)...';
      ui.chatInput.value = '';
    }
    
    // Note: submitBtn state is managed by input event listener, not manually disabled here
  }

  // ============================================================================
  // PHASE 3: TABLEAU FILTER CAPTURE
  // ============================================================================

  /**
   * Clean Tableau field name to extract the actual field name
   * Tableau uses internal format like "[federated.xxx][none:client:nk]"
   * We need to extract "client" from this
   */
  function cleanTableauFieldName(fieldName) {
    if (!fieldName) return '';

    try {
      // Pattern 1: [none|yr|mn|dy|qr|sum|avg|cnt:FIELDNAME:...]
      const match1 = fieldName.match(/\[(?:none|yr|mn|dy|qr|sum|avg|cnt):([^:]+):/);
      if (match1) {
        return match1[1].toLowerCase();
      }

      // Pattern 2: Last segment after colon
      const segments = fieldName.split(':');
      if (segments.length > 1) {
        const lastSegment = segments[segments.length - 1];
        return lastSegment.replace(/[\[\]]/g, '').toLowerCase();
      }

      // Fallback: remove all brackets
      return fieldName.replace(/[\[\]]/g, '').toLowerCase();
    } catch (error) {
      ContentLogger.warn('[FILTER_CAPTURE] Error cleaning field name', { fieldName, error: error.message });
      return fieldName.toLowerCase();
    }
  }

  /**
   * Capture active Tableau dashboard filters using Tableau JavaScript API
   * Returns structured filter data or null if filters cannot be captured
   */
  async function getActiveTableauFilters() {
    try {
      ContentLogger.debug('[FILTER_CAPTURE] Starting filter capture...');

      // Check if Tableau JavaScript API is available
      if (typeof tableau === 'undefined') {
        ContentLogger.debug('[FILTER_CAPTURE] Tableau API not available (window.tableau undefined)');
        return null;
      }

      // Check for VizManager (used in embedded views)
      if (!tableau.VizManager) {
        ContentLogger.debug('[FILTER_CAPTURE] Tableau VizManager not available, trying Extensions API...');

        // Try Extensions API as fallback (for dashboard extensions)
        if (tableau.extensions && tableau.extensions.dashboardContent) {
          const dashboard = tableau.extensions.dashboardContent.dashboard;
          if (dashboard && dashboard.worksheets && dashboard.worksheets.length > 0) {
            ContentLogger.debug('[FILTER_CAPTURE] Using Extensions API to get filters');
            const worksheet = dashboard.worksheets[0];

            const filters = await worksheet.getFiltersAsync();
            return parseFiltersFromExtensionsAPI(filters);
          }
        }

        ContentLogger.debug('[FILTER_CAPTURE] No supported Tableau API found');
        return null;
      }

      // Get viz instances
      const vizList = tableau.VizManager.getVizs();
      if (!vizList || vizList.length === 0) {
        ContentLogger.debug('[FILTER_CAPTURE] No Tableau viz found');
        return null;
      }

      const viz = vizList[0];
      const workbook = viz.getWorkbook();
      const activeSheet = workbook.getActiveSheet();

      ContentLogger.debug('[FILTER_CAPTURE] Found active sheet', {
        sheetName: activeSheet.getName(),
        sheetType: activeSheet.getSheetType()
      });

      // Get filters from the active sheet
      const filters = await activeSheet.getFiltersAsync();

      ContentLogger.debug('[FILTER_CAPTURE] Retrieved filters', {
        filterCount: filters ? filters.length : 0
      });

      if (!filters || filters.length === 0) {
        ContentLogger.debug('[FILTER_CAPTURE] No active filters found');
        return null;
      }

      // Parse filters into structured format
      const filterState = {};

      for (const filter of filters) {
        try {
          const fieldName = filter.getFieldName();
          const filterType = filter.getFilterType();

          // Clean field name (remove Tableau internal formatting)
          const cleanFieldName = cleanTableauFieldName(fieldName);

          let filterData = {
            type: filterType,
            field_name_raw: fieldName
          };

          // Get filter values based on type
          if (filterType === 'categorical') {
            filterData.values = filter.getAppliedValues().map(v => v.value);
            filterData.is_exclude = filter.getIsExcludeMode();

            ContentLogger.debug('[FILTER_CAPTURE] Categorical filter', {
              field: cleanFieldName,
              values: filterData.values,
              isExclude: filterData.is_exclude
            });

          } else if (filterType === 'range') {
            filterData.min = filter.getMin();
            filterData.max = filter.getMax();

            ContentLogger.debug('[FILTER_CAPTURE] Range filter', {
              field: cleanFieldName,
              min: filterData.min,
              max: filterData.max
            });

          } else if (filterType === 'relative-date') {
            filterData.period_type = filter.getPeriodType();
            filterData.range_n = filter.getRangeN();
            filterData.range_type = filter.getRangeType();

            ContentLogger.debug('[FILTER_CAPTURE] Relative date filter', {
              field: cleanFieldName,
              periodType: filterData.period_type,
              rangeN: filterData.range_n,
              rangeType: filterData.range_type
            });
          }

          filterState[cleanFieldName] = filterData;

        } catch (filterError) {
          ContentLogger.warn('[FILTER_CAPTURE] Error processing filter', {
            error: filterError.message
          });
        }
      }

      ContentLogger.info('[FILTER_CAPTURE] Successfully captured filters', {
        filterCount: Object.keys(filterState).length,
        fields: Object.keys(filterState)
      });

      return filterState;

    } catch (error) {
      ContentLogger.warn('[FILTER_CAPTURE] Error capturing filters', {
        error: error.message,
        stack: error.stack
      });
      return null;
    }
  }

  /**
   * Parse filters from Tableau Extensions API format
   * (Fallback when VizManager is not available)
   */
  function parseFiltersFromExtensionsAPI(filters) {
    if (!filters || filters.length === 0) return null;

    const filterState = {};

    for (const filter of filters) {
      try {
        const fieldName = filter.fieldName || filter.getFieldName();
        const cleanFieldName = cleanTableauFieldName(fieldName);

        filterState[cleanFieldName] = {
          type: filter.filterType || 'categorical',
          field_name_raw: fieldName,
          values: filter.appliedValues || [],
          is_exclude: false
        };
      } catch (error) {
        ContentLogger.warn('[FILTER_CAPTURE] Error parsing Extensions API filter', { error: error.message });
      }
    }

    return Object.keys(filterState).length > 0 ? filterState : null;
  }

  /**
   * PHASE 4: Detect filter mentions in user query using NLP patterns
   * @param {string} query - User's natural language query
   * @returns {Object} - Detected filter key-value pairs from query
   */
  function detectQueryFilters(query) {
    if (!query) return {};

    ContentLogger.debug('[QUERY_FILTER_DETECT] Analyzing query for filter mentions', { query });

    const queryFilters = {};
    const queryLower = query.toLowerCase();

    try {
      // Month detection
      const monthMatch = queryLower.match(/\b(?:in|for|during)\s+(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\b/i);
      if (monthMatch) {
        queryFilters.month = monthMatch[1];
        ContentLogger.debug('[QUERY_FILTER_DETECT] Detected month filter', { month: monthMatch[1] });
      }

      // Year detection
      const yearMatch = queryLower.match(/\b(?:in|for|year|during)\s+(20\d{2})\b/);
      if (yearMatch) {
        queryFilters.year = yearMatch[1];
        ContentLogger.debug('[QUERY_FILTER_DETECT] Detected year filter', { year: yearMatch[1] });
      }

      // Client detection - look for "for client", "client:", "client ="
      const clientMatch = query.match(/\b(?:for\s+client|client[:=]?)\s+([A-Za-z0-9\s\-_]+?)(?:\s+and|\s+in|\s+for|,|$)/i);
      if (clientMatch) {
        queryFilters.client = clientMatch[1].trim();
        ContentLogger.debug('[QUERY_FILTER_DETECT] Detected client filter', { client: clientMatch[1].trim() });
      }

      // Vendor detection
      const vendorMatch = query.match(/\b(?:vendor|from\s+vendor|vendor[:=]?)\s+([A-Za-z0-9\s\-_]+?)(?:\s+and|\s+in|\s+for|,|$)/i);
      if (vendorMatch) {
        queryFilters.vendor = vendorMatch[1].trim();
        ContentLogger.debug('[QUERY_FILTER_DETECT] Detected vendor filter', { vendor: vendorMatch[1].trim() });
      }

      // Domain detection
      const domainMatch = query.match(/\b(?:domain|in\s+domain|domain[:=]?)\s+([A-Za-z0-9\s\-_]+?)(?:\s+and|\s+in|\s+for|,|$)/i);
      if (domainMatch) {
        queryFilters.domain = domainMatch[1].trim();
        ContentLogger.debug('[QUERY_FILTER_DETECT] Detected domain filter', { domain: domainMatch[1].trim() });
      }

      ContentLogger.info('[QUERY_FILTER_DETECT] Query filter detection complete', {
        detectedCount: Object.keys(queryFilters).length,
        filters: queryFilters
      });

    } catch (error) {
      ContentLogger.warn('[QUERY_FILTER_DETECT] Error during filter detection', {
        error: error.message,
        query
      });
    }

    return queryFilters;
  }

  /**
   * PHASE 4: Detect conflicts between dashboard filters and query filters
   * @param {Object} dashboardFilters - Filters active in Tableau dashboard
   * @param {Object} queryFilters - Filters detected from user query
   * @returns {Array} - Array of conflict objects
   */
  function detectFilterConflicts(dashboardFilters, queryFilters) {
    const conflicts = [];

    if (!dashboardFilters || !queryFilters) return conflicts;

    for (const [field, queryValue] of Object.entries(queryFilters)) {
      if (dashboardFilters[field]) {
        const dashboardFilterData = dashboardFilters[field];

        // For categorical filters, check if values match
        if (dashboardFilterData.type === 'categorical') {
          const dashboardValues = dashboardFilterData.values || [];
          const queryValueLower = queryValue.toLowerCase();

          // Check if query value is in dashboard values
          const matchFound = dashboardValues.some(v =>
            v.toLowerCase().includes(queryValueLower) ||
            queryValueLower.includes(v.toLowerCase())
          );

          if (!matchFound) {
            conflicts.push({
              field: field,
              dashboardValue: dashboardValues.join(', '),
              queryValue: queryValue,
              type: 'value_mismatch'
            });
          }
        }
      }
    }

    if (conflicts.length > 0) {
      ContentLogger.info('[FILTER_CONFLICT] Detected conflicts between dashboard and query filters', {
        conflictCount: conflicts.length,
        conflicts
      });
    }

    return conflicts;
  }

  /**
   * PHASE 4: Show filter prompt UI modal to let user choose filter behavior
   * @param {string} query - User's query text
   * @param {Object} dashboardFilters - Active dashboard filters
   * @param {Object} queryFilters - Detected query filters
   * @param {Array} conflicts - Array of detected conflicts
   * @returns {Promise<Object>} - Resolves with user's choice { applyFilters: boolean }
   */
  function showFilterPromptModal(query, dashboardFilters, queryFilters, conflicts) {
    return new Promise((resolve) => {
      ContentLogger.debug('[FILTER_PROMPT] Showing filter prompt modal');

      // Create overlay
      const overlay = document.createElement('div');
      overlay.className = 'filter-prompt-overlay';
      overlay.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.6);
        z-index: 2147483650;
        display: flex;
        align-items: center;
        justify-content: center;
        animation: fadeIn 0.2s ease-out;
      `;

      // Create modal content
      const modal = document.createElement('div');
      modal.className = 'filter-prompt-modal';
      modal.style.cssText = `
        background: white;
        border-radius: 12px;
        padding: 24px;
        max-width: 600px;
        width: 90%;
        max-height: 80vh;
        overflow-y: auto;
        box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3);
        animation: slideUp 0.3s ease-out;
      `;

      // Header
      const header = document.createElement('div');
      header.style.cssText = `
        margin-bottom: 20px;
        padding-bottom: 16px;
        border-bottom: 2px solid #e2e8f0;
      `;

      const title = document.createElement('h3');
      title.textContent = '🔍 Dashboard Filters Detected';
      title.style.cssText = `
        margin: 0 0 8px 0;
        font-size: 20px;
        font-weight: 600;
        color: #1e293b;
      `;

      const subtitle = document.createElement('p');
      subtitle.textContent = 'Your dashboard has active filters. How should I process your query?';
      subtitle.style.cssText = `
        margin: 0;
        font-size: 14px;
        color: #64748b;
      `;

      header.appendChild(title);
      header.appendChild(subtitle);

      // Dashboard filters section
      const dashboardSection = document.createElement('div');
      dashboardSection.style.cssText = `
        margin-bottom: 16px;
        padding: 16px;
        background: #f8fafc;
        border-radius: 8px;
        border-left: 4px solid #3b82f6;
      `;

      const dashboardTitle = document.createElement('h4');
      dashboardTitle.textContent = '📊 Active Dashboard Filters:';
      dashboardTitle.style.cssText = `
        margin: 0 0 12px 0;
        font-size: 14px;
        font-weight: 600;
        color: #1e293b;
      `;
      dashboardSection.appendChild(dashboardTitle);

      const dashboardList = document.createElement('ul');
      dashboardList.style.cssText = `
        margin: 0;
        padding-left: 20px;
        list-style-type: none;
      `;

      for (const [field, filterData] of Object.entries(dashboardFilters)) {
        const li = document.createElement('li');
        li.style.cssText = `
          margin-bottom: 8px;
          font-size: 13px;
          color: #475569;
        `;

        let valueDisplay = '';
        if (filterData.type === 'categorical') {
          valueDisplay = filterData.values.join(', ');
          if (filterData.is_exclude) {
            valueDisplay = `NOT (${valueDisplay})`;
          }
        } else if (filterData.type === 'range') {
          valueDisplay = `${filterData.min || '∞'} to ${filterData.max || '∞'}`;
        }

        li.innerHTML = `<strong style="color: #1e293b;">${field}:</strong> <span style="color: #3b82f6;">${valueDisplay}</span>`;
        dashboardList.appendChild(li);
      }

      dashboardSection.appendChild(dashboardList);

      // Query section
      const querySection = document.createElement('div');
      querySection.style.cssText = `
        margin-bottom: 16px;
        padding: 16px;
        background: #fef3c7;
        border-radius: 8px;
        border-left: 4px solid #f59e0b;
      `;

      const queryTitle = document.createElement('h4');
      queryTitle.textContent = '💬 Your Query:';
      queryTitle.style.cssText = `
        margin: 0 0 8px 0;
        font-size: 14px;
        font-weight: 600;
        color: #1e293b;
      `;

      const queryText = document.createElement('p');
      queryText.textContent = `"${query}"`;
      queryText.style.cssText = `
        margin: 0;
        font-size: 13px;
        color: #475569;
        font-style: italic;
      `;

      querySection.appendChild(queryTitle);
      querySection.appendChild(queryText);

      // Query filters detected (if any)
      if (Object.keys(queryFilters).length > 0) {
        const queryFiltersTitle = document.createElement('p');
        queryFiltersTitle.textContent = 'Detected filters from your query:';
        queryFiltersTitle.style.cssText = `
          margin: 12px 0 8px 0;
          font-size: 13px;
          font-weight: 600;
          color: #92400e;
        `;
        querySection.appendChild(queryFiltersTitle);

        const queryFiltersList = document.createElement('ul');
        queryFiltersList.style.cssText = `
          margin: 0;
          padding-left: 20px;
          list-style-type: none;
        `;

        for (const [field, value] of Object.entries(queryFilters)) {
          const li = document.createElement('li');
          li.style.cssText = `
            margin-bottom: 4px;
            font-size: 13px;
            color: #78350f;
          `;
          li.innerHTML = `<strong>${field}:</strong> ${value}`;
          queryFiltersList.appendChild(li);
        }

        querySection.appendChild(queryFiltersList);
      }

      // Conflicts warning (if any)
      if (conflicts.length > 0) {
        const conflictSection = document.createElement('div');
        conflictSection.style.cssText = `
          margin-bottom: 16px;
          padding: 16px;
          background: #fee2e2;
          border-radius: 8px;
          border-left: 4px solid #ef4444;
        `;

        const conflictTitle = document.createElement('h4');
        conflictTitle.textContent = '⚠️ Conflicts Detected:';
        conflictTitle.style.cssText = `
          margin: 0 0 8px 0;
          font-size: 14px;
          font-weight: 600;
          color: #991b1b;
        `;

        const conflictText = document.createElement('p');
        conflictText.style.cssText = `
          margin: 0 0 8px 0;
          font-size: 13px;
          color: #7f1d1d;
        `;

        const conflictDetails = conflicts.map(c =>
          `<strong>${c.field}</strong>: Dashboard has "${c.dashboardValue}" but query asks for "${c.queryValue}"`
        ).join('<br>');

        conflictText.innerHTML = conflictDetails;

        const conflictNote = document.createElement('p');
        conflictNote.textContent = 'If you apply dashboard filters, both filters will be combined (may result in no data).';
        conflictNote.style.cssText = `
          margin: 8px 0 0 0;
          font-size: 12px;
          color: #991b1b;
          font-style: italic;
        `;

        conflictSection.appendChild(conflictTitle);
        conflictSection.appendChild(conflictText);
        conflictSection.appendChild(conflictNote);

        modal.appendChild(conflictSection);
      }

      // Options section
      const optionsSection = document.createElement('div');
      optionsSection.style.cssText = `
        margin-bottom: 20px;
      `;

      const optionsTitle = document.createElement('p');
      optionsTitle.textContent = 'How should I answer your query?';
      optionsTitle.style.cssText = `
        margin: 0 0 12px 0;
        font-size: 14px;
        font-weight: 600;
        color: #1e293b;
      `;

      const optionsContainer = document.createElement('div');
      optionsContainer.style.cssText = `
        display: flex;
        flex-direction: column;
        gap: 10px;
      `;

      // Option 1: Apply filters
      const applyOption = document.createElement('label');
      applyOption.style.cssText = `
        display: flex;
        align-items: start;
        padding: 12px;
        border: 2px solid #e2e8f0;
        border-radius: 8px;
        cursor: pointer;
        transition: all 0.2s ease;
      `;

      // 🆕 PHASE 5: Load saved filter preference from session
      let savedPreference = 'apply'; // Default to 'apply'
      try {
        const saved = sessionStorage.getItem('tableau_chatbot_filter_preference');
        if (saved === 'apply' || saved === 'ignore') {
          savedPreference = saved;
          ContentLogger.debug('[PHASE5] Loaded filter preference from session', {
            preference: savedPreference
          });
        }
      } catch (error) {
        ContentLogger.warn('[PHASE5] Could not load filter preference', {
          error: error.message
        });
      }

      const applyRadio = document.createElement('input');
      applyRadio.type = 'radio';
      applyRadio.name = 'filter-choice';
      applyRadio.value = 'apply';
      applyRadio.checked = (savedPreference === 'apply'); // 🆕 Use saved preference
      applyRadio.style.cssText = `
        margin-right: 12px;
        margin-top: 2px;
        cursor: pointer;
      `;

      const applyText = document.createElement('div');
      applyText.innerHTML = `
        <div style="font-weight: 600; color: #1e293b; margin-bottom: 4px;">✅ Apply dashboard filters</div>
        <div style="font-size: 13px; color: #64748b;">Show results that match both the dashboard filters and your query (filtered view)</div>
      `;

      applyOption.appendChild(applyRadio);
      applyOption.appendChild(applyText);

      applyOption.addEventListener('mouseenter', function() {
        this.style.borderColor = '#3b82f6';
        this.style.background = '#eff6ff';
      });

      applyOption.addEventListener('mouseleave', function() {
        this.style.borderColor = '#e2e8f0';
        this.style.background = 'white';
      });

      // Option 2: Ignore filters
      const ignoreOption = document.createElement('label');
      ignoreOption.style.cssText = `
        display: flex;
        align-items: start;
        padding: 12px;
        border: 2px solid #e2e8f0;
        border-radius: 8px;
        cursor: pointer;
        transition: all 0.2s ease;
      `;

      const ignoreRadio = document.createElement('input');
      ignoreRadio.type = 'radio';
      ignoreRadio.name = 'filter-choice';
      ignoreRadio.value = 'ignore';
      ignoreRadio.checked = (savedPreference === 'ignore'); // 🆕 Use saved preference
      ignoreRadio.style.cssText = `
        margin-right: 12px;
        margin-top: 2px;
        cursor: pointer;
      `;

      const ignoreText = document.createElement('div');
      ignoreText.innerHTML = `
        <div style="font-weight: 600; color: #1e293b; margin-bottom: 4px;">🌐 Ignore dashboard filters</div>
        <div style="font-size: 13px; color: #64748b;">Show total/unfiltered results based only on your query</div>
      `;

      ignoreOption.appendChild(ignoreRadio);
      ignoreOption.appendChild(ignoreText);

      ignoreOption.addEventListener('mouseenter', function() {
        this.style.borderColor = '#3b82f6';
        this.style.background = '#eff6ff';
      });

      ignoreOption.addEventListener('mouseleave', function() {
        this.style.borderColor = '#e2e8f0';
        this.style.background = 'white';
      });

      optionsContainer.appendChild(applyOption);
      optionsContainer.appendChild(ignoreOption);

      optionsSection.appendChild(optionsTitle);
      optionsSection.appendChild(optionsContainer);

      // Buttons
      const buttonsSection = document.createElement('div');
      buttonsSection.style.cssText = `
        display: flex;
        gap: 12px;
        justify-content: flex-end;
      `;

      const cancelButton = document.createElement('button');
      cancelButton.textContent = 'Cancel';
      cancelButton.type = 'button';
      cancelButton.style.cssText = `
        padding: 10px 20px;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        background: white;
        color: #475569;
        font-size: 14px;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.2s ease;
      `;

      cancelButton.addEventListener('mouseenter', function() {
        this.style.background = '#f1f5f9';
        this.style.borderColor = '#cbd5e1';
      });

      cancelButton.addEventListener('mouseleave', function() {
        this.style.background = 'white';
        this.style.borderColor = '#e2e8f0';
      });

      cancelButton.addEventListener('click', () => {
        overlay.style.animation = 'fadeOut 0.2s ease-out';
        setTimeout(() => {
          overlay.remove();
          resolve(null); // User cancelled
        }, 200);
      });

      const submitButton = document.createElement('button');
      submitButton.textContent = 'Submit Query';
      submitButton.type = 'button';
      submitButton.style.cssText = `
        padding: 10px 24px;
        border: none;
        border-radius: 6px;
        background: #3b82f6;
        color: white;
        font-size: 14px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.2s ease;
      `;

      submitButton.addEventListener('mouseenter', function() {
        this.style.background = '#2563eb';
      });

      submitButton.addEventListener('mouseleave', function() {
        this.style.background = '#3b82f6';
      });

      submitButton.addEventListener('click', () => {
        const selectedOption = modal.querySelector('input[name="filter-choice"]:checked');
        const applyFilters = selectedOption.value === 'apply';

        ContentLogger.info('[FILTER_PROMPT] User selected option', {
          choice: selectedOption.value,
          applyFilters
        });

        // 🆕 PHASE 5: Save user preference for this session
        try {
          sessionStorage.setItem('tableau_chatbot_filter_preference', selectedOption.value);
          ContentLogger.debug('[PHASE5] Saved filter preference to session', {
            preference: selectedOption.value
          });
        } catch (error) {
          ContentLogger.warn('[PHASE5] Could not save filter preference', {
            error: error.message
          });
        }

        overlay.style.animation = 'fadeOut 0.2s ease-out';
        setTimeout(() => {
          overlay.remove();
          resolve({ applyFilters });
        }, 200);
      });

      buttonsSection.appendChild(cancelButton);
      buttonsSection.appendChild(submitButton);

      // Assemble modal
      modal.appendChild(header);
      modal.appendChild(dashboardSection);
      modal.appendChild(querySection);
      modal.appendChild(optionsSection);
      modal.appendChild(buttonsSection);

      // Assemble overlay
      overlay.appendChild(modal);

      // Add to DOM
      document.body.appendChild(overlay);

      // Close on overlay click (but not modal click)
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
          cancelButton.click();
        }
      });

      // 🆕 PHASE 5: Add keyboard shortcuts
      const keyboardHandler = (e) => {
        switch(e.key) {
          case 'Escape':
            e.preventDefault();
            cancelButton.click();
            ContentLogger.debug('[PHASE5] Keyboard shortcut: Escape pressed - canceling');
            break;

          case 'Enter':
            e.preventDefault();
            submitButton.click();
            ContentLogger.debug('[PHASE5] Keyboard shortcut: Enter pressed - submitting');
            break;

          case ' ':
            // Toggle between options
            e.preventDefault();
            const currentlyChecked = modal.querySelector('input[name="filter-choice"]:checked');
            if (currentlyChecked.value === 'apply') {
              ignoreRadio.checked = true;
              ContentLogger.debug('[PHASE5] Keyboard shortcut: Space pressed - toggled to ignore');
            } else {
              applyRadio.checked = true;
              ContentLogger.debug('[PHASE5] Keyboard shortcut: Space pressed - toggled to apply');
            }
            break;
        }
      };

      document.addEventListener('keydown', keyboardHandler);

      // Remove keyboard handler when modal closes
      const originalRemove = overlay.remove;
      overlay.remove = function() {
        document.removeEventListener('keydown', keyboardHandler);
        ContentLogger.debug('[PHASE5] Keyboard shortcuts removed');
        originalRemove.call(this);
      };

      // Add CSS animations if not already present
      if (!document.getElementById('filter-prompt-animations')) {
        const style = document.createElement('style');
        style.id = 'filter-prompt-animations';
        style.textContent = `
          @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
          }
          @keyframes fadeOut {
            from { opacity: 1; }
            to { opacity: 0; }
          }
          @keyframes slideUp {
            from { transform: translateY(20px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
          }
        `;
        document.head.appendChild(style);
      }
    });
  }

  // Handle chat form submission
  async function handleChatSubmit(ev) {
    ev.preventDefault();
    const text = ui.chatInput.value.trim();
    if (!text) return;
    
    debugLog(`Processing chat message: "${text}"`);
    
    // Clear any previously selected chart - each question should start fresh
    // (This will be set again when resubmitting after chart selection)
    if (chartSelectionState.selectedChart) {
      debugLog('Clearing previous chart selection for new question');
      chartSelectionState.selectedChart = null;
    }
    
    // Check if a chart is selected (optional - chart selection is now optional)
    const selectedChart = chartSelectionState.selectedChart;
    
    appendMessage(text, 'user');
    ui.chatInput.value = '';
    
    // Add loading indicator
    const loadingId = `loading-${Date.now()}`;
    appendMessage('🔄 Processing your request...', 'bot status', null, loadingId);
    
    // Create a status updater function
    const updateStatus = (message) => {
      updateMessageById(loadingId, `🔄 ${message}`);
    };
    
    try {
      // Check if backend connection is ready
      if (!flaskConnectionState.connected) {
        debugLog('Backend not connected, initializing...');
        updateStatus('Initializing backend connection...');
        const connected = await initializeFlaskConnection();
        
        if (!connected) {
          updateMessageById(loadingId, 'Cannot process request - connection failed. Please try refreshing the page.', 'bot error');
          return;
        }
      }

      // Send message to backend
      debugLog('Sending chat message to backend...');
      updateStatus('Connecting to analytics backend...');
      
      // Make sure we have the right connection key
      const connectionKey = flaskConnectionState.connectionKey || flaskConnectionState.workbookName;
      
      if (!connectionKey) {
        ContentLogger.error('No connection key available for chat request', {
          flaskState: flaskConnectionState,
          selectedChart: selectedChart ? selectedChart.name : null
        }, 'CONNECTION_ERROR');
        updateMessageById(loadingId, 'Connection error: No connection key available. Please refresh the page.', 'bot error');
        return;
      }
      
      updateStatus('Preparing request for enhanced analysis...');

      // Log the chat request
      ContentLogger.logChatRequest(text, selectedChart ? selectedChart.name : null, connectionKey);

      // PHASE 3: Capture active Tableau dashboard filters
      let dashboardFilters = null;
      try {
        ContentLogger.debug('[FILTER_CAPTURE] Attempting to capture dashboard filters...');
        dashboardFilters = await getActiveTableauFilters();

        if (dashboardFilters && Object.keys(dashboardFilters).length > 0) {
          ContentLogger.info('[FILTER_CAPTURE] Dashboard filters captured successfully', {
            filterCount: Object.keys(dashboardFilters).length,
            filterFields: Object.keys(dashboardFilters)
          });
        } else {
          ContentLogger.debug('[FILTER_CAPTURE] No dashboard filters active');
        }
      } catch (filterError) {
        ContentLogger.warn('[FILTER_CAPTURE] Failed to capture filters, continuing without them', {
          error: filterError.message
        });
      }

      // PHASE 4: Detect query filters and show user prompt if dashboard filters exist
      let useDashboardFilters = false;
      let queryFilters = null;

      if (dashboardFilters && Object.keys(dashboardFilters).length > 0) {
        // Detect filters mentioned in the query
        queryFilters = detectQueryFilters(text);

        // Detect conflicts between dashboard and query filters
        const conflicts = detectFilterConflicts(dashboardFilters, queryFilters);

        // Show filter prompt modal to let user choose
        updateStatus('Dashboard filters detected - waiting for your choice...');

        try {
          const userChoice = await showFilterPromptModal(text, dashboardFilters, queryFilters, conflicts);

          if (userChoice === null) {
            // User cancelled - don't proceed with query
            ContentLogger.info('[FILTER_PROMPT] User cancelled query');
            updateMessageById(loadingId, 'Query cancelled.', 'bot status');
            return;
          }

          useDashboardFilters = userChoice.applyFilters;
          ContentLogger.info('[FILTER_PROMPT] User choice received', {
            applyFilters: useDashboardFilters,
            hasQueryFilters: queryFilters && Object.keys(queryFilters).length > 0,
            hasConflicts: conflicts.length > 0
          });

        } catch (promptError) {
          ContentLogger.warn('[FILTER_PROMPT] Error showing filter prompt, defaulting to ignore filters', {
            error: promptError.message
          });
          useDashboardFilters = false;
        }

        updateStatus('Preparing request with your filter preferences...');
      } else {
        ContentLogger.debug('[FILTER_PROMPT] No dashboard filters active, skipping prompt');
      }

      const requestBody = {
        message: text,
        context: extensionState.context,
        tableauReady: extensionState.ready,
        timestamp: new Date().toISOString(),
        selected_chart: selectedChart ? selectedChart.name : null,
        chart_context: selectedChart ? {
          chart_id: selectedChart.id,
          chart_name: selectedChart.name,
          chart_type: selectedChart.type || 'unknown'
        } : null,
        connection_key: connectionKey,
        source: 'chrome_extension',
        // PHASE 4: Include dashboard filter parameters based on user choice
        use_dashboard_filters: useDashboardFilters,
        dashboard_filters: dashboardFilters,
        query_filters: queryFilters
      };
      
      debugLog('Chat request body:', requestBody);
      
      const chatUrl = `${extensionState.backendUrl}/api/chat`;
      
      updateStatus('Sending request to AI analytics engine...');
      
      // Log the network request
      ContentLogger.logNetworkRequest('POST', chatUrl, null, requestBody);
      
      // Add progress indicator
      const startTime = Date.now();
      const progressInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        if (elapsed < 60) {
          updateStatus(`Processing query... (${elapsed}s) - Analyzing workbook data`);
        } else if (elapsed < 120) {
          updateStatus(`Processing query... (${elapsed}s) - Generating insights with AI`);
        } else if (elapsed < 180) {
          updateStatus(`Processing query... (${elapsed}s) - Creating visualizations`);
        } else {
          updateStatus(`Processing query... (${elapsed}s) - Finalizing analysis`);
        }
      }, 5000);
      
      const response = await proxyFetch(chatUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody)
      });
      
      clearInterval(progressInterval);

      // Log network response
      ContentLogger.logNetworkRequest('POST', chatUrl, response.status, requestBody, null, response.ok ? null : response.statusText);

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      debugLog('Chat response:', data);
      
      // DEBUG: Log chart_image length received from backend
      if (data.visualization && data.visualization.chart_image) {
        console.log('[VIZ DEBUG] Frontend received chart_image length:', data.visualization.chart_image.length, 'chars');
        console.log('[VIZ DEBUG] Frontend chart_image preview:', data.visualization.chart_image.substring(0, 100));
      } else {
        console.log('[VIZ DEBUG] Frontend - No chart_image in response');
      }
      
      // Log successful chat response
      ContentLogger.logChatResponse(data.reply || 'No response', !data.error, data.error ? 'Response contained error flag' : null);

      // Check if chart selection is required (for analysis intents)
      if (data.requires_chart_selection && data.available_charts) {
        debugLog('Chart selection required for analysis', {
          available_charts: data.available_charts.length
        });
        
        // Remove loading indicator
        const loadingElement = document.getElementById(loadingId);
        if (loadingElement) {
          loadingElement.remove();
        }
        
        // Store the original question to re-submit after chart selection
        const originalQuestion = text;
        
        // Show chart selection dropdown
        showChartSelectionDropdown(data.available_charts, originalQuestion);
        return;
      }

      if (data.requires_initialization) {
        debugLog('Backend requires reinitialization');
        appendMessage('Reinitializing connection...', 'bot status');
        
        // CRITICAL: Preserve connection key info during reset
        const previousConnectionKey = flaskConnectionState.connectionKey;
        const previousWorkbookName = flaskConnectionState.workbookName;
        const previousDashboardName = flaskConnectionState.dashboardName;
        
        ContentLogger.info('Preserving connection state during reinitialization', {
          previousConnectionKey,
          previousWorkbookName,
          previousDashboardName
        });
        
        flaskConnectionState.initialized = false;
        flaskConnectionState.connected = false;
        
        await initializeFlaskConnection();
        
        // Verify connection key was restored
        ContentLogger.info('Connection state after reinitialization', {
          connectionKey: flaskConnectionState.connectionKey,
          workbookName: flaskConnectionState.workbookName,
          restoredCorrectly: flaskConnectionState.connectionKey === previousConnectionKey
        });
        
        // Retry the request
        return handleChatSubmit(ev);
      }

      // Remove loading indicator and show result
      updateStatus('Analysis complete! ✅');
      
      // Small delay before showing results
      setTimeout(() => {
        // Remove the loading message
        const loadingElement = document.getElementById(loadingId);
        if (loadingElement) {
          loadingElement.remove();
        }
        
        // Handle enhanced response with potential visualization
        const messageType = data.error ? 'bot error' : 'bot';
        const messageText = data.reply || 'No response received';
        
        // Pass the full response data for visualization rendering
        appendMessage(messageText, messageType, data);
      }, 500);

    } catch (error) {
      debugLog('Chat error:', error);
      
      // Clear progress indicator and show error
      clearInterval(progressInterval);
      
      // Log chat error
      ContentLogger.logChatResponse(`Error: ${error.message}`, false, error.message);
      
      // Update loading message to show error
      updateMessageById(loadingId, `❌ Error: ${error.message}`, 'bot error');
      
      // Reset connection state on error
      if (error.message.includes('fetch')) {
        flaskConnectionState.connected = false;
        ContentLogger.logConnectionEvent('lost', false, {
          error: error.message,
          context: 'During chat request'
        });
        appendMessage('Lost connection to backend. Will attempt to reconnect on next message.', 'bot status');
      }
    }
  }

  // Monitor URL changes for auto-reload
  function monitorUrlChanges() {
    let currentUrl = window.location.href;
    let currentContext = extractTableauContext();
    
    setInterval(() => {
      const newUrl = window.location.href;
      if (newUrl !== currentUrl) {
        ContentLogger.info('URL changed, checking if workbook changed', {
          oldUrl: currentUrl,
          newUrl: newUrl
        });
        
        const newContext = extractTableauContext();
        
        // Check if workbook or dashboard changed
        if (currentContext.workbookName !== newContext.workbookName || 
            currentContext.dashboardName !== newContext.dashboardName) {
          
          ContentLogger.info('Workbook/Dashboard changed, reloading analytics', {
            oldWorkbook: currentContext.workbookName,
            newWorkbook: newContext.workbookName,
            oldDashboard: currentContext.dashboardName,
            newDashboard: newContext.dashboardName
          });
          
          // Reset connection state
          flaskConnectionState.initialized = false;
          flaskConnectionState.connected = false;
          chartSelectionState.chartsLoaded = false;
          chartSelectionState.availableCharts = [];
          
          // Clear UI and show reloading message
          if (ui && ui.chatLog) {
            ui.chatLog.innerHTML = '';
            appendMessage('Workbook changed - reloading analytics...', 'bot status');
          }
          
          // Clear chart selection
          if (ui && ui.chartButtonsContainer) {
            ui.chartButtonsContainer.innerHTML = '';
          }
          
          // Navigate back to chart selection if we were in chat mode
          if (navigationState.currentPage === 'chat') {
            navigateBackToChartSelection();
          }
          
          // Reinitialize if chat is visible
          if (ui && ui.chatbot.classList.contains('visible')) {
            setTimeout(() => {
              initializeFlaskConnection();
            }, 1000);
          }
          
          // Update current context
          currentContext = newContext;
        }
        
        // Update current URL
        currentUrl = newUrl;
        extensionState.currentUrl = newUrl;
      }
    }, 2000); // Check every 2 seconds
  }

  // Initialize the extension
  function initialize() {
    debugLog('Initializing Tableau Analysis Assistant extension');

    // Wait for page to be fully loaded
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', initialize);
      return;
    }

    // Check if we're in an iframe (to avoid conflicts with the original extension)
    if (window.self !== window.top) {
      debugLog('Running in iframe, skipping initialization');
      return;
    }

    // Initialize UI
    initializeUI();
    
    // Start URL monitoring for auto-reload
    monitorUrlChanges();
    
    // Show welcome message with personalized greeting
    if (!navigationState.welcomeShown) {
      showPersonalizedWelcomeMessage();
    }

    extensionState.ready = true;
    debugLog('Tableau Analysis Assistant initialized successfully');
  }

  /**
   * Show personalized welcome message after waiting for user API data
   * Falls back to generic message if API data not available within timeout
   */
  function showPersonalizedWelcomeMessage() {
    const maxWaitTime = 5000; // Wait up to 5 seconds for API
    const pollInterval = 200; // Check every 200ms
    const startTime = Date.now();
    
    debugLog('Starting to wait for getSessionInfo API for personalized greeting');
    
    const checkForUserData = () => {
      const firstName = getUserFirstName();
      
      if (firstName) {
        // Found first name - show personalized message
        debugLog('Showing personalized welcome message with first name:', firstName);
        if (ui && ui.chatLog) {
          appendMessage(`Hi ${firstName}, I am your Analytics Assistant.`, 'bot');
          navigationState.welcomeShown = true;
        }
        return true;
      }
      
      // Check if we've exceeded max wait time
      const elapsed = Date.now() - startTime;
      if (elapsed >= maxWaitTime) {
        // Timeout - show generic message
        debugLog('Timeout waiting for user data, showing generic welcome message');
        if (ui && ui.chatLog) {
          appendMessage('Hi, I am your Analytics Assistant.', 'bot');
          navigationState.welcomeShown = true;
        }
        return true;
      }
      
      // Keep polling
      setTimeout(checkForUserData, pollInterval);
      return false;
    };
    
    // Start polling after a short initial delay (let UI settle)
    setTimeout(checkForUserData, 1000);
  }

  // Start initialization
  initialize();

  // Expose debugging interface
  window.tableauAnalysisAssistant = {
    getState: () => ({
      extension: extensionState,
      flask: flaskConnectionState,
      navigation: navigationState,
      charts: chartSelectionState
    }),
    showChat: showChat,
    hideChat: hideChat,
    reinitialize: () => {
      flaskConnectionState.initialized = false;
      flaskConnectionState.connected = false;
      return initializeFlaskConnection();
    },
    debug: {
      appendMessage: appendMessage,
      clearChartSelection: clearChartSelection,
      loadCharts: loadAvailableCharts
    }
  };

  debugLog('Content script initialization complete');

})();
