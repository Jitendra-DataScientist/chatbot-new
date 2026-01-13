# Phase 2 Implementation Plan: API Endpoint Updates

**Date**: January 9, 2026
**Status**: Ready for Implementation
**Goal**: Wire dashboard filter parameters from Chrome Extension → Flask API → Data Exploration Service

---

## Overview

Phase 1 implemented the backend filter logic. Phase 2 connects the API to accept and pass through filter parameters. This is a **minimal, surgical change** with zero hardcoding and full backward compatibility.

---

## What Changes Are Needed

### File: `app.py`

**Location**: `/api/chat` endpoint → `chat_api()` function (line ~1640)

**Changes Required**:
1. Extract `use_dashboard_filters`, `dashboard_filters`, and `query_filters` from incoming request
2. Pass these parameters to `EnhancedChatRequest` instantiation
3. Parameters flow automatically to data exploration service (already wired in Phase 1)

---

## Implementation Details

### Change 1: Extract Filter Parameters from Request

**Location**: `app.py`, line ~1646 (after `data = request.get_json()`)

**Current Code**:
```python
data = request.get_json(silent=True) or {}
print("data")
print (json.dumps(data, indent=4))
msg = (data.get("message") or "").strip()
tableau_context = data.get("context", {})
tableau_ready = data.get("tableauReady", False)
connection_key = data.get("connection_key")

# NEW: Handle selected chart context
selected_chart = data.get("selected_chart")
chart_context = data.get("chart_context", {})
```

**Add After Line ~1656** (after chart_context extraction):
```python
# Phase 2: Extract dashboard filter parameters
use_dashboard_filters = data.get("use_dashboard_filters", False)
dashboard_filters = data.get("dashboard_filters", None)
query_filters = data.get("query_filters", None)

# Log filter parameters for debugging
if use_dashboard_filters:
    debug_log("Dashboard filters received from client", {
        "use_dashboard_filters": use_dashboard_filters,
        "num_dashboard_filters": len(dashboard_filters) if dashboard_filters else 0,
        "num_query_filters": len(query_filters) if query_filters else 0,
        "dashboard_filter_fields": list(dashboard_filters.keys()) if dashboard_filters else []
    })
```

---

### Change 2: Pass Filter Parameters to EnhancedChatRequest

**Location**: `app.py`, line ~1884 (EnhancedChatRequest instantiation)

**Current Code**:
```python
chat_request = EnhancedChatRequest(
    message=msg,
    context={
        "workbook_name": state.workbook_name,
        "workbook_id": state.workbook_id,
        "available_columns": available_columns,
        "available_charts": [view['name'] for view in state.available_views],
        "csv_file_path": csv_file_path_for_context
    },
    tableau_context={
        "workbook_id": state.workbook_id,
        "site_id": state.site_id,
        "connection_key": state.workbook_name
    },
    connection_key=state.workbook_name,
    selected_chart=selected_chart,
    chart_context=chart_context,
    source=source
)
```

**Add These Lines** (after `source=source`):
```python
    # Phase 2: Add dashboard filter parameters
    use_dashboard_filters=use_dashboard_filters,
    dashboard_filters=dashboard_filters,
    query_filters=query_filters
```

**Complete Updated Code**:
```python
chat_request = EnhancedChatRequest(
    message=msg,
    context={
        "workbook_name": state.workbook_name,
        "workbook_id": state.workbook_id,
        "available_columns": available_columns,
        "available_charts": [view['name'] for view in state.available_views],
        "csv_file_path": csv_file_path_for_context
    },
    tableau_context={
        "workbook_id": state.workbook_id,
        "site_id": state.site_id,
        "connection_key": state.workbook_name
    },
    connection_key=state.workbook_name,
    selected_chart=selected_chart,
    chart_context=chart_context,
    source=source,
    # Phase 2: Dashboard filter support
    use_dashboard_filters=use_dashboard_filters,
    dashboard_filters=dashboard_filters,
    query_filters=query_filters
)
```

