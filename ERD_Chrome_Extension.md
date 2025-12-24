# Chrome Extension - Entity Relationship Diagram (ERD)

## System Overview
**Tableau Chrome Extension** - Manifest V3 extension providing AI-powered chat interface for Tableau dashboards with network interception and backend integration.

---

## Core Entities & Relationships

```
┌────────────────────────┐         ┌────────────────────────┐
│   extensionState       │<───────>│ flaskConnectionState   │
├────────────────────────┤  syncs  ├────────────────────────┤
│ ready: boolean         │         │ initialized: boolean   │
│ connected: boolean     │         │ sessionId: string      │
│ backendUrl: string     │         │ connected: boolean     │
│ context: {             │         │ workbookName: string   │
│   dashboardName        │         │ dashboardName: string  │
│   workbookName         │         │ connectionKey: string  │
│   worksheetNames[]     │         └────────────────────────┘
│   filters              │                     │
│   selection[]          │                     │
│   selectedData         │                     │ identifies
│   activeWorksheet      │                     ▼
│ }                      │         ┌────────────────────────┐
│ currentUrl: string     │         │  Backend Connection    │
└────────────────────────┘         ├────────────────────────┤
            │                      │ POST /api/tableau/     │
            │ provides context     │      initialize        │
            │                      │ POST /api/chat         │
            ▼                      │ GET /api/get_worksheets│
┌────────────────────────┐         │ GET /api/export-status │
│  chartSelectionState   │         └────────────────────────┘
├────────────────────────┤                     │
│ availableCharts[]      │                     │
│ selectedChart: {       │<────────────────────┘ returns
│   name                 │           worksheets
│   viewName             │
│   workbookName         │
│ }                      │
│ chartsLoaded: boolean  │
│ loading: boolean       │
└────────────────────────┘
            │
            │ selected for
            ▼
┌────────────────────────┐         ┌────────────────────────┐
│   navigationState      │         │ networkRequestsState   │
├────────────────────────┤         ├────────────────────────┤
│ currentPage: string    │         │ captureEnabled: boolean│
│ selectedChart: object  │         │ captureStartTime: Date │
│ welcomeShown: boolean  │         │ captureTimeoutMs: int  │
│ userDataLogged: boolean│         │ requests[]             │
└────────────────────────┘         │ totalCaptured: int     │
                                   └────────────────────────┘
                                               │
                                               │ contains
                                               ▼
                                   ┌────────────────────────┐
                                   │   NetworkRequest       │
                                   ├────────────────────────┤
                                   │ id: number             │
                                   │ url: string            │
                                   │ method: string         │
                                   │ status: number         │
                                   │ timestamp: ISO string  │
                                   │ response_headers: {}   │
                                   │ response_body: any     │
                                   │ response_size_bytes    │
                                   │ duration_ms: number    │
                                   │ captured_at: ISO       │
                                   └────────────────────────┘
```

---

## Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CHROME EXTENSION                         │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Background Service Worker (background.js)           │  │
│  │  - Tab management & lifecycle                        │  │
│  │  - Proxy fetch (CORS bypass)                         │  │
│  │  - Health check polling (60s)                        │  │
│  │  - Chrome storage logging                            │  │
│  └──────────────────────────────────────────────────────┘  │
│           │ chrome.runtime.sendMessage                      │
│           ▼                                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Content Script (content-script.js - 4652 lines)     │  │
│  │  - Main UI rendering (floating chat interface)       │  │
│  │  - State management (5 state objects)                │  │
│  │  - Chat interaction logic                            │  │
│  │  - Backend API integration                           │  │
│  │  - Export status polling (1s interval)               │  │
│  │  - Network request handling                          │  │
│  └──────────────────────────────────────────────────────┘  │
│           │ window.postMessage                              │
│           ▼                                                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Page Context (network-interceptor.js)               │  │
│  │  - Override fetch() & XMLHttpRequest                 │  │
│  │  - Capture Tableau API calls                         │  │
│  │  - Pattern matching (getSessionInfo, getUserInfo)    │  │
│  │  - Post captured requests to content script          │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Feedback Module (feedback.js)                       │  │
│  │  - FeedbackManager class                             │  │
│  │  - User feedback collection UI                       │  │
│  │  - Thumbs up/down with optional text                 │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Popup Interface (popup.html/js)                     │  │
│  │  - Quick status view                                 │  │
│  │  - Extension icon badge                              │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                           │
                           │ HTTP/HTTPS
                           ▼
                ┌──────────────────────┐
                │  Backend Server      │
                │  localhost:8502      │
                └──────────────────────┘
