# Dashboard Filter Prompt Implementation Plan

**Date**: January 8, 2026
**Approach**: Ask user whether to apply dashboard filters via Chrome Extension dropdown

---

## Executive Summary

When a user queries data from a Tableau dashboard that has active filters, show them a dropdown to choose:
- **Apply dashboard filters** (get filtered results matching their view)
- **Ignore dashboard filters** (get total/unfiltered results)

This gives users control and transparency while avoiding incorrect assumptions.

---

## Problem Statement

### Current Behavior
- User asks: "twc count in march"
- Dashboard has filters: Client="Support - All", Vendor="MT Only", Domain="KB - KMS"
- Chatbot returns: 52.7M (all data)
- User sees in chart: 678k (filtered data)
- **Mismatch causes confusion**

### Why Not Auto-Apply Filters?
1. User might want total data, not filtered view
2. User's query might conflict with dashboard filters (e.g., query=March, dashboard=April)
3. User might explicitly mention different filter values in query
4. Auto-applying creates unpredictable behavior

### Solution
**Ask the user** which behavior they want, showing them exactly what filters are active.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. USER TYPES QUERY                                             │
│    "twc count in march"                                          │
└─────────────────────────┬───────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────────┐
│ 2. CHROME EXTENSION                                             │
│    - Capture query                                               │
│    - Check if Tableau dashboard has active filters (JS API)     │
│    - Parse user query for filter mentions (NLP)                 │
└─────────────────────────┬───────────────────────────────────────┘
                          ↓
                    ┌─────────┐
                    │ Filters │
                    │ Active? │
                    └────┬────┘
                         │
           ┌─────────────┴──────────────┐
           │                            │
          NO                          YES
           │                            │
           ↓                            ↓
┌──────────────────────┐    ┌──────────────────────────────────────┐
│ 3A. SEND DIRECTLY    │    │ 3B. SHOW FILTER PROMPT UI            │
│     TO BACKEND       │    │                                       │
│                      │    │  ┌─────────────────────────────────┐ │
│  No dropdown shown   │    │  │ Dashboard Filters Active:       │ │
│                      │    │  │ • Client: Support - All         │ │
│                      │    │  │ • Vendor: MT Only               │ │
│                      │    │  │ • Domain: KB - KMS              │ │
│                      │    │  │ • Month: April 2025             │ │
│                      │    │  │                                 │ │
│                      │    │  │ Your query: "twc count in march"│ │
│                      │    │  │ Detected filters from query:    │ │
│                      │    │  │ • Month: March 2025             │ │
│                      │    │  │                                 │ │
│                      │    │  │ How should I answer?            │ │
│                      │    │  │ [Dropdown ▼]                    │ │
│                      │    │  │  ○ Apply dashboard filters      │ │
│                      │    │  │  ○ Ignore dashboard filters     │ │
│                      │    │  │                                 │ │
│                      │    │  │        [Submit Query]           │ │
│                      │    │  └─────────────────────────────────┘ │
│                      │    │                                       │
│                      │    │  User selects option & clicks Submit │
└──────────┬───────────┘    └───────────────┬───────────────────────┘
           │                                │
           └────────────────┬───────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 4. SEND TO BACKEND                                              │