---

### Change 3: Also Update AUTO_ANALYSIS EnhancedChatRequest

**Location**: `app.py`, line ~2182 (auto_analysis_request instantiation)

**Current Code**:
```python
auto_analysis_request = EnhancedChatRequest(
    message=analysis_message,
    context={
        "workbook_name": state.workbook_name,
        "workbook_id": state.workbook_id,
        "csv_file_path": csv_data_loader.csv_file_path if csv_data_loader else None,
        # ... other context fields
    },
    # ... other parameters
)
```

**Add After Existing Parameters**:
```python
    # Phase 2: Dashboard filter support (pass through from original request)
    use_dashboard_filters=use_dashboard_filters,
    dashboard_filters=dashboard_filters,
    query_filters=query_filters
```

---

## Testing Strategy

### Test 1: Backward Compatibility (No Filters)

**Request** (existing format - no filter params):
```json
{
  "message": "twc count in march",
  "context": {
    "workbookName": "CentralizedCommOpsL10NMetrics"
  },
  "connection_key": "CentralizedCommOpsL10NMetrics"
}
```

**Expected Behavior**:
- ✅ Request processes normally
- ✅ No filters applied
- ✅ Results match current behavior
- ✅ No errors or warnings

---

### Test 2: With Dashboard Filters (Apply Mode)

**Request** (with filter params):
```json
{
  "message": "twc count in march",
  "context": {
    "workbookName": "CentralizedCommOpsL10NMetrics"
  },
  "connection_key": "CentralizedCommOpsL10NMetrics",
  "use_dashboard_filters": true,
  "dashboard_filters": {
    "client": {
      "type": "categorical",
      "values": ["Support - All"],
      "is_exclude": false
    },
    "vendor": {
      "type": "categorical",
      "values": ["MT Only"],
      "is_exclude": false
    }
  },
  "query_filters": {
    "month": "March"
  }
}
```

**Expected Behavior**:
- ✅ Dashboard filters applied to data
- ✅ Query filters (March) also applied
- ✅ Result reflects filtered data
- ✅ Logs show filter application

**Expected Log Output**:
```
[DASHBOARD_FILTER] Applying 2 dashboard filters
[DASHBOARD_FILTER] ✅ client IN ['Support - All']
[DASHBOARD_FILTER] ✅ vendor IN ['MT Only']
[DASHBOARD_FILTER] Filtering complete: 2067807 → 13512 rows (0.7% remaining)
```

---

### Test 3: With Dashboard Filters (Ignore Mode)

**Request**:
```json
{
  "message": "twc count in march",
  "use_dashboard_filters": false,
  "dashboard_filters": { ... },  // Provided but ignored
  "query_filters": {
    "month": "March"
  }
}
```

**Expected Behavior**:
- ✅ Dashboard filters ignored (use_dashboard_filters=false)
- ✅ Only query filters applied
- ✅ Result reflects unfiltered data (except query filters)

---

## How Parameters Flow Through the System

```
Chrome Extension
    ↓
    sends POST /api/chat with:
    {
        "message": "...",
        "use_dashboard_filters": true,
        "dashboard_filters": {...},
        "query_filters": {...}
    }
    ↓
app.py:chat_api()
    ↓
    Extracts: use_dashboard_filters, dashboard_filters, query_filters
    ↓
app.py:EnhancedChatRequest
    ↓
    Packages into: EnhancedChatRequest object
    ↓
query_agent.process_query()
    ↓
    Passes chat_request to: data_exploration_service
    ↓
data_exploration_no_chart.py:process()
    ↓
    Receives: use_dashboard_filters, dashboard_filters
    ↓
nl_to_python_workflow.py:generate_python_code()
    ↓
    Applies filters using: _apply_dashboard_filters()
    ↓
Result returned with filtered data
```

---

## Backward Compatibility Guarantees

