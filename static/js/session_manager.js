/**
 * Session Manager - Client-Side Session and Context Management
 * 
 * Manages user identity, dashboard context, and session lifecycle on the frontend.
 * Extracts user information from Tableau and maintains session state.
 * 
 * Key Features:
 * - Automatic user extraction from Tableau session
 * - Session ID generation and persistence
 * - RequestContext assembly for all API calls
 * - Anonymous fallback for testing
 * 
 * Author: Enterprise Architecture Refactor
 * Date: December 2024
 */

class SessionManager {
    constructor() {
        this.sessionId = null;
        this.userIdentity = null;
        this.dashboardContext = null;
        this.initialized = false;
        
        console.log('[SessionManager] Instance created');
    }
    
    /**
     * Initialize session manager
     * Extracts user and dashboard context from Tableau
     * 
     * @returns {Promise<boolean>} True if initialization successful
     */
    async initialize() {
        console.log('[SessionManager] Starting initialization...');
        
        try {
            // Step 1: Generate or restore session ID
            await this._initializeSessionId();
            
            // Step 2: Extract user identity
            await this._extractUserIdentity();
            
            // Step 3: Extract dashboard context
            await this._extractDashboardContext();
            
            // Validate all required data is present
            if (!this.userIdentity || !this.dashboardContext || !this.sessionId) {
                throw new Error('Failed to initialize all required session components');
            }
            
            this.initialized = true;
            
            console.log('[SessionManager] ✅ Initialization complete:', {
                sessionId: this.sessionId,
                user: this.userIdentity.username,
                dashboard: this.dashboardContext.dashboardName
            });
            
            return true;
            
        } catch (error) {
            console.error('[SessionManager] ❌ Initialization failed:', error);
            this.initialized = false;
            return false;
        }
    }
    
    /**
     * Generate or restore session ID from sessionStorage
     * Session persists across page reloads but not browser tabs
     * 
     * @private
     */
    async _initializeSessionId() {
        // Check sessionStorage for existing session
        this.sessionId = sessionStorage.getItem('chatbot_session_id');
        
        if (!this.sessionId) {
            // Generate new UUID
            this.sessionId = this._generateUUID();
            sessionStorage.setItem('chatbot_session_id', this.sessionId);
            console.log('[SessionManager] New session created:', this.sessionId);
        } else {
            console.log('[SessionManager] Session restored:', this.sessionId);
        }
    }
    
    /**
     * Extract user identity from Tableau
     * Tries multiple methods in priority order
     * 
     * @private
     */
    async _extractUserIdentity() {
        console.log('[SessionManager] Extracting user identity...');
        
        try {
            // Method 1: Try Tableau Extensions API (most reliable)
            if (typeof tableau !== 'undefined' && tableau.extensions) {
                console.log('[SessionManager] Tableau Extensions API available');
                // Note: Extensions API doesn't directly expose user info
                // We'll try other methods
            }
            
            // Method 2: Extract from window.bootstrapData (Tableau Cloud)
            if (window.bootstrapData && window.bootstrapData.user) {
                const user = window.bootstrapData.user;
                this.userIdentity = {
                    luid: user.luid,
                    username: user.username,
                    displayName: user.displayName,
                    systemUserId: user.systemUserId,
                    domainName: user.domainName || 'local'
                };
                
                console.log('[SessionManager] ✅ User extracted from bootstrapData:', this.userIdentity.username);
                return;
            }
            
            // Method 3: Try to fetch from backend (if already logged)
            const backendUser = await this._fetchUserFromBackend();
            if (backendUser) {
                this.userIdentity = backendUser;
                console.log('[SessionManager] ✅ User retrieved from backend:', this.userIdentity.username);
                return;
            }
            
            // Method 4: Fallback - create anonymous user
            console.warn('[SessionManager] ⚠️ Could not extract Tableau user, creating anonymous session');
            this.userIdentity = await this._createAnonymousUser();
            
        } catch (error) {
            console.error('[SessionManager] Error extracting user:', error);
            // Fallback to anonymous
            this.userIdentity = await this._createAnonymousUser();
        }
    }
    
    /**
     * Extract dashboard context from Tableau
     * 
     * @private
     */
    async _extractDashboardContext() {
        console.log('[SessionManager] Extracting dashboard context...');
        
        try {
            // Method 1: Tableau Extensions API
            if (typeof tableau !== 'undefined' && tableau.extensions) {
                try {
                    const dashboard = tableau.extensions.dashboardContent.dashboard;
                    const workbook = dashboard.workbook;
                    
                    this.dashboardContext = {
                        workbookId: workbook.id,
                        workbookName: workbook.name,
                        dashboardName: dashboard.name
                    };
                    
                    console.log('[SessionManager] ✅ Dashboard context from Extensions API:', this.dashboardContext);
                    return;
                } catch (extError) {
                    console.warn('[SessionManager] Extensions API available but failed:', extError);
                }
            }
            
            // Method 2: Parse from URL
            const urlContext = this._extractFromURL();
            if (urlContext) {
                this.dashboardContext = urlContext;
                console.log('[SessionManager] ✅ Dashboard context from URL:', this.dashboardContext);
                return;
            }
            
            // Method 3: Parse from page title
            const titleContext = this._extractFromPageTitle();
            if (titleContext) {
                this.dashboardContext = titleContext;
                console.log('[SessionManager] ✅ Dashboard context from page title:', this.dashboardContext);
                return;
            }
            
            // Fallback: Default context
            this.dashboardContext = {
                workbookId: 'unknown_workbook',
                workbookName: 'Unknown Workbook',
                dashboardName: 'Unknown Dashboard'
            };
            
            console.warn('[SessionManager] ⚠️ Using default dashboard context');
            
        } catch (error) {
            console.error('[SessionManager] Error extracting dashboard context:', error);
            throw error;
        }
    }
    
