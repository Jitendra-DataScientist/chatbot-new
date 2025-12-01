// Tableau Network Request Interceptor
// This script runs in the main page context (not content script context)
// It intercepts fetch and XMLHttpRequest to capture Tableau API responses

(function() {
  'use strict';

  console.log('[Network Interceptor] Initializing Tableau API request interceptor...');

  // Configuration
  const CONFIG = {
    // Maximum size of response body to capture (100KB)
    MAX_RESPONSE_SIZE: 100 * 1024,
    
    // Maximum number of requests to capture per session
    MAX_REQUESTS: 50,
    
    // Tableau API endpoint patterns to capture
    CAPTURE_PATTERNS: [
      'vizportal/api/web/v1/getSessionInfo',  // No leading slash - Tableau uses relative URLs
      'vizportal/api/web/v1/getUserInfo',
      '/api/3.9/sites/',
      '/api/3.9/users/me',
      '/api/*/currentUser',
      '/api/*/sessions/current',
      '/api/*/users/',
      '/rest/api/3.9/users/'
    ],
    
    // Headers to redact for security
    REDACT_HEADERS: [
      'authorization',
      'cookie',
      'set-cookie',
      'x-csrf-token',
      'x-xsrf-token'
    ]
  };

  // State management
  let capturedRequests = [];
  let requestCounter = 0;

  /**
   * Check if a URL should be captured based on patterns
   */
  function shouldCaptureRequest(url) {
    if (!url) return false;
    
    return CONFIG.CAPTURE_PATTERNS.some(pattern => {
      // Simple pattern matching - check if URL contains the pattern
      return url.toLowerCase().includes(pattern.toLowerCase());
    });
  }

  /**
   * Sanitize headers by redacting sensitive values
   */
  function sanitizeHeaders(headers) {
    if (!headers) return {};
    
    const sanitized = {};
    for (const [key, value] of Object.entries(headers)) {
      const lowerKey = key.toLowerCase();
      if (CONFIG.REDACT_HEADERS.includes(lowerKey)) {
        sanitized[key] = '[REDACTED]';
      } else {
        sanitized[key] = value;
      }
    }
    return sanitized;
  }

  /**
   * Truncate large response bodies
   */
  function truncateResponseBody(body, url) {
    const bodyStr = JSON.stringify(body);
    if (bodyStr.length > CONFIG.MAX_RESPONSE_SIZE) {
      console.warn(`[Network Interceptor] Response body too large (${bodyStr.length} bytes), truncating for: ${url}`);
      return {
        _truncated: true,
        _original_size: bodyStr.length,
        _message: 'Response body exceeded size limit and was truncated',
        _partial_data: bodyStr.substring(0, CONFIG.MAX_RESPONSE_SIZE)
      };
    }
    return body;
  }

  /**
   * Send captured request data to content script
   */
  function postToContentScript(requestData) {
    try {
      window.postMessage({
        type: 'TABLEAU_API_INTERCEPTED',
        source: 'network-interceptor',
        payload: requestData
      }, '*');
      
      console.log(`[Network Interceptor] Posted request data to content script:`, {
        url: requestData.url,
        status: requestData.status,
        method: requestData.method
      });
    } catch (error) {
      console.error('[Network Interceptor] Failed to post message to content script:', error);
    }
  }

  /**
   * Process and capture a network response
   */
  function captureResponse(url, method, status, responseHeaders, responseBody, startTime) {
    try {
      // Check if we should capture this request
      if (!shouldCaptureRequest(url)) {
        return;
      }

      // Check request limit
      if (capturedRequests.length >= CONFIG.MAX_REQUESTS) {
        console.warn('[Network Interceptor] Maximum request limit reached, skipping capture');
        return;
      }

      requestCounter++;
      const endTime = Date.now();
      const duration = endTime - startTime;

      console.log(`[Network Interceptor] Capturing request #${requestCounter}: ${method} ${url} (${status})`);

      // Parse response body if it's JSON
      let parsedBody = responseBody;
      if (typeof responseBody === 'string') {
        try {
          parsedBody = JSON.parse(responseBody);
        } catch (e) {
          // Not JSON, keep as string
          console.debug('[Network Interceptor] Response body is not JSON, keeping as string');
        }
      }

      // Truncate if necessary
      parsedBody = truncateResponseBody(parsedBody, url);

      // Build request data object
      const requestData = {
        id: requestCounter,
        url: url,
        method: method,
        status: status,
        timestamp: new Date().toISOString(),
        response_headers: sanitizeHeaders(responseHeaders),
        response_body: parsedBody,
        response_size_bytes: typeof responseBody === 'string' ? responseBody.length : JSON.stringify(responseBody).length,
        duration_ms: duration,
        captured_at: new Date().toISOString()
      };

      // Store in local array
      capturedRequests.push(requestData);

      // Send to content script
      postToContentScript(requestData);

      console.log(`[Network Interceptor] Successfully captured request #${requestCounter} - Total captured: ${capturedRequests.length}`);

    } catch (error) {
      console.error('[Network Interceptor] Error capturing response:', error);
    }
  }

  /**
   * Override fetch API
   */
  const originalFetch = window.fetch;
  window.fetch = async function(...args) {
    const startTime = Date.now();
    let url = '';
    let method = 'GET';

    // Extract URL and method from fetch arguments
    if (typeof args[0] === 'string') {
      url = args[0];
    } else if (args[0] instanceof Request) {
      url = args[0].url;
      method = args[0].method || 'GET';
    }

    if (args[1] && args[1].method) {
      method = args[1].method;
    }

    try {
      // Call original fetch
      const response = await originalFetch.apply(this, args);

      // Check if we should capture this request
      if (shouldCaptureRequest(url) && response.ok) {
        console.log('[Network Interceptor] Capturing fetch request:', url, 'status:', response.status);
        // Clone response to read body without consuming it
        const clonedResponse = response.clone();

        // Extract headers
        const responseHeaders = {};
        for (const [key, value] of clonedResponse.headers.entries()) {
          responseHeaders[key] = value;
        }

        // Read response body
        try {
          const contentType = clonedResponse.headers.get('content-type') || '';
          let responseBody;

          if (contentType.includes('application/json')) {
            responseBody = await clonedResponse.json();
          } else {
            responseBody = await clonedResponse.text();
          }

          // Capture the response
          captureResponse(url, method, response.status, responseHeaders, responseBody, startTime);

        } catch (bodyError) {
          console.warn('[Network Interceptor] Failed to read response body:', bodyError);
        }
      }

      // Return original response
      return response;

    } catch (error) {
      console.error('[Network Interceptor] Fetch error:', error);
      throw error;
    }
  };

  console.log('[Network Interceptor] fetch() API overridden successfully');

  /**
   * Override XMLHttpRequest
   */
  const originalXHROpen = XMLHttpRequest.prototype.open;
  const originalXHRSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this._interceptorData = {
      method: method,
      url: url,
      startTime: Date.now()
    };
    return originalXHROpen.apply(this, [method, url, ...rest]);
  };

  XMLHttpRequest.prototype.send = function(...args) {
    const xhr = this;

    // Add event listener to capture response
    const originalOnReadyStateChange = xhr.onreadystatechange;
    xhr.onreadystatechange = function(...eventArgs) {
      // Call original handler if exists
      if (originalOnReadyStateChange) {
        originalOnReadyStateChange.apply(this, eventArgs);
      }

      // Capture when request completes
      if (xhr.readyState === 4 && xhr._interceptorData) {
        const url = xhr._interceptorData.url;
        const method = xhr._interceptorData.method;
        const startTime = xhr._interceptorData.startTime;

        if (shouldCaptureRequest(url) && xhr.status >= 200 && xhr.status < 300) {
          console.log('[Network Interceptor] Capturing XHR request:', url, 'status:', xhr.status);
          // Extract response headers
          const responseHeaders = {};
          const headersString = xhr.getAllResponseHeaders();
          if (headersString) {
            const headers = headersString.split('\r\n');
            headers.forEach(header => {
              const parts = header.split(': ');
              if (parts.length === 2) {
                responseHeaders[parts[0]] = parts[1];
              }
            });
          }

          // Get response body
          let responseBody = xhr.responseText;
          try {
            const contentType = xhr.getResponseHeader('content-type') || '';
            if (contentType.includes('application/json')) {
              responseBody = JSON.parse(xhr.responseText);
            }
          } catch (e) {
            // Keep as text
          }

          // Capture the response
          captureResponse(url, method, xhr.status, responseHeaders, responseBody, startTime);
        }
      }
    };

    return originalXHRSend.apply(this, args);
  };

  console.log('[Network Interceptor] XMLHttpRequest overridden successfully');

  /**
   * Expose API for debugging
   */
  window.__tableauNetworkInterceptor = {
    getCapturedRequests: () => capturedRequests,
    getRequestCount: () => capturedRequests.length,
    clearRequests: () => {
      capturedRequests = [];
      requestCounter = 0;
      console.log('[Network Interceptor] Cleared all captured requests');
    },
    config: CONFIG
  };

  console.log('[Network Interceptor] Initialization complete. Monitoring Tableau API requests...');
  console.log('[Network Interceptor] Debug API available at: window.__tableauNetworkInterceptor');

})();