│    {                                                             │
│      "query": "twc count in march",                             │
│      "use_dashboard_filters": true/false,                       │
│      "dashboard_filters": {                                     │
│        "client": "Support - All",                               │
│        "vendor": "MT Only",                                     │
│        "domain": "KB - KMS",                                    │
│        "month": "April 2025"                                    │
│      },                                                          │
│      "query_filters": {                                         │
│        "month": "March 2025"                                    │
│      }                                                           │
│    }                                                             │
└─────────────────────────┬───────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────────┐
│ 5. BACKEND PROCESSING                                           │
│                                                                  │
│    if use_dashboard_filters == True:                            │
│        # Apply ALL dashboard filters + query filters            │
│        df = df.filter(client="Support - All")                   │
│        df = df.filter(vendor="MT Only")                         │
│        df = df.filter(domain="KB - KMS")                        │
│        df = df.filter(month="April 2025")   # Dashboard         │
│        df = df.filter(month="March 2025")   # Query             │
│        # Result: 0 rows (April ∩ March = empty)                 │
│                                                                  │
│    else:  # use_dashboard_filters == False                      │
│        # Apply ONLY query filters                               │
│        df = df.filter(month="March 2025")                       │
│        # Result: 52.7M rows                                     │
└─────────────────────────┬───────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────────┐
│ 6. RETURN RESULT TO USER                                        │
│    "TWC for March 2025: 0                                       │
│     (Applied filters: Client=Support - All, Vendor=MT Only,     │
│      Domain=KB - KMS, Month=April 2025 AND Month=March 2025)    │
│     No data matches all these filters."                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Details

### **Phase 1: Chrome Extension - Filter Detection**

#### **File**: `chrome_extension/content_script.js`

**Add function to capture active filters:**

```javascript
async function getActiveTableauFilters() {
    try {
        // Check if Tableau API is available
        if (typeof tableau === 'undefined' || !tableau.VizManager) {
            console.log('[FILTER_DETECT] Tableau API not available');
            return null;
        }

        const vizList = tableau.VizManager.getVizs();
        if (vizList.length === 0) {
            console.log('[FILTER_DETECT] No Tableau viz found');
            return null;
        }

        const viz = vizList[0];
        const workbook = viz.getWorkbook();
        const activeSheet = workbook.getActiveSheet();

        // Get filters
        const filters = await activeSheet.getFiltersAsync();

        console.log(`[FILTER_DETECT] Found ${filters.length} active filters`);

        if (filters.length === 0) {
            return null; // No filters active
        }

        // Parse filters into structured format
        const filterState = {};

        for (const filter of filters) {
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
            } else if (filterType === 'range') {
                filterData.min = filter.getMin();
                filterData.max = filter.getMax();
            } else if (filterType === 'relative-date') {
                filterData.period_type = filter.getPeriodType();
                filterData.range_n = filter.getRangeN();
                filterData.range_type = filter.getRangeType();
            }

            filterState[cleanFieldName] = filterData;
        }

        return filterState;

    } catch (error) {
        console.error('[FILTER_DETECT] Error capturing filters:', error);
        return null;
    }
}

function cleanTableauFieldName(fieldName) {
    // Extract clean field name from Tableau's internal format
    // Example: "[federated.xxx][none:client:nk]" → "client"

    const match = fieldName.match(/\[(?:none|yr|mn|dy|qr|sum|avg|cnt):([^:]+):/);
    if (match) {
        return match[1].toLowerCase();
    }

    // Fallback: try last segment
    const segments = fieldName.split(':');
    if (segments.length > 1) {
        return segments[1].replace(/[\[\]]/g, '').toLowerCase();
    }

    // Last resort
    return fieldName.replace(/[\[\]]/g, '').toLowerCase();
}
```

---

### **Phase 2: Chrome Extension - Query Filter Detection**

**Add NLP to detect filter mentions in user query:**

```javascript
function detectQueryFilters(query) {
    // Simple pattern matching for common filter mentions
    const queryFilters = {};

    const patterns = {
        client: /(?:for|client)\s+([A-Za-z0-9\s-]+?)(?:\s|$|,)/i,
        vendor: /(?:vendor|from)\s+([A-Za-z0-9\s-]+?)(?:\s|$|,)/i,
        domain: /(?:domain)\s+([A-Za-z0-9\s-]+?)(?:\s|$|,)/i,
        month: /(?:in|for)\s+(january|february|march|april|may|june|july|august|september|october|november|december)/i,
        year: /(?:in|for|year)\s+(20\d{2})/
    };

    for (const [field, pattern] of Object.entries(patterns)) {
        const match = query.match(pattern);
        if (match) {
            queryFilters[field] = match[1].trim();
        }
    }

    return queryFilters;
}
```