    /**
     * Fetch user from backend API
     * 
     * @private
     * @returns {Promise<Object|null>} User object or null
     */
    async _fetchUserFromBackend() {
        try {
            const response = await fetch('/api/user/get_current_user', {
                method: 'GET',
                credentials: 'include'
            });
            
            if (response.ok) {
                const data = await response.json();
                if (data.success && data.user) {
                    return {
                        luid: data.user.luid,
                        username: data.user.username,
                        displayName: data.user.displayName,
                        systemUserId: data.user.systemUserId,
                        domainName: data.user.domainName
                    };
                }
            }
        } catch (error) {
            console.warn('[SessionManager] Could not fetch user from backend:', error);
        }
        
        return null;
    }
    
    /**
     * Create anonymous user with browser fingerprint
     * 
     * @private
     * @returns {Promise<Object>} Anonymous user object
     */
    async _createAnonymousUser() {
        const fingerprint = this._generateBrowserFingerprint();
        
        return {
            luid: `anonymous_${fingerprint}`,
            username: 'anonymous@local',
            displayName: 'Anonymous User',
            systemUserId: null,
            domainName: 'anonymous'
        };
    }
    
    /**
     * Extract dashboard context from URL
     * Example: https://prod-in-a.online.tableau.com/#/site/xxx/views/WorkbookName/DashboardName
     * 
     * @private
     * @returns {Object|null} Dashboard context or null
     */
    _extractFromURL() {
        const url = window.location.href;
        
        // Match Tableau URL pattern
        const match = url.match(/\/views\/([^\/]+)\/([^?#]+)/);
        
        if (match) {
            return {
                workbookId: match[1],
                workbookName: match[1].replace(/_/g, ' '),
                dashboardName: match[2].replace(/_/g, ' ')
            };
        }
        
        return null;
    }
    
    /**
     * Extract dashboard context from page title
     * Example: "Workbook Name: Dashboard Name - Tableau Cloud"
     * 
     * @private
     * @returns {Object|null} Dashboard context or null
     */
    _extractFromPageTitle() {
        const title = document.title;
        
        // Match pattern "Workbook: Dashboard - Tableau"
        const match = title.match(/^([^:]+):\s*([^\-]+)/);
        
        if (match) {
            const workbookName = match[1].trim();
            const dashboardName = match[2].trim();
            
            return {
                workbookId: workbookName.replace(/\s+/g, '_'),
                workbookName: workbookName,
                dashboardName: dashboardName
            };
        }
        
        return null;
    }
    
    /**
     * Generate browser fingerprint for anonymous users
     * Combines multiple browser characteristics
     * 
     * @private
     * @returns {string} Fingerprint hash
     */
    _generateBrowserFingerprint() {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        ctx.textBaseline = 'top';
        ctx.font = '14px Arial';
        ctx.fillText('fingerprint', 2, 2);
        const canvasHash = canvas.toDataURL().slice(-50);
        
        const components = [
            navigator.userAgent,
            navigator.language,
            screen.width + 'x' + screen.height,
            new Date().getTimezoneOffset(),
            canvasHash
        ].join('|');
        
        // Simple hash function
        let hash = 0;
        for (let i = 0; i < components.length; i++) {
            hash = ((hash << 5) - hash) + components.charCodeAt(i);
            hash = hash & hash;
        }
        
        return Math.abs(hash).toString(36);
    }
    
    /**
     * Generate UUID v4
     * 
     * @private
     * @returns {string} UUID string
     */
    _generateUUID() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            const r = Math.random() * 16 | 0;
            const v = c == 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }
    
    /**
     * Get complete request context for API calls
     * 
     * @returns {Object} Context object to include in API requests
     * @throws {Error} If session not initialized
     */
    getRequestContext() {
        if (!this.initialized) {
            throw new Error('SessionManager not initialized. Call initialize() first.');
        }
        
        return {
            user: this.userIdentity,
            workbook_id: this.dashboardContext.workbookId,
            workbook_name: this.dashboardContext.workbookName,
            dashboard_name: this.dashboardContext.dashboardName,
            session_id: this.sessionId,
            timestamp: new Date().toISOString()
        };
    }
    
    /**
     * End current session
     * Clears session ID from sessionStorage
     */
    endSession() {
        console.log('[SessionManager] Ending session:', this.sessionId);
        
        sessionStorage.removeItem('chatbot_session_id');
        
        this.sessionId = null;
        this.userIdentity = null;
        this.dashboardContext = null;
        this.initialized = false;
        
        console.log('[SessionManager] ✅ Session ended');
    }
    
    /**
     * Check if session is initialized
     * 
     * @returns {boolean} True if ready to use
     */
    isReady() {
        return this.initialized;
    }
    
    /**
     * Get session info for display/debugging
     * 
     * @returns {Object} Session information
     */
    getSessionInfo() {
        return {
            initialized: this.initialized,
            sessionId: this.sessionId,
            user: this.userIdentity ? {
                username: this.userIdentity.username,
                displayName: this.userIdentity.displayName,
                isAnonymous: this.userIdentity.domainName === 'anonymous'
            } : null,
            dashboard: this.dashboardContext ? {
                workbookName: this.dashboardContext.workbookName,
                dashboardName: this.dashboardContext.dashboardName
            } : null
        };
    }
}

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = SessionManager;
}

// Also make available globally
window.SessionManager = SessionManager;

console.log('[SessionManager] Module loaded');