```

---

## Data Flow Sequences

### 1. Initialization Flow
```
Tableau Page Load
  → Inject network-interceptor.js (document_start)
  → Load content-script.js
  → POST /api/tableau/initialize
  → Poll GET /api/export-status (1s interval)
  → GET /api/get_worksheets
  → Update extensionState & chartSelectionState
  → Render UI
```

### 2. Chat Flow
```
User Input Question
  → Validate selected chart
  → Build request with context
  → POST /api/chat via proxyFetch
  → Background script makes actual fetch
  → Response received
  → Update navigationState
  → Render response in UI
```

### 3. Network Interception Flow
```
Page makes fetch/XHR
  → network-interceptor.js intercepts
  → Check URL patterns (Tableau API)
  → Capture request/response
  → window.postMessage to content-script
  → Store in networkRequestsState
  → Extract user context (name, session)
```

---

## Technology Stack

### Chrome APIs (Manifest V3)
- **chrome.runtime** - Messaging, lifecycle
- **chrome.tabs** - Tab management
- **chrome.scripting** - Script injection
- **chrome.storage** - Local persistence (100 logs)
- **chrome.action** - Icon, badge, popup

### Core Technologies
- **Vanilla JavaScript ES6+** - No frameworks
- **CSS3** - Flexbox, Grid, viewport-fixed positioning
- **Fetch API** - HTTP requests (overridden)
- **XMLHttpRequest** - Legacy AJAX (overridden)
- **postMessage API** - Cross-context communication

### Design Patterns
- **Proxy Pattern** - `proxyFetch` routes through background
- **Observer Pattern** - Event listeners, message passing
- **State Management** - 5 explicit state objects
- **Module Pattern** - IIFE scope encapsulation
- **Logger Pattern** - ContentLogger with levels

### UI/UX
- **Pure CSS Animations** - No animation libraries
- **SVG Icons** - Scalable vector graphics
- **Responsive Design** - Media queries
- **Accessibility** - ARIA labels, semantic HTML

---

## Storage & Persistence

### In-Memory (Lost on Refresh)
- extensionState
- flaskConnectionState
- chartSelectionState
- navigationState
- networkRequestsState

### Chrome Storage (Persistent)
- Last 100 log entries
- Background script logs

### Backend Persistence
- Connection state by connection_key
- Chat history (server-side)
- User logs via `/api/user/log_frontend_data`
- Feedback via `/api/feedback`

---

## Security & Permissions

### Host Permissions
- `http://localhost:8502/*` - Backend
- `https://*.tableau.com/*`
- `https://*.tableauusercontent.com/*`
- `https://tableau.uberinternal.com/*` (Uber internal)

### Security Features
- Sensitive header redaction (Authorization, Cookie, CSRF)
- Response size limits (100KB max)
- Request count limits (50 max/session)
- No credential storage in extension

### Content Security Policy
- Strict CSP for Manifest V3
- No inline scripts
- External script isolation

---

## Key Features

1. **Floating Chat UI**: Viewport-fixed, draggable interface
2. **Network Interception**: Captures Tableau API calls at page level
3. **Chart Selection**: Multi-worksheet support
4. **Export Polling**: Real-time data export tracking
5. **Feedback System**: Modular user feedback collection
6. **Context Awareness**: Dashboard, workbook, worksheet context
7. **Error Handling**: Graceful degradation, retry logic
8. **Logging**: Multi-level logging to backend & console

---

**Total Extension Code**: 4,652 lines (content-script.js) | **Manifest**: V3 | **Backend**: localhost:8502