---

### **Phase 3: Chrome Extension - UI Prompt**

**Show dropdown when filters detected:**

```javascript
async function handleQuerySubmit(query) {
    // Get dashboard filters
    const dashboardFilters = await getActiveTableauFilters();

    // Detect query filters
    const queryFilters = detectQueryFilters(query);

    // If NO dashboard filters, send query directly
    if (!dashboardFilters || Object.keys(dashboardFilters).length === 0) {
        sendQueryToBackend(query, false, {}, queryFilters);
        return;
    }

    // Dashboard HAS filters - show prompt UI
    showFilterPromptUI(query, dashboardFilters, queryFilters);
}

function showFilterPromptUI(query, dashboardFilters, queryFilters) {
    // Create modal/dropdown UI
    const promptHTML = `
        <div class="filter-prompt-overlay">
            <div class="filter-prompt-modal">
                <h3>Dashboard Filters Detected</h3>

                <div class="filter-section">
                    <h4>Active Dashboard Filters:</h4>
                    <ul>
                        ${Object.entries(dashboardFilters).map(([field, data]) => {
                            const value = data.type === 'categorical'
                                ? data.values.join(', ')
                                : `${data.min} to ${data.max}`;
                            return `<li><strong>${field}:</strong> ${value}</li>`;
                        }).join('')}
                    </ul>
                </div>

                <div class="filter-section">
                    <h4>Your Query:</h4>
                    <p>"${query}"</p>
                    ${Object.keys(queryFilters).length > 0 ? `
                        <h5>Detected filters from query:</h5>
                        <ul>
                            ${Object.entries(queryFilters).map(([field, value]) =>
                                `<li><strong>${field}:</strong> ${value}</li>`
                            ).join('')}
                        </ul>
                    ` : ''}
                </div>

                <div class="filter-options">
                    <label>How should I answer your query?</label>
                    <select id="filter-choice">
                        <option value="apply">Apply dashboard filters (show filtered results)</option>
                        <option value="ignore">Ignore dashboard filters (show total/unfiltered results)</option>
                    </select>
                </div>

                <div class="filter-actions">
                    <button id="submit-with-choice">Submit Query</button>
                    <button id="cancel-query">Cancel</button>
                </div>
            </div>
        </div>
    `;

    // Insert into DOM
    document.body.insertAdjacentHTML('beforeend', promptHTML);

    // Add event listeners
    document.getElementById('submit-with-choice').addEventListener('click', () => {
        const choice = document.getElementById('filter-choice').value;
        const useDashboardFilters = (choice === 'apply');

        // Remove modal
        document.querySelector('.filter-prompt-overlay').remove();

        // Send query with user's choice
        sendQueryToBackend(query, useDashboardFilters, dashboardFilters, queryFilters);
    });

    document.getElementById('cancel-query').addEventListener('click', () => {
        document.querySelector('.filter-prompt-overlay').remove();
    });
}
```

**CSS for modal:**

```css
.filter-prompt-overlay {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.6);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 10000;
}

.filter-prompt-modal {
    background: white;
    padding: 24px;
    border-radius: 8px;
    max-width: 600px;
    max-height: 80vh;
    overflow-y: auto;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
}

.filter-section {
    margin: 16px 0;
    padding: 12px;
    background: #f5f5f5;
    border-radius: 4px;
}

.filter-options {
    margin: 20px 0;
}

.filter-options select {
    width: 100%;
    padding: 10px;
    font-size: 14px;
    border: 1px solid #ccc;
    border-radius: 4px;
    margin-top: 8px;
}

.filter-actions {
    display: flex;
    gap: 12px;
    justify-content: flex-end;
    margin-top: 20px;
}

.filter-actions button {
    padding: 10px 20px;
    border-radius: 4px;
    border: none;
    cursor: pointer;
    font-size: 14px;
}

#submit-with-choice {
    background: #0066cc;
    color: white;
}

#cancel-query {
    background: #e0e0e0;
    color: #333;
}
```

