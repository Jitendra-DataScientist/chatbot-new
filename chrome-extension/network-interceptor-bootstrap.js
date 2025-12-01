// Network Interceptor Bootstrap
// This script runs at document_start to inject the network interceptor
// BEFORE Tableau's scripts load and make API calls

(function() {
  'use strict';

  console.log('[Network Interceptor Bootstrap] Injecting network interceptor at document_start...');

  // Create script element to inject into main page context
  const script = document.createElement('script');
  script.src = chrome.runtime.getURL('network-interceptor.js');
  script.type = 'text/javascript';
  
  // Inject immediately - this happens before any other page scripts execute
  // Use documentElement instead of document.head to work even if <head> doesn't exist yet
  (document.head || document.documentElement).appendChild(script);
  
  // Remove the script tag after injection to clean up DOM
  script.onload = function() {
    console.log('[Network Interceptor Bootstrap] Network interceptor script injected successfully');
    script.remove();
  };
  
  script.onerror = function() {
    console.error('[Network Interceptor Bootstrap] Failed to inject network interceptor');
  };
})();

