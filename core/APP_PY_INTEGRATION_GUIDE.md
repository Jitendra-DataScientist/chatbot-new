# App.py Integration Guide

## Overview
This guide shows how to integrate the new architecture into app.py with minimal changes.

## Step 1: Add Imports (Add after existing imports)

```python
# ===== NEW ARCHITECTURE IMPORTS =====
from core.app_integration import (
    initialize_new_managers,
    register_new_routes,
    get_managers,
    cleanup_on_shutdown
)
from core.request_context import RequestContext
from core.flask_middleware import get_request_context, require_context
from core.agent_context_adapter import (
    get_disambiguation_adapter,
    get_conversation_adapter,
    StatelessAgentWrapper
)
from core.scoped_data_manager import get_scoped_data_manager
# ===== END NEW IMPORTS =====
```

## Step 2: Initialize New Managers (Add after global state managers section ~line 390)

```python
# ============================================================================
# NEW ARCHITECTURE MANAGERS (Multi-User, Multi-Dashboard Support)
# ============================================================================
try:
    new_managers = initialize_new_managers()
    
    # Extract managers for easy access
    hierarchical_state_manager = new_managers['state_manager']
    scoped_data_manager = new_managers['data_manager']
    scoped_cache_manager = new_managers['cache_manager']
    session_lifecycle_manager = new_managers['lifecycle_manager']
    
    # Initialize adapters
    disambiguation_adapter = get_disambiguation_adapter()
    conversation_adapter = get_conversation_adapter()
    
    master_logger.info("✅ New architecture managers initialized successfully")
    master_logger.info("✅ Multi-user, multi-dashboard support enabled")
    
except Exception as e:
    master_logger.error(f"❌ Failed to initialize new architecture: {e}", exc_info=True)
    raise
# ============================================================================
```

## Step 3: Register New Routes (Add after app initialization, before existing routes)

```python
# Register new architecture routes and middleware
register_new_routes(app)
master_logger.info("✅ New architecture routes registered")
```

## Step 4: Update Chat Route (Example refactor of /api/chat)

### Old Pattern:
```python
@app.post("/api/chat")
def chat_api():
    data = request.get_json(silent=True) or {}
    connection_key = data.get("connection_key", "default_workbook")
    
    # Get state using connection_key
    current_state = state_manager.get_state(connection_key)
    ...
```

### New Pattern:
```python
@app.post("/api/chat")
@require_context  # Ensures context validation
def chat_api():
    # Get validated RequestContext from middleware
    context = get_request_context()
    
    if not context:
        # Fallback for legacy requests (gradual migration)
        return handle_legacy_chat_request()
    
    # Get state using context (multi-tenant safe)
    current_state = hierarchical_state_manager.get_or_create_state(context)
    
    # Get data using context (session-scoped)
    main_data = scoped_data_manager.get_data(context, 'main_data')
    
    # Use disambiguation adapter (session-scoped)
    user_choice = disambiguation_adapter.get_disambiguation(
        context, 
        column_name='status',
        original_value='open'
    )
    
    # Store conversation (session-scoped)
    conversation_adapter.add_turn(context, query=msg, response=result)
    
    ...
```

## Step 5: Update Data Registration

### Old Pattern:
```python
# Register data globally
data_id = data_manager.register_data(df, connection_key)
```

### New Pattern:
```python
# Register data scoped to session
context = get_request_context()
data_id = scoped_data_manager.register_data(context, df, data_name='main_data')
```

## Step 6: Add Shutdown Handler

```python
# At the end of app.py
import atexit

@atexit.register
def shutdown():
    """Cleanup on app shutdown"""
    master_logger.info("App shutting down...")
    cleanup_on_shutdown()
```

## Step 7: Update Tableau Initialization Route

```python
@app.post("/api/initialize_tableau")
def initialize_tableau():
    """Initialize Tableau connection with new context system"""
    data = request.get_json(silent=True) or {}
    
    # Try to get context (new format)
    context = get_request_context()
    
    if context:
        # New format - use hierarchical state manager
        state = hierarchical_state_manager.get_or_create_state(context)
        
        # Store Tableau data in state
        state.workbook_id = data.get('workbook_id')
        state.dashboard_name = data.get('dashboard_name')
        
        # Register data if provided
        if 'tableau_data' in data:
            df = pd.DataFrame(data['tableau_data'])
            scoped_data_manager.register_data(context, df, 'main_data')
        
        return jsonify({
            'success': True,
            'session_id': context.session_id
        })
    else:
        # Legacy format - handle for backward compatibility
        return handle_legacy_initialization(data)
```

## Migration Strategy

### Phase 1: Run Both Systems (Weeks 1-2)
- Initialize both old and new managers
- Old routes continue working unchanged
- New routes use new architecture
- Monitor performance and correctness

### Phase 2: Gradual Route Migration (Weeks 3-4)
- Migrate one route at a time
- Keep fallbacks for legacy requests
- Test each migration thoroughly

### Phase 3: Deprecate Old System (Week 5+)
- Remove fallback code
- Remove old managers
- Full new architecture

## Testing Checklist

- [ ] Single user, single dashboard works
- [ ] Multiple users, same dashboard are isolated
- [ ] Same user, multiple dashboards are isolated
- [ ] Page refresh creates new session
- [ ] Session cleanup runs without errors
- [ ] Memory usage is stable over time
- [ ] No data leakage between sessions
- [ ] Anonymous users work (fallback)
- [ ] Health endpoints return correct data

## Rollback Plan

If issues occur:
1. Comment out `register_new_routes(app)` 
2. Comment out new manager initialization
3. Old system continues working
4. No data loss (disk persistence)