---

### **Phase 4: Backend - Request Schema Update**

**File**: `models/schemas.py`

```python
from typing import Optional, Dict, Any
from pydantic import BaseModel

class ChatRequest(BaseModel):
    message: str
    use_dashboard_filters: bool = False
    dashboard_filters: Optional[Dict[str, Any]] = None
    query_filters: Optional[Dict[str, Any]] = None
    # ... existing fields ...
```

---

### **Phase 5: Backend - Filter Application Logic**

**File**: `services/nlp_to_python/nl_to_python_workflow.py`

```python
def generate_python_code(
    self,
    query: str,
    use_dashboard_filters: bool = False,
    dashboard_filters: Optional[Dict[str, Any]] = None,
    # ... existing params ...
):
    """
    Generate Python code with optional dashboard filter application
    """

    # Load data
    df = pl.read_parquet('data_cache/default.parquet')

    # STEP 1: Apply dashboard filters if user chose to
    if use_dashboard_filters and dashboard_filters:
        df = self._apply_dashboard_filters(df, dashboard_filters)
        logger.info(f"[DASHBOARD_FILTER] Applied dashboard filters, rows: {len(df)}")

    # STEP 2: Continue with existing query processing
    # (existing code for parsing query, applying query filters, etc.)
    ...

    return generated_code


def _apply_dashboard_filters(
    self,
    df: pl.DataFrame,
    dashboard_filters: Dict[str, Any]
) -> pl.DataFrame:
    """
    Apply dashboard filters to DataFrame

    Args:
        df: polars DataFrame
        dashboard_filters: {
            'client': {'type': 'categorical', 'values': ['Support - All'], 'is_exclude': False},
            'vendor': {'type': 'categorical', 'values': ['MT Only'], 'is_exclude': False},
            ...
        }

    Returns:
        Filtered DataFrame
    """
    logger.info(f"[DASHBOARD_FILTER] Applying {len(dashboard_filters)} dashboard filters")

    for field_name, filter_config in dashboard_filters.items():
        # Map field name to column (handle case sensitivity, aliases)
        column_name = self._map_field_to_column(field_name, df.columns)

        if not column_name:
            logger.warning(f"[DASHBOARD_FILTER] Column '{field_name}' not found in data, skipping")
            continue

        filter_type = filter_config.get('type')

        if filter_type == 'categorical':
            values = filter_config.get('values', [])
            is_exclude = filter_config.get('is_exclude', False)

            if is_exclude:
                df = df.filter(~pl.col(column_name).is_in(values))
                logger.info(f"[DASHBOARD_FILTER] ✅ {column_name} NOT IN {values}")
            else:
                df = df.filter(pl.col(column_name).is_in(values))
                logger.info(f"[DASHBOARD_FILTER] ✅ {column_name} IN {values}")

        elif filter_type == 'range':
            min_val = filter_config.get('min')
            max_val = filter_config.get('max')

            if min_val is not None:
                df = df.filter(pl.col(column_name) >= min_val)
            if max_val is not None:
                df = df.filter(pl.col(column_name) <= max_val)

            logger.info(f"[DASHBOARD_FILTER] ✅ {column_name} RANGE [{min_val}, {max_val}]")

        # Add more filter types as needed (relative-date, etc.)

    logger.info(f"[DASHBOARD_FILTER] After all filters: {len(df)} rows")
    return df


def _map_field_to_column(self, field_name: str, available_columns: list) -> Optional[str]:
    """
    Map filter field name to actual DataFrame column name

    Handles:
    - Case insensitivity (Client → client)
    - Exact matches
    - Partial matches
    """
    field_lower = field_name.lower()

    # Exact match (case-insensitive)
    for col in available_columns:
        if col.lower() == field_lower:
            return col

    # Partial match
    for col in available_columns:
        if field_lower in col.lower() or col.lower() in field_lower:
            logger.info(f"[FIELD_MAP] Matched '{field_name}' → '{col}' (partial)")
            return col

    return None
```