✅ **If filter parameters NOT provided**: System works exactly as before
✅ **All parameters are optional**: Default values (False, None, None)
✅ **No breaking changes**: Existing API contracts unchanged
✅ **No hardcoding**: Works with any dashboard/dataset/filters
✅ **Schema already supports it**: Phase 1 added schema fields

---

## Files Modified Summary

| File | Lines Changed | Type of Change |
|------|---------------|----------------|
| `app.py` | ~12 lines | Extract params + pass to request objects |

**Total Lines Added**: ~12
**Total Lines Removed**: 0
**Total Files Modified**: 1

---

## Rollout Checklist

### Pre-Implementation
- [ ] Review this implementation plan
- [ ] Understand each change location
- [ ] Confirm Phase 1 tests still pass

### Implementation
- [ ] Make Change 1: Extract filter params in chat_api()
- [ ] Make Change 2: Pass params to EnhancedChatRequest (line ~1884)
- [ ] Make Change 3: Pass params to auto_analysis EnhancedChatRequest (line ~2182)
- [ ] Add debug logging for filter parameters

### Testing
- [ ] Test 1: Backward compatibility (no filters)
- [ ] Test 2: With dashboard filters (apply mode)
- [ ] Test 3: With dashboard filters (ignore mode)
- [ ] Check master_debug.log for filter application logs

### Validation
- [ ] Confirm Phase 1 tests still pass
- [ ] Confirm existing queries work unchanged
- [ ] Confirm filters are applied when provided
- [ ] No errors in server logs

---

## Test Script for Manual Validation

After implementation, use this script to test the API:

```python
import requests
import json

BASE_URL = "http://localhost:5000"  # Adjust as needed

# Test 1: No filters (backward compatibility)
def test_no_filters():
    response = requests.post(f"{BASE_URL}/api/chat", json={
        "message": "show data",
        "connection_key": "test_workbook"
    })
    print("Test 1 (No Filters):", response.status_code)
    print(json.dumps(response.json(), indent=2)[:200])

# Test 2: With dashboard filters
def test_with_filters():
    response = requests.post(f"{BASE_URL}/api/chat", json={
        "message": "twc count in march",
        "connection_key": "CentralizedCommOpsL10NMetrics",
        "use_dashboard_filters": True,
        "dashboard_filters": {
            "client": {
                "type": "categorical",
                "values": ["Support - All"],
                "is_exclude": False
            }
        }
    })
    print("\nTest 2 (With Filters):", response.status_code)
    print(json.dumps(response.json(), indent=2)[:200])

# Run tests
if __name__ == "__main__":
    test_no_filters()
    test_with_filters()
```

---

## Success Criteria

✅ All existing queries work without modification
✅ New filter parameters accepted and logged
✅ Filters applied correctly when provided
✅ No performance regression
✅ Clean logs showing filter application
✅ Zero hardcoding or bandaid solutions

---

## Next Steps After Phase 2

Once Phase 2 is complete and tested:

**Phase 3**: Chrome Extension - Filter Capture
- Implement Tableau JavaScript API integration
- Capture active filter state from dashboard
- Test filter capture on live dashboards

**Phase 4**: Chrome Extension - User Prompt UI
- Build dropdown modal
- Implement NLP query filter detection
- Let user choose "Apply" or "Ignore"

**Phase 5**: End-to-End Integration
- Connect extension to backend
- Test full user flow
- Add response formatting with applied filters

---

## Questions/Issues

If you encounter any issues during implementation:
1. Check master_debug.log for detailed error messages
2. Verify Phase 1 tests still pass
3. Confirm EnhancedChatRequest schema has filter fields
4. Review the parameter flow diagram above

---

**Phase 2 Status**: ✅ READY FOR IMPLEMENTATION
**Estimated Time**: 15-20 minutes for implementation + 10 minutes for testing
**Risk Level**: LOW (minimal changes, fully backward compatible)
