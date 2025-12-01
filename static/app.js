(function() {
  // SINGLETON PROTECTION - Prevent multiple executions
  if (window.tableauChatbotInitialized) {
    console.log('[INFO] Tableau Chatbot already initialized, skipping duplicate initialization');
    return;
  }
  window.tableauChatbotInitialized = true;

  // Enhanced logging system
  const Logger = {
    config: {
      sendToBackend: true,
      logToConsole: true,
      backendEndpoint: '/api/log',
      source: 'static_app_js'
    },

    log: function(level, message, data = null) {
      const timestamp = new Date().toISOString();
      const logEntry = {
        timestamp: timestamp,
        level: level.toUpperCase(),
        message: message,
        data: data,
        source: this.config.source,
        url: window.location.href,
        userAgent: navigator.userAgent
      };

      // Console logging
      if (this.config.logToConsole) {
        const consoleMethod = level === 'error' ? 'error' : level === 'warn' ? 'warn' : 'log';
        const prefix = `[${timestamp}][${level.toUpperCase()}][${this.config.source}]`;
        
        if (data) {
          console[consoleMethod](prefix, message, data);
        } else {
          console[consoleMethod](prefix, message);
        }
      }

      // Send to backend
      if (this.config.sendToBackend) {
        this.sendToBackend(logEntry);
      }
    },

    debug: function(message, data = null) { this.log('debug', message, data); },
    info: function(message, data = null) { this.log('info', message, data); },
    warn: function(message, data = null) { this.log('warn', message, data); },
    error: function(message, data = null) { this.log('error', message, data); },

    sendToBackend: function(logEntry) {
      try {
        fetch(this.config.backendEndpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify(logEntry)
        }).catch(err => {
          // Fallback to console if backend logging fails
          console.warn('[LOGGER] Failed to send log to backend:', err);
        });
      } catch (error) {
        console.warn('[LOGGER] Failed to send log to backend:', error);
      }
    }
  };

  // Log initialization
  Logger.info('Tableau Chatbot JavaScript initialization started');

  // Configuration object to remove hardcoding
  const CONFIG = {
    debug: true,
    timeouts: {
      chartInteractionInit: 2000,
      chartLoadDelay: 1000,
      healthCheckInterval: 5 * 60 * 1000, // 5 minutes
      connectionHealthCheck: 60000 // 1 minute
    },
    ui: {
      maxChartButtons: 20,
      chartButtonHeight: 36,
      maxChartContainerHeight: 200,
      chatInputPlaceholder: {
        noChart: 'Select a chart first, then ask your question...',
        withChart: 'Ask questions about "{chartName}"...'
      }
    },
    messages: {
      welcome: 'Hi, I am your Analytics Assistant. Ask me about the current dashboard context.',
      connecting: 'Connecting to analytics backend...',
      noChartSelected: 'Please select a chart first using the buttons above.',
      connectionFailed: 'Connection failed. Please try refreshing the dashboard.',
      chartNotFound: 'Chart "{chartName}" not found in available worksheets.',
      noPermissions: 'Could not retrieve data for chart "{chartName}". Please check permissions.'
    }
  };

  const chat = document.getElementById('chatbot');
  const log = document.getElementById('chatlog');
  const form = document.getElementById('chatForm');
  const input = document.getElementById('chatInput');
  const minBtn = document.getElementById('minBtn');
  const launcher = document.getElementById('launcher');

  function debugLog(message, data = null) {
    Logger.debug(message, data);
    
    if (CONFIG.debug) {
      console.log(`[DEBUG] ${message}`, data || '');
    }
  }

  // ============================================================================
  // EXPORT STATUS POLLING
  // ============================================================================
  let exportProgressPoller = null;
  let currentConnectionKey = null;

  function pollExportStatus() {
    if (!currentConnectionKey) {
      stopExportStatusPolling();
      return;
    }
    
    fetch(`/api/export-status?connection_key=${encodeURIComponent(currentConnectionKey)}`)
      .then(response => response.json())
      .then(status => {
        if (status.in_progress) {
          showExportProgress(status);
          // Disable chat input while exporting
          const chatInput = document.getElementById('chatInput');
          const chatSubmit = document.querySelector('#chatForm button[type="submit"]');
          if (chatInput) chatInput.disabled = true;
          if (chatSubmit) chatSubmit.disabled = true;
        } else {
          // Export complete or not started
          if (status.stage === 'complete') {
            showExportProgress({
              ...status,
              message: status.message || 'Dashboard data ready! You can now ask questions.'
            });
            // Re-enable chat after brief delay
            setTimeout(() => {
              hideExportProgress();
              const chatInput = document.getElementById('chatInput');
              const chatSubmit = document.querySelector('#chatForm button[type="submit"]');
              if (chatInput) chatInput.disabled = false;
              if (chatSubmit) chatSubmit.disabled = false;
            }, 2000);
          } else if (status.stage === 'failed') {
            showExportProgress({
              ...status,
              message: status.message || 'Export failed. You may still ask questions.'
            });
            setTimeout(() => hideExportProgress(), 5000);
          } else {
            hideExportProgress();
          }
          
          // Stop polling if not in progress
          if (!status.in_progress) {
            stopExportStatusPolling();
          }
        }
      })
      .catch(err => {
        debugLog('Export status poll error:', err);
        // Don't show error to user, just stop polling
        stopExportStatusPolling();
      });
  }

  function showExportProgress(status) {
    const progressDiv = document.getElementById('export-progress');
    const stageSpan = document.getElementById('progress-stage');
    const messageDiv = document.getElementById('progress-message');
    const detailsDiv = document.getElementById('progress-details');
    
    if (!progressDiv || !stageSpan || !messageDiv) return;
    
    // Show progress indicator
    progressDiv.style.display = 'block';
    
    // Update stage
    const stageText = {
      'starting': '🚀 Starting',
      'downloading': '📥 Downloading',
      'parsing': '🔍 Parsing',
      'extracting': '📊 Extracting Data',
      'exporting': '💾 Exporting',
      'complete': '✅ Complete',
      'failed': '❌ Failed'
    }[status.stage] || status.stage;
    
    stageSpan.textContent = stageText;
    
    // Update message
    messageDiv.textContent = status.message || '';
    
    // Update details (datasources progress)
    let detailsText = '';
    if (status.datasources_processed > 0 || status.total_datasources > 0) {
      detailsText = `Data sources: ${status.datasources_processed}/${status.total_datasources}`;
      if (status.current_item) {
        detailsText += ` (${status.current_item})`;
      }
    }
    if (detailsDiv) {
      detailsDiv.textContent = detailsText;
    }
  }

  function hideExportProgress() {
    const progressDiv = document.getElementById('export-progress');
    if (progressDiv) {
      progressDiv.style.display = 'none';
    }
  }

  function startExportStatusPolling(connectionKey) {
    currentConnectionKey = connectionKey;
    
    // Stop any existing poller
    stopExportStatusPolling();
    
    debugLog('Starting export status polling', { connectionKey });
    
    // Start polling immediately
    pollExportStatus();
    
    // Then poll every 1 second
    exportProgressPoller = setInterval(pollExportStatus, 1000);
  }

  function stopExportStatusPolling() {
    if (exportProgressPoller) {
      clearInterval(exportProgressPoller);
      exportProgressPoller = null;
      debugLog('Stopped export status polling');
    }
  }

  // Cleanup on page unload
  window.addEventListener('beforeunload', stopExportStatusPolling);

  // Global connection state to prevent duplicates
  window.tableauConnectionState = window.tableauConnectionState || {
    initializing: false,
    initialized: false,
    connectionKey: null,
    lastInitTime: null
  };

  // Navigation state management
  let navigationState = {
    currentPage: 'chart-selection',
    selectedChart: null,
    welcomeShown: false
  };

  // Enhanced state management with initialization protection
  let tableauState = {
    ready: false,
    connected: false,
    context: {
      dashboardName: null,
      workbookName: null,
      worksheetNames: [],
      filters: {},
      selection: [],
      selectedData: null,
      activeWorksheet: null
    },
    connectionAge: null,
    lastCheck: null
  };

  let flaskConnectionState = {
    initialized: false,
    sessionId: null,
    connected: false,
    workbookName: null,
    dashboardName: null,
    connectionKey: null
  };

  let chartSelectionState = {
    availableCharts: [],
    selectedChart: null,
    chartsLoaded: false,
    loading: false,
    buttonsContainer: null
  };

  // Enhanced chart interaction handling with singleton protection
  let chartInteractionManager = {
    activeWorksheet: null,
    worksheetDataCache: {},
    clickOverlays: {},
    initializationAttempts: 0,
    initialized: false,
    
    async initializeChartInteractions() {
      Logger.info('=== INITIALIZING CHART INTERACTIONS ===');
      
      // Prevent duplicate initialization
      if (this.initialized) {
        Logger.info('Chart interactions already initialized, skipping');
        debugLog('Chart interactions already initialized, skipping');
        return true;
      }

      this.initializationAttempts++;
      Logger.info(`Chart interaction initialization attempt #${this.initializationAttempts}`);
      debugLog(`Chart interaction initialization attempt #${this.initializationAttempts}`);
      
      if (!tableauState.ready) {
        Logger.warn('Tableau not ready for chart interactions');
        debugLog('Tableau not ready for chart interactions');
        return false;
      }

      if (typeof tableau === 'undefined') {
        Logger.error('Tableau API not available');
        debugLog('Tableau API not available');
        return false;
      }

      if (!tableau.extensions || !tableau.extensions.dashboardContent) {
        Logger.error('Tableau dashboard content not available');
        debugLog('Tableau dashboard content not available');
        return false;
      }

      try {
        Logger.info('Accessing Tableau dashboard content');
        const dashboard = tableau.extensions.dashboardContent.dashboard;
        const worksheets = dashboard.worksheets;
        
        Logger.info(`Found ${worksheets.length} worksheets in dashboard "${dashboard.name}"`);
        debugLog(`Found ${worksheets.length} worksheets in dashboard "${dashboard.name}"`);
        
        if (worksheets.length === 0) {
          Logger.warn('No worksheets found in dashboard');
          return false;
        }
        
        let successCount = 0;
        
        for (const worksheet of worksheets) {
          try {
            const success = await this.setupWorksheetInteraction(worksheet);
            if (success) {
              successCount++;
            }
          } catch (error) {
            debugLog(`Failed to setup worksheet "${worksheet.name}":`, error);
          }
        }
        
        this.initialized = successCount > 0;
        debugLog(`Chart interactions setup: ${successCount} successful`);
        
        return this.initialized;
        
      } catch (error) {
        debugLog('Error initializing chart interactions:', error);
        return false;
      }
    },

    async setupWorksheetInteraction(worksheet) {
      debugLog(`Setting up interaction for worksheet: "${worksheet.name}"`);
      
      try {
        const dataTable = await worksheet.getSummaryDataAsync({
          maxRows: 10,
          ignoreAliases: false,
          includeAllColumns: true
        });
        
        if (!dataTable || !dataTable.data || dataTable.data.length === 0) {
          debugLog(`No data available for worksheet: "${worksheet.name}"`);
          return false;
        }
        
        // Cache worksheet data
        this.worksheetDataCache[worksheet.name] = {
          data: dataTable,
          columns: dataTable.columns.map(col => ({ 
            fieldName: col.fieldName, 
            dataType: col.dataType,
            index: col.index 
          })),
          lastUpdated: new Date().toISOString()
        };

        // Add event listeners with duplicate protection
        const eventKey = `${worksheet.name}_markSelection`;
        if (!this.clickOverlays[eventKey]) {
          worksheet.addEventListener(tableau.TableauEventType.MarkSelectionChanged, (event) => {
            this.handleMarkSelection(worksheet, event);
          });
          this.clickOverlays[eventKey] = true;
        }

        return true;
        
      } catch (error) {
        debugLog(`Failed to setup interaction for "${worksheet.name}":`, error);
        return false;
      }
    },

    async handleMarkSelection(worksheet, event) {
      debugLog(`Processing mark selection for: "${worksheet.name}"`);
      
      try {
        this.activeWorksheet = worksheet;
        const marksSelection = await event.getMarksAsync();
        
        if (!marksSelection.data || marksSelection.data.length === 0) {
          this.clearSelection();
          return;
        }
        
        const selectedData = this.extractSelectedData(marksSelection);
        if (selectedData.length === 0) {
          return;
        }
        
        const context = this.buildInteractionContext(worksheet, selectedData);
        
        // Update tableau state
        tableauState.context.selectedData = selectedData;
        tableauState.context.activeWorksheet = worksheet.name;
        
        await this.sendInteractionContext(context);
        
        const dataPoint = selectedData[0];
        const description = this.describeDataPoint(dataPoint);
        const message = `Selected: ${description} in "${worksheet.name}". What would you like to analyze?`;
        
        appendMsg(message, 'bot status');
        
      } catch (error) {
        debugLog('Error handling mark selection:', error);
        appendMsg(`Error processing chart selection: ${error.message}`, 'bot error');
      }
    },

    extractSelectedData(marksSelection) {
      const selectedData = [];
      
      try {
        if (!marksSelection.data) {
          return selectedData;
        }
        
        for (let i = 0; i < marksSelection.data.length; i++) {
          const mark = marksSelection.data[i];
          const dataPoint = {};
          
          if (mark.tupleId && Array.isArray(mark.tupleId)) {
            for (const tuple of mark.tupleId) {
              const column = marksSelection.columns.find(col => col.index === tuple.columnIndex);
              if (column && tuple.value !== null && tuple.value !== undefined) {
                dataPoint[column.fieldName] = tuple.value;
              }
            }
          }
          
          if (Object.keys(dataPoint).length === 0 && mark.pairs) {
            for (const pair of mark.pairs) {
              if (pair.fieldName && pair.value !== null && pair.value !== undefined) {
                dataPoint[pair.fieldName] = pair.value;
              }
            }
          }
          
          if (Object.keys(dataPoint).length > 0) {
            selectedData.push(dataPoint);
          }
        }
        
        return selectedData;
        
      } catch (error) {
        debugLog('Error extracting selected data:', error);
        return selectedData;
      }
    },

    buildInteractionContext(worksheet, selectedData) {
      return {
        type: 'chart_click',
        worksheet: worksheet.name,
        selectedData: selectedData,
        timestamp: new Date().toISOString(),
        columns: this.worksheetDataCache[worksheet.name]?.columns || [],
        totalDataPoints: this.worksheetDataCache[worksheet.name]?.data?.data?.length || 0
      };
    },

    async sendInteractionContext(context) {
      try {
        const response = await fetch('/api/chart/interaction', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            interaction: context,
            tableauContext: tableauState.context,
            connectionKey: this.getConnectionKey()
          })
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const result = await response.json();
        return result;
        
      } catch (error) {
        debugLog('Failed to send interaction context:', error);
        return { success: false, error: error.message };
      }
    },

    describeDataPoint(dataPoint) {
      const keys = Object.keys(dataPoint);
      if (keys.length === 0) return 'data point';
      
      const descriptions = keys.map(key => {
        const value = dataPoint[key];
        return `${key}: ${value}`;
      }).slice(0, 3);
      
      return descriptions.join(', ');
    },

    getConnectionKey() {
      return tableauState.context.workbookName || 
             tableauState.context.dashboardName || 
             flaskConnectionState.workbookName || 
             flaskConnectionState.connectionKey ||
             'default_workbook';
    },

    clearSelection() {
      this.activeWorksheet = null;
      tableauState.context.selectedData = null;
      tableauState.context.activeWorksheet = null;
    },

    getStatus() {
      return {
        initialized: this.initialized,
        worksheetCount: Object.keys(this.worksheetDataCache).length,
        worksheets: Object.keys(this.worksheetDataCache),
        activeWorksheet: this.activeWorksheet?.name || null,
        hasSelection: !!tableauState.context.selectedData,
        initializationAttempts: this.initializationAttempts
      };
    }
  };

  // Chart Selection Manager with duplicate prevention
  let chartSelectionManager = {
    container: null,
    buttons: [],
    chatContainer: null,
    initialized: false,

    async loadAvailableCharts() {
      if (this.loading) {
        debugLog('Charts already loading, skipping duplicate request');
        return false;
      }

      this.loading = true;
      debugLog('Loading available charts from backend...');
      
      try {
        if (!flaskConnectionState.connected) {
          debugLog('Flask backend not connected, cannot load charts');
          return false;
        }

        const connectionKey = this.getConnectionKey();
        debugLog('Using connection key for charts request', { connectionKey });
        
        const response = await fetch(`/api/get_worksheets?connection_key=${encodeURIComponent(connectionKey)}`);
        
        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(`HTTP ${response.status}: ${response.statusText} - ${errorText}`);
        }

        const data = await response.json();
        debugLog('Charts loaded from backend:', data);

        if (data.success && data.worksheets) {
          chartSelectionState.availableCharts = data.worksheets;
          chartSelectionState.chartsLoaded = true;
          
          this.createChartButtons();
          
          debugLog(`Successfully loaded ${data.worksheets.length} charts`);
          // COMMENTED OUT FOR SIMPLIFIED DISPLAY
          // appendMsg(`Found ${data.worksheets.length} charts available for analysis`, 'bot status');
          return true;
        } else {
          throw new Error(data.error || 'Failed to load charts');
        }

      } catch (error) {
        debugLog('Failed to load charts:', error);
        appendMsg(`Could not load chart information: ${error.message}`, 'bot error');
        return false;
      } finally {
        this.loading = false;
      }
    },

    getConnectionKey() {
      return flaskConnectionState.connectionKey || 
             flaskConnectionState.workbookName ||
             chartInteractionManager.getConnectionKey();
    },

    createChartButtons() {
      if (!this.container) {
        this.container = document.getElementById('chart-buttons-container');
        
        if (!this.container) {
          this.container = document.createElement('div');
          this.container.id = 'chart-buttons-container';
          this.container.style.cssText = `
            padding: 10px 12px;
            background: #f8f9fa;
            border-top: 1px solid rgba(0,0,0,0.08);
            max-height: ${CONFIG.ui.maxChartContainerHeight}px;
            overflow-y: auto;
          `;
          
          const chatForm = document.getElementById('chatForm');
          if (chatForm && chatForm.parentNode) {
            chatForm.parentNode.insertBefore(this.container, chatForm);
          }
        }
      }

      // Clear existing buttons
      this.container.innerHTML = '';
      this.buttons = [];

      if (chartSelectionState.availableCharts.length === 0) {
        this.container.innerHTML = '<div style="color: #666; font-size: 14px; text-align: center; padding: 10px;">No charts available</div>';
        return;
      }

      // Add header
      const header = document.createElement('div');
      header.style.cssText = 'font-size: 12px; font-weight: 600; color: #495057; margin-bottom: 8px; text-align: center;';
      header.textContent = `Select a Chart to Analyze (${Math.min(chartSelectionState.availableCharts.length, CONFIG.ui.maxChartButtons)} available)`;
      this.container.appendChild(header);

      // Create button grid
      const buttonGrid = document.createElement('div');
      buttonGrid.style.cssText = `
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
        gap: 6px;
        margin-top: 8px;
      `;

      // Limit number of charts to prevent UI overflow
      const chartsToShow = chartSelectionState.availableCharts.slice(0, CONFIG.ui.maxChartButtons);
      
      chartsToShow.forEach((chart, index) => {
        const button = this.createChartButton(chart, index);
        this.buttons.push(button);
        buttonGrid.appendChild(button);
      });

      this.container.appendChild(buttonGrid);
      debugLog(`Created ${this.buttons.length} chart buttons`);
    },

    createChartButton(chart, index) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'chart-selection-button';
      button.textContent = chart.name || `Chart ${index + 1}`;
      button.title = `Click to analyze: ${chart.name}`;
      
      button.style.cssText = `
        padding: 8px 12px;
        border: 1px solid #dee2e6;
        border-radius: 6px;
        background: white;
        color: #495057;
        font-size: 12px;
        cursor: pointer;
        transition: all 0.2s ease;
        text-align: center;
        word-wrap: break-word;
        min-height: ${CONFIG.ui.chartButtonHeight}px;
        display: flex;
        align-items: center;
        justify-content: center;
      `;

      // Add event listeners with duplicate protection
      const addHoverEffects = () => {
        button.addEventListener('mouseenter', () => {
          if (!button.classList.contains('selected')) {
            button.style.background = '#f8f9fa';
            button.style.borderColor = '#0b72e7';
            button.style.color = '#0b72e7';
          }
        });

        button.addEventListener('mouseleave', () => {
          if (!button.classList.contains('selected')) {
            button.style.background = 'white';
            button.style.borderColor = '#dee2e6';
            button.style.color = '#495057';
          }
        });

        button.addEventListener('click', () => {
          this.selectChart(chart, button);
        });
      };

      addHoverEffects();
      return button;
    },

    selectChart(chart, buttonElement) {
      debugLog('Chart selected:', chart);
      
      chartSelectionState.selectedChart = chart;
      navigationState.selectedChart = chart;
      
      this.navigateToChatPage();
      debugLog('Chart selection completed', { selected: chart.name });
    },

    navigateToChatPage() {
      navigationState.currentPage = 'chat';
      
      if (this.container) {
        this.container.style.display = 'none';
      }
      
      const chatLog = document.getElementById('chatlog');
      if (chatLog) {
        chatLog.innerHTML = '';
      }
      
      this.createChatInterface();
      
      // Enable input field with dynamic placeholder
      const chatInput = document.getElementById('chatInput');
      const submitButton = document.querySelector('#chatForm button[type="submit"]');
      
      if (chatInput && navigationState.selectedChart) {
        chatInput.disabled = false;
        chatInput.placeholder = CONFIG.ui.chatInputPlaceholder.withChart.replace('{chartName}', navigationState.selectedChart.name);
        chatInput.focus();
      }
      
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.style.background = '#0b72e7';
        submitButton.style.cursor = 'pointer';
      }
    },

    createChatInterface() {
      let chatHeader = document.querySelector('.chat-page-header');
      if (!chatHeader) {
        chatHeader = document.createElement('div');
        chatHeader.className = 'chat-page-header';
        chatHeader.style.cssText = `
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 10px 12px;
          background: #f8f9fa;
          border-bottom: 1px solid rgba(0,0,0,0.08);
          font-weight: 600;
          color: #495057;
        `;
        
        const chatLog = document.getElementById('chatlog');
        if (chatLog && chatLog.parentNode) {
          chatLog.parentNode.insertBefore(chatHeader, chatLog);
        }
      }
      
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
      
      backButton.addEventListener('mouseenter', () => {
        backButton.style.background = '#0b72e7';
        backButton.style.borderColor = '#0b72e7';
        backButton.style.color = 'white';
      });
      
      backButton.addEventListener('mouseleave', () => {
        backButton.style.background = 'transparent';
        backButton.style.borderColor = '#dee2e6';
        backButton.style.color = '#495057';
      });
      
      backButton.addEventListener('click', () => {
        this.navigateBackToChartSelection();
      });
      
      const chartName = document.createElement('span');
      chartName.textContent = navigationState.selectedChart?.name || 'Unknown Chart';
      chartName.style.cssText = 'font-size: 14px; color: #0b72e7;';
      
      chatHeader.innerHTML = '';
      chatHeader.appendChild(backButton);
      chatHeader.appendChild(chartName);
      
      this.chatContainer = chatHeader;
    },

    navigateBackToChartSelection() {
      navigationState.currentPage = 'chart-selection';
      navigationState.selectedChart = null;
      chartSelectionState.selectedChart = null;
      
      if (this.chatContainer) {
        this.chatContainer.style.display = 'none';
      }
      
      if (this.container) {
        this.container.style.display = 'block';
      }
      
      this.clearSelection();
      
      const chatLog = document.getElementById('chatlog');
      if (chatLog) {
        chatLog.innerHTML = '';
      }
    },

    clearSelection() {
      chartSelectionState.selectedChart = null;
      
      this.buttons.forEach(button => {
        button.classList.remove('selected');
        button.style.background = 'white';
        button.style.borderColor = '#dee2e6';
        button.style.color = '#495057';
        button.style.fontWeight = 'normal';
      });
      
      const chatInput = document.getElementById('chatInput');
      const submitButton = document.querySelector('#chatForm button[type="submit"]');
      
      if (chatInput) {
        chatInput.disabled = true;
        chatInput.placeholder = CONFIG.ui.chatInputPlaceholder.noChart;
        chatInput.value = '';
      }
      
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.style.background = '#ccc';
        submitButton.style.cursor = 'not-allowed';
      }
    },

    getSelectedChart() {
      return chartSelectionState.selectedChart;
    }
  };

  function updateConnectionStatus(status, message) {
    debugLog(`Connection Status: ${status} - ${message}`);
    
    if (status === 'connected') {
      appendMsg(`Connected to Tableau: ${message}`, 'bot status');
    } else if (status === 'error') {
      appendMsg(`Connection Error: ${message}`, 'bot error');
    }
  }

  function appendMsg(text, who) {
    const div = document.createElement('div');
    div.className = who + ' msg';
    
    if (who.includes('error')) {
      div.style.backgroundColor = '#fee2e2';
      div.style.borderLeft = '3px solid #ef4444';
    } else if (who.includes('status')) {
      div.style.backgroundColor = '#dbeafe';
      div.style.borderLeft = '3px solid #3b82f6';
    }
    
    if (who.includes('status') && text.includes('**')) {
      text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
      text = text.replace(/\n/g, '<br>');
      div.innerHTML = text;
    } else {
      div.textContent = text;
    }
    
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  function minimize(toMin) {
    debugLog(`Minimize called with: ${toMin}`);
    
    if (toMin) { 
      if (chat) chat.hidden = true;
      if (launcher) {
        launcher.hidden = false;
        setTimeout(() => {
          forceBottomRightPosition();
        }, 10);
      }
    } else { 
      if (chat) chat.hidden = false;
      if (launcher) launcher.hidden = true;
      const input = document.getElementById('chatInput');
      if (input) input.focus();
      
      // Initialize connections when chat is opened (with duplicate protection)
      if (!flaskConnectionState.initialized && !window.tableauConnectionState.initializing) {
        initializeFlaskConnection();
      }
    }
    updatePosition();
  }

  // Enhanced Tableau Extensions API initialization with singleton protection
  async function initTableau() {
    if (tableauState.ready) {
      debugLog('Tableau already initialized, skipping');
      return;
    }

    debugLog('Starting Tableau Extensions API initialization...');
    
    if (navigationState.currentPage === 'chart-selection' && !navigationState.welcomeShown) {
      appendMsg(CONFIG.messages.welcome, 'bot');
      navigationState.welcomeShown = true;
    }
    
    try {
      if (typeof tableau === 'undefined') {
        debugLog('Tableau Extensions API not available - running in standalone mode');
        tableauState.ready = true;
        return;
      }

      debugLog('Tableau API found, initializing...');
      await tableau.extensions.initializeAsync();
      debugLog('Tableau Extensions API initialized successfully');
      
      const dashboard = tableau.extensions.dashboardContent.dashboard;
      
      // Enhanced context extraction
      tableauState.context.dashboardName = dashboard.name;
      
      const worksheets = dashboard.worksheets;
      tableauState.context.worksheetNames = worksheets.map(ws => ws.name);
      
      // Extract workbook name from URL or dashboard object
      try {
        const url = window.location.href;
        const workbookMatch = url.match(/workbook\/([^\/\?]+)/);
        if (workbookMatch) {
          tableauState.context.workbookName = decodeURIComponent(workbookMatch[1]);
        }
        
        if (!tableauState.context.workbookName && dashboard.workbook) {
          tableauState.context.workbookName = dashboard.workbook.name;
        }
      } catch (e) {
        debugLog('Could not extract workbook name:', e);
      }

      // Set up event listeners with duplicate protection
      worksheets.forEach(ws => {
        const filterEventKey = `${ws.name}_filter`;
        const selectionEventKey = `${ws.name}_selection`;
        
        if (!chartInteractionManager.clickOverlays[filterEventKey]) {
          ws.addEventListener(tableau.TableauEventType.FilterChanged, async (e) => {
            try {
              const filters = await ws.getFiltersAsync();
              tableauState.context.filters[ws.name] = filters.map(f => ({ 
                name: f.fieldName, 
                type: f.filterType 
              }));
            } catch (err) {
              debugLog(`Error handling filter change in ${ws.name}:`, err);
            }
          });
          chartInteractionManager.clickOverlays[filterEventKey] = true;
        }

        if (!chartInteractionManager.clickOverlays[selectionEventKey]) {
          ws.addEventListener(tableau.TableauEventType.MarkSelectionChanged, async (e) => {
            try {
              const marks = await e.getMarksAsync();
              tableauState.context.selection = marks.data || [];
            } catch (err) {
              debugLog(`Error handling selection change in ${ws.name}:`, err);
            }
          });
          chartInteractionManager.clickOverlays[selectionEventKey] = true;
        }
      });

      tableauState.ready = true;
      tableauState.connected = true;

      // Initialize chart interactions with delay
      setTimeout(async () => {
        await chartInteractionManager.initializeChartInteractions();
      }, CONFIG.timeouts.chartInteractionInit);

    } catch (err) {
      debugLog('Tableau initialization error:', err);
      tableauState.ready = true;
      
      if (err.message && !err.message.includes('not supported')) {
        updateConnectionStatus('error', `Tableau init failed: ${err.message}`);
      }
    }
  }

  // Initialize Flask backend connection with duplicate protection
  async function initializeFlaskConnection() {
    Logger.info('=== INITIALIZING FLASK CONNECTION ===');
    
    // Prevent duplicate initialization
    if (window.tableauConnectionState.initializing || window.tableauConnectionState.initialized) {
      Logger.info('Flask connection already initializing/initialized, skipping duplicate');
      debugLog('Flask connection already initializing/initialized, skipping duplicate');
      return window.tableauConnectionState.initialized;
    }

    // Check if initialization was attempted recently (within 5 seconds)
    const now = Date.now();
    if (window.tableauConnectionState.lastInitTime && (now - window.tableauConnectionState.lastInitTime) < 5000) {
      debugLog('Flask initialization attempted too recently, skipping');
      return false;
    }

    window.tableauConnectionState.initializing = true;
    window.tableauConnectionState.lastInitTime = now;
    
    debugLog('Starting Flask backend connection initialization...');

    try {
      debugLog('Initializing Flask backend connection...');
      appendMsg(CONFIG.messages.connecting, 'bot');

      // Wait for Tableau to be ready
      if (!tableauState.ready) {
        debugLog('Waiting for Tableau initialization...');
        await new Promise(resolve => {
          const checkTableau = () => {
            if (tableauState.ready) {
              resolve();
            } else {
              setTimeout(checkTableau, 100);
            }
          };
          checkTableau();
        });
      }

      // Initialize Tableau connection on backend
      debugLog('Sending initialization request to Flask backend...');
      const response = await fetch('/api/tableau/initialize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          tableauContext: tableauState.context,
          clientTimestamp: new Date().toISOString()
        })
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

        const cacheStatus = result.cached ? ' (cached)' : ' (new connection)';
        const message = `Connected to workbook: ${result.workbook_name}${cacheStatus}`;
        
        appendMsg(message, 'bot');
        
        if (result.available_views) {
          appendMsg(`Found ${result.available_views} worksheets available for analysis.`, 'bot');
        }

        window.tableauConnectionState.initialized = true;
        updateConnectionStatus('connected', message);
        
        // Start export status polling immediately
        if (flaskConnectionState.connectionKey) {
          debugLog('Starting export status polling for connection:', flaskConnectionState.connectionKey);
          startExportStatusPolling(flaskConnectionState.connectionKey);
        }
        
        // Load available charts after backend initialization
        setTimeout(async () => {
          const chartsLoaded = await chartSelectionManager.loadAvailableCharts();
          if (chartsLoaded) {
            // Initially disable input until chart is selected
            const chatInput = document.getElementById('chatInput');
            const submitButton = form.querySelector('button[type="submit"]');
            
            if (chatInput) {
              chatInput.disabled = true;
              chatInput.placeholder = CONFIG.ui.chatInputPlaceholder.noChart;
            }
            
            if (submitButton) {
              submitButton.disabled = true;
              submitButton.style.background = '#ccc';
              submitButton.style.cursor = 'not-allowed';
            }
          }
        }, CONFIG.timeouts.chartLoadDelay);
        
        return true;
      } else {
        throw new Error(result.error || 'Unknown initialization error');
      }

    } catch (error) {
      debugLog('Flask initialization error:', error);
      
      const errorMessage = `Connection failed: ${error.message}`;
      appendMsg(errorMessage, 'bot error');
      updateConnectionStatus('error', errorMessage);
      
      return false;
    } finally {
      window.tableauConnectionState.initializing = false;
    }
  }

  async function checkConnectionHealth() {
    try {
      const response = await fetch('/api/state');
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      const state = await response.json();
      
      if (state.connected) {
        flaskConnectionState.connected = true;
        tableauState.connectionAge = state.connection_age;
        return true;
      } else {
        flaskConnectionState.connected = false;
        return false;
      }
    } catch (error) {
      debugLog('Health check failed:', error);
      flaskConnectionState.connected = false;
      return false;
    }
  }

  // Enhanced chat form submission with duplicate protection
  if (form) {
    // Remove any existing listeners to prevent duplicates
    form.removeEventListener('submit', handleFormSubmit);
    form.addEventListener('submit', handleFormSubmit);
    
    async function handleFormSubmit(ev) {
      ev.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      
      Logger.info('=== CHAT MESSAGE SUBMITTED ===');
      Logger.info(`Message: "${text}"`);
      
      debugLog(`Processing chat message: "${text}"`);
      
      const selectedChart = chartSelectionManager.getSelectedChart();
      Logger.info(`Selected chart: ${selectedChart ? selectedChart.name : 'none'}`);
      
      if (!selectedChart) {
        Logger.warn('No chart selected for chat message');
        appendMsg(CONFIG.messages.noChartSelected, 'bot error');
        return;
      }
      
      appendMsg(text, 'user');
      input.value = '';
      
      try {
        if (!flaskConnectionState.connected) {
          const connected = await initializeFlaskConnection();
          if (!connected) {
            appendMsg(CONFIG.messages.connectionFailed, 'bot error');
            return;
          }
        }

        // Periodic health check
        if (!tableauState.lastCheck || (Date.now() - tableauState.lastCheck) > CONFIG.timeouts.connectionHealthCheck) {
          const healthy = await checkConnectionHealth();
          tableauState.lastCheck = Date.now();
          
          if (!healthy) {
            appendMsg('Connection appears stale, please refresh your dashboard.', 'bot error');
            return;
          }
        }

        const requestBody = { 
          message: text, 
          context: tableauState.context,
          tableauReady: tableauState.ready && tableauState.connected,
          timestamp: new Date().toISOString(),
          selected_chart: selectedChart.name,
          chart_context: {
            chart_id: selectedChart.id,
            chart_name: selectedChart.name,
            chart_type: selectedChart.type || 'unknown'
          },
          chartInteraction: {
            hasSelection: !!tableauState.context.selectedData,
            activeWorksheet: tableauState.context.activeWorksheet,
            selectedData: tableauState.context.selectedData
          }
        };
        
        const response = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(requestBody)
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.requires_initialization) {
          flaskConnectionState.initialized = false;
          flaskConnectionState.connected = false;
          window.tableauConnectionState.initialized = false;
          await initializeFlaskConnection();
          return;
        }

        appendMsg(data.reply || 'No response received', data.error ? 'bot error' : 'bot');

      } catch (error) {
        debugLog('Chat error:', error);
        appendMsg(`Error: ${error.message}`, 'bot error');
        
        if (error.message.includes('fetch')) {
          flaskConnectionState.connected = false;
          appendMsg('Lost connection to backend. Will attempt to reconnect on next message.', 'bot status');
        }
      }
    }
  }

  // Position management functions
  function getParentScroll() {
    try {
      let parentScrollX = 0, parentScrollY = 0;
      
      if (window.parent && window.parent !== window) {
        parentScrollX = window.parent.scrollX || window.parent.pageXOffset || 0;
        parentScrollY = window.parent.scrollY || window.parent.pageYOffset || 0;
        
        if (parentScrollX === 0 && parentScrollY === 0 && window.parent.document) {
          parentScrollX = window.parent.document.documentElement.scrollLeft || window.parent.document.body.scrollLeft || 0;
          parentScrollY = window.parent.document.documentElement.scrollTop || window.parent.document.body.scrollTop || 0;
        }
      }
      
      return { x: parentScrollX, y: parentScrollY, accessible: true };
    } catch (e) {
      return { x: 0, y: 0, accessible: false };
    }
  }

  function updatePosition() {
    const parentScroll = getParentScroll();
    const scrollX = parentScroll.accessible ? parentScroll.x : (window.scrollX || window.pageXOffset || 0);
    const scrollY = parentScroll.accessible ? parentScroll.y : (window.scrollY || window.pageYOffset || 0);
    
    if (launcher) {
      launcher.style.setProperty('position', 'fixed', 'important');
      launcher.style.setProperty('right', '20px', 'important');
      launcher.style.setProperty('bottom', '20px', 'important');
      launcher.style.setProperty('left', 'auto', 'important');
      launcher.style.setProperty('top', 'auto', 'important');
      launcher.style.setProperty('transform', `translate(${scrollX}px, ${scrollY}px)`, 'important');
      launcher.style.setProperty('z-index', '999999', 'important');
    }
    
    if (chat && !chat.hidden) {
      chat.style.setProperty('position', 'fixed', 'important');
      chat.style.setProperty('right', '20px', 'important');
      chat.style.setProperty('bottom', '20px', 'important');
      chat.style.setProperty('left', 'auto', 'important');
      chat.style.setProperty('top', 'auto', 'important');
      chat.style.setProperty('transform', `translate(${scrollX}px, ${scrollY}px)`, 'important');
      chat.style.setProperty('z-index', '999999', 'important');
    }
  }

  function forceBottomRightPosition() {
    if (launcher) {
      launcher.style.cssText = `
        position: fixed !important;
        right: 20px !important;
        bottom: 20px !important;
        left: auto !important;
        top: auto !important;
        width: 180px !important;
        height: 48px !important;
        border-radius: 24px !important;
        border: none !important;
        background: #1f6feb !important;
        color: #fff !important;
        font-size: 16px !important;
        font-weight: 600 !important;
        box-shadow: 0 10px 24px rgba(0,0,0,.24) !important;
        z-index: 999999 !important;
        pointer-events: auto !important;
        cursor: pointer !important;
        transition: all 0.3s ease !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        gap: 8px !important;
      `;
    }
    
    if (chat && !chat.hidden) {
      chat.style.cssText = `
        position: fixed !important;
        right: 20px !important;
        bottom: 20px !important;
        left: auto !important;
        top: auto !important;
        width: 360px !important;
        max-width: calc(100vw - 40px) !important;
        height: 520px !important;
        max-height: calc(100vh - 40px) !important;
        border-radius: 16px !important;
        background: #ffffff !important;
        box-shadow: 0 12px 36px rgba(0,0,0,.25) !important;
        display: flex !important;
        flex-direction: column !important;
        overflow: hidden !important;
        z-index: 999999 !important;
        pointer-events: auto !important;
        border: 1px solid rgba(0,0,0,.06) !important;
      `;
    }
    
    updatePosition();
  }

  function initializeState() {
    if (chat && launcher) {
      chat.hidden = true;
      launcher.hidden = false;
      
      launcher.style.setProperty('position', 'fixed', 'important');
      launcher.style.setProperty('right', '20px', 'important');
      launcher.style.setProperty('bottom', '20px', 'important');
      launcher.style.setProperty('left', 'auto', 'important');
      launcher.style.setProperty('top', 'auto', 'important');
      launcher.style.setProperty('z-index', '999999', 'important');
      launcher.style.setProperty('pointer-events', 'auto', 'important');
      
      // Remove any existing click handlers to prevent duplicates
      launcher.removeEventListener('click', launcherClickHandler);
      launcher.addEventListener('click', launcherClickHandler);
      
      updatePosition();
    }
  }

  function launcherClickHandler() {
    debugLog('Launcher clicked');
    minimize(false);
  }

  // Add event listeners with duplicate protection
  if (minBtn) {
    minBtn.removeEventListener('click', minimizeClickHandler);
    minBtn.addEventListener('click', minimizeClickHandler);
  }

  function minimizeClickHandler() {
    debugLog('Minimize button clicked');
    minimize(true);
  }

  // Initialize when DOM is ready (with duplicate protection)
  if (document.readyState !== 'loading') {
    initializeState();
  } else {
    document.addEventListener('DOMContentLoaded', initializeState);
  }

  // Position update listeners
  window.addEventListener('scroll', updatePosition, { passive: true });
  window.addEventListener('resize', updatePosition, { passive: true });
  setInterval(updatePosition, 100);

  // Periodic connection health check
  setInterval(async () => {
    if (flaskConnectionState.connected && !chat.hidden) {
      await checkConnectionHealth();
    }
  }, CONFIG.timeouts.healthCheckInterval);

  // Initialize Tableau on load (with duplicate protection)
  initTableau();

  // Global API for debugging
  window.tableauChatbot = {
    getTableauState: () => tableauState,
    getFlaskState: () => flaskConnectionState,
    getChartState: () => chartSelectionState,
    checkHealth: checkConnectionHealth,
    reinitialize: async () => {
      window.tableauConnectionState.initialized = false;
      window.tableauConnectionState.initializing = false;
      flaskConnectionState.initialized = false;
      flaskConnectionState.connected = false;
      return await initializeFlaskConnection();
    },
    forcePosition: forceBottomRightPosition,
    config: CONFIG
  };

  // Emergency positioning fix
  setTimeout(() => {
    forceBottomRightPosition();
    
    if (launcher) {
      launcher.onclick = () => {
        minimize(false);
      };
    }
  }, 1000);

  // Keep fixing position every 2 seconds if needed
  setInterval(() => {
    if (launcher && !launcher.hidden) {
      const rect = launcher.getBoundingClientRect();
      if (rect.right < window.innerWidth - 100 || rect.bottom < window.innerHeight - 100) {
        forceBottomRightPosition();
      }
    }
  }, 2000);

})();