---

### **Phase 6: Backend - Enhanced Response**

**File**: `services/data_exploration/data_exploration_no_chart.py`

```python
def _format_response_with_filter_context(
    self,
    result: pl.DataFrame,
    use_dashboard_filters: bool,
    dashboard_filters: Optional[Dict],
    query_filters: Optional[Dict]
) -> str:
    """
    Format response showing what filters were applied
    """

    # Format the result value
    value = result[0, 0]  # Assuming single aggregation result

    response = f"Result: {value:,.0f}\n\n"

    # Show filter context
    if use_dashboard_filters and dashboard_filters:
        response += "Applied filters from dashboard:\n"
        for field, config in dashboard_filters.items():
            if config['type'] == 'categorical':
                values_str = ', '.join(config['values'])
                response += f"  • {field}: {values_str}\n"
        response += "\n"

    if query_filters:
        response += "Applied filters from your query:\n"
        for field, value in query_filters.items():
            response += f"  • {field}: {value}\n"

    if not use_dashboard_filters and dashboard_filters:
        response += "\n(Dashboard filters were ignored as per your selection)\n"

    return response
```

---

## Handling Edge Cases

### **Case 1: Conflicting Filters (Dashboard=April, Query=March)**

**Behavior:**
- If user selects "Apply dashboard filters"
- Apply BOTH filters: `month='April' AND month='March'`
- Result: 0 rows (no data can be both April and March)
- Show clear message:
  ```
  Result: 0

  Applied filters from dashboard:
    • month: April 2025

  Applied filters from your query:
    • month: March 2025

  ⚠️ Note: No data matches all these filters.
  Your query asks for March, but dashboard is filtered to April.
  ```

**Alternative (smarter handling):**
- Detect temporal conflict
- In dropdown, show warning:
  ```
  ⚠️ Conflict detected:
  Dashboard filter: Month = April 2025
  Your query: Month = March 2025

  If you apply dashboard filters, the month from your query (March)
  will override the dashboard month (April).
  ```

### **Case 2: Query Explicitly Mentions Different Filter Value**

**Example:**
- Dashboard: Client = "Support - All"
- Query: "twc in march for Uber Internal"

**Behavior:**
- Show in prompt:
  ```
  Dashboard Filters:
    • Client: Support - All

  Your Query: "twc in march for Uber Internal"
  Detected filters from query:
    • Client: Uber Internal (conflicts with dashboard)
    • Month: March

  [Dropdown]
    ○ Use query filters (Client=Uber Internal, Month=March)
    ○ Use dashboard filters (Client=Support - All, Month=March)
    ○ Use both (Client=Support - All AND Uber Internal → likely 0 results)
  ```

### **Case 3: Dashboard Has No Filters**

**Behavior:**
- Skip dropdown entirely
- Send query directly to backend
- Process normally

### **Case 4: Tableau API Not Available**

**Behavior:**
- Log warning
- Skip dropdown
- Process query with only query filters (current behavior)
- Show note to user: "Unable to detect dashboard filters, showing unfiltered results"

---

## User Experience Flow Examples

### **Example 1: Happy Path with Dashboard Filters**

1. User viewing dashboard with filters: Client="Support - All", Vendor="MT Only"
2. User types: "twc count in march"
3. Dropdown appears:
   ```
   Dashboard Filters Active:
   • Client: Support - All
   • Vendor: MT Only

   Your query: "twc count in march"

   [Apply dashboard filters ▼]

   [Submit Query]
   ```
4. User selects "Apply dashboard filters" → clicks Submit
5. Result: "TWC for March 2025: 678,070 (filtered by Client=Support - All, Vendor=MT Only)"

### **Example 2: User Wants Total (Ignore Filters)**

1. Same dashboard with filters
2. User types: "total twc in march"
3. Dropdown appears
4. User selects "Ignore dashboard filters" → clicks Submit
5. Result: "TWC for March 2025: 52,726,023 (all clients, all vendors)"

### **Example 3: No Dashboard Filters**

1. User viewing dashboard with NO active filters
2. User types: "twc count in march"
3. No dropdown shown (sent directly to backend)
4. Result: "TWC for March 2025: 52,726,023"

---

## Implementation Checklist

### **Chrome Extension**
- [ ] Add Tableau JavaScript API integration
- [ ] Implement `getActiveTableauFilters()` function
- [ ] Implement `cleanTableauFieldName()` helper
- [ ] Implement `detectQueryFilters()` NLP function
- [ ] Create filter prompt UI (HTML/CSS)
- [ ] Add event handlers for dropdown and submit
- [ ] Update `sendQueryToBackend()` to include filter data
- [ ] Handle errors when Tableau API unavailable

### **Backend**
- [ ] Update `ChatRequest` schema with filter fields
- [ ] Implement `_apply_dashboard_filters()` method
- [ ] Implement `_map_field_to_column()` helper
- [ ] Update `generate_python_code()` to accept filter params
- [ ] Update response formatting to show applied filters
- [ ] Add conflict detection for temporal filters
- [ ] Add logging for filter application steps

### **Testing**
- [ ] Test with dashboard with single filter
- [ ] Test with dashboard with multiple filters
- [ ] Test with no dashboard filters
- [ ] Test with conflicting temporal filters (April vs March)
- [ ] Test with query explicitly mentioning different filter value
- [ ] Test with Tableau API unavailable
- [ ] Test field name mapping (various formats)
- [ ] Test with categorical, range, and date filters
- [ ] End-to-end test: query → dropdown → result matches expectation

---

## Migration Plan

### **Phase 1: Chrome Extension Only (Week 1)**
- Implement filter capture and dropdown UI
- Test on live dashboards
- Validate filter data structure

### **Phase 2: Backend Integration (Week 2)**
- Implement filter application logic
- Add field mapping
- Test with mock filter data

### **Phase 3: End-to-End Testing (Week 3)**
- Connect extension to backend
- Test all scenarios
- Fix edge cases

### **Phase 4: Rollout (Week 4)**
- Deploy to test users
- Gather feedback
- Iterate on UX

---

## Success Metrics

- ✅ Dropdown shows correctly when dashboard has filters (95%+ cases)
- ✅ Filter application produces correct results (matches manual verification)
- ✅ Zero false positives (dropdown when no filters active)
- ✅ Clear error messages when conflicts occur
- ✅ User satisfaction: results match expectations

---

## Open Questions

1. **Dropdown styling** - Should it match Tableau's UI style or custom branding?
2. **Performance** - How long does `getFiltersAsync()` take on dashboards with many filters?
3. **Multi-sheet dashboards** - If dashboard has multiple sheets with different filters, which to use?
4. **Filter persistence** - Should we cache filter state or fetch fresh every query?

---

## Files to Create/Modify

### **New Files**
- `chrome_extension/filter_capture.js` - Filter detection logic
- `chrome_extension/filter_prompt_ui.js` - Dropdown UI component
- `chrome_extension/styles/filter_prompt.css` - Modal styling

### **Modified Files**
- `chrome_extension/content_script.js` - Add filter capture on query submit
- `models/schemas.py` - Update ChatRequest schema
- `services/nlp_to_python/nl_to_python_workflow.py` - Add filter application
- `services/data_exploration/data_exploration_no_chart.py` - Update response formatting

---

## Conclusion

This approach gives users **full control** while maintaining **transparency**. By showing exactly what filters exist and asking the user's preference, we eliminate confusion and support both use cases:
- Users who want filtered results (matching their dashboard view)
- Users who want total/unfiltered results (exploratory queries)

The implementation is straightforward, testable, and handles edge cases gracefully.
