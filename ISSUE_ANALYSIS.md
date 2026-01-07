# Issue Analysis: TWC Count Discrepancy

**Date**: January 6, 2026
**Query**: "twc count in march"
**Chatbot Result**: 5.27e7 (52,726,023)
**Chart Value**: 678,070 (678.07k)
**Discrepancy**: ~77x difference

---

## Investigation Summary

### What Worked Correctly ✅

1. **Aggregation Semantic Enhancement** (AGGREGATION_SEMANTICS_IMPLEMENTATION.md)
   - Line 1596-1599 in master_debug.log shows the semantic override worked perfectly:
   ```
   [AGG_SEMANTIC] 'twc' is typically SUMmed (100% confidence)
   [AGG_SEMANTIC] Query uses 'count' but column semantic suggests SUM - overriding
   [AGG_SEMANTIC] ✅ Updated agg_functions: ['count'] → [sum]
   [AGG_SEMANTIC]    Reason: Semantic data shows 'twc' used with SUM in 173 charts
   ```
   - The system correctly changed COUNT to SUM ✅
   - This aggregation semantics feature is working as designed ✅

### Root Cause of Discrepancy ❌

**The problem is NOT with aggregation semantics. The problem is with YEAR FILTERING.**

#### Evidence from Logs

**master_debug.log (CommOps Dashboard):**
```
Line 1604: [YEAR_DETECTION] No explicit year found in query
Line 1605: [SMART_YEAR] ✅ Found most recent year in data: 2025
Line 1606: [TEMPORAL_FILTER] Using smart default year: 2025
Line 1692: df_filtered = df.filter(((df['month'].dt.month() == 3) & (df['month'].dt.year() == 2025)))
```

**master_debug_fro_dash.log (FRO Dashboard):**
```
Line 1273: [YEAR_DETECTION] No explicit year found in query
Line 1274: [PARAM_BUILD] 🔧 Year 2024 not explicit in query, will use current year
Line 1275: [SMART_YEAR] ✅ Found most recent year in data: 2025
Line 1276: [TEMPORAL_FILTER] Using smart default year: 2025
Line 1365: df_filtered = df.filter(((df['create_month'].dt.month() == 3) & (df['create_month'].dt.year() == 2025)))
```

#### Data Analysis

**March TWC sums by year (from data_cache/default.parquet):**

| Year | Row Count | TWC Sum       |
|------|-----------|---------------|
| 2022 | 36,650    | 6,973,910     |
| 2023 | 26,732    | 10,969,746    |
| 2024 | 23,966    | 19,651,976    |
| 2025 | 36,676    | **52,726,023** ← **Chatbot returned this** |

**Additional calculations:**
- All March data (2022-2025): 124,024 rows, TWC sum = 90,321,656
- Chatbot returned: 52,726,023 (March 2025 only)
- Chart shows: 678,070 (unknown source)

---

## The Problem: "Smart Year" Default Logic

### How It Currently Works

From `nl_to_python_workflow.py`:

1. **`_is_year_explicit_in_query()`** - Checks if user mentioned a year in query
   - Query: "twc count in march" → No year mentioned

2. **`_get_smart_default_year()`** - Finds most recent year in data
   - Scans the date column
   - Finds 2025 as the most recent year

3. **`_build_temporal_filter_expression()`** - Builds filter with that year
   - Creates: `(month == 3) & (year == 2025)`

### Why This Is Problematic

1. **Assumption Mismatch**: The system assumes users want the most recent year, but:
   - The chart the user is viewing might be filtered to a different year
   - The chart might show data aggregated across ALL years
   - The user might be interested in a specific historical period

2. **No Context Awareness**: The system doesn't check:
   - What year/time range the Tableau chart is currently filtered to
   - What year filters are active in the Tableau view
   - What the user is actually looking at

3. **Silent Behavior**: The system doesn't inform the user that it:
   - Automatically selected year 2025
   - Filtered out 2022, 2023, and 2024 data
   - Made an assumption about which time period to use

---

## What About the Chart Value (678,070)?

The chart value of 678,070 doesn't match any of the calculated March totals:
- Not March 2022: 6,973,910
- Not March 2023: 10,969,746
- Not March 2024: 19,651,976
- Not March 2025: 52,726,023
- Not all years: 90,321,656

**Possible explanations:**
1. Chart has additional filters applied (country, region, product, etc.)
2. Chart is showing a calculated field with different logic
3. Chart is showing a subset of data based on dashboard-level filters
4. Chart is using a different date range (e.g., specific weeks in March)
5. The exported CSV doesn't have all the filters that are active in the Tableau view

---

## The Core Issue

**The chatbot cannot reliably answer "twc count in march" because:**

1. **Missing Context**: It doesn't know what filters are active in the chart the user is viewing
2. **Wrong Defaults**: The "smart year" logic assumes most recent year, but has no validation
3. **No Verification**: It doesn't check if the result matches what the user sees
4. **Insufficient Chart Metadata**: The system doesn't capture or use the active filters from Tableau

---

## Why This Is Not Solved by Aggregation Semantics

The AGGREGATION_SEMANTICS_IMPLEMENTATION.md document correctly solved:
- ✅ Converting "count" to SUM for numeric columns
- ✅ Learning aggregation patterns from Tableau formulas
- ✅ Applying semantic corrections to aggregation functions

But it did NOT address:
- ❌ Temporal filter selection (which year to use)
- ❌ Chart context awareness (what filters are active)
- ❌ Multi-year vs single-year queries
- ❌ Alignment with user's current Tableau view

---

## Required Fix (Non-Bandaid, Robust Solution)

### Option 1: Chart-Context-Aware Filtering (Recommended)

**Principle**: When a user asks a question, use the filters that are currently active in their Tableau view

**Implementation:**
1. Capture active filters from Tableau when user asks a question
2. Pass these filters to the backend as part of the query context
3. Apply the same filters to the chatbot's data analysis
4. Return results that match what the user sees in their chart

**Advantages:**
- Matches user's mental model (they're looking at a filtered chart)
- No assumptions needed about year or other dimensions
- Works generically across all dashboards and filters
- Self-documenting (filter values come from Tableau, not hardcoded)

**Changes needed:**
- `Chrome Extension`: Capture active filter state from Tableau API
- `ChatContext model`: Add `active_tableau_filters` field
- `nl_to_python_workflow.py`: Use active_tableau_filters before smart defaults
- `_build_operation_params()`: Integrate Tableau filters into generated code

### Option 2: Multi-Year Awareness with Explicit Prompting

**Principle**: When no year is specified, don't assume - either ask or aggregate across all years

**Implementation:**
1. Detect when query mentions a month/period but no year
2. Check if data spans multiple years for that period
3. Either:
   - **A)** Aggregate across all years (sum/average depending on query)
   - **B)** Ask user which year they want
   - **C)** Return breakdown by year and let user choose

**Advantages:**
- Transparent about what years exist
- Gives user control
- No silent assumptions

**Disadvantages:**
- Might require extra interaction
- Could be annoying for users who do want recent data

### Option 3: Intelligent Year Selection with Validation

**Principle**: Make smart default, but validate against chart context

**Implementation:**
1. Use smart default year (most recent)
2. ALSO calculate for other available years
3. Compare with chart context (if available)
4. If discrepancy detected, inform user:
   ```
   "I calculated March 2025 (52.7M), but I also found:
    - March 2024: 19.7M
    - March 2023: 11.0M
    - March 2022: 7.0M

    Your chart shows 678K. This might be filtered differently.
    Which calculation matches what you're looking for?"
   ```

**Advantages:**
- Provides transparency
- Helps user identify the right data
- Educates user about available data

**Disadvantages:**
- More complex response generation
- Might be verbose for simple queries

---

## Recommendation

**Implement Option 1 (Chart-Context-Aware Filtering) as the primary solution.**

This is the most robust approach because:
1. **No hardcoding**: Filter values come from Tableau's actual state
2. **No assumptions**: Uses what the user is actually looking at
3. **Generic**: Works for any filter dimension (year, country, product, etc.)
4. **Self-documenting**: The chatbot knows exactly what filters were applied
5. **Matches user intent**: User is looking at a filtered view, chatbot uses same filters

**Fallback to Option 3** when:
- Chart context not available
- User asks question without looking at a specific chart
- Provides transparency about what was calculated

---

## Implementation Priority

1. **HIGH PRIORITY**: Capture and use Tableau active filters (Option 1)
2. **MEDIUM PRIORITY**: Add year detection validation and user prompting (Option 3)
3. **LOW PRIORITY**: Document behavior and add logging for transparency

---

## Files to Investigate/Modify

1. **Chrome Extension** (`tableau-chrome-extension/`):
   - Capture active filters from Tableau API
   - Pass to backend in chat request

2. **models/schemas.py** (`ChatContext`):
   - Add `active_tableau_filters: Optional[Dict[str, Any]]`

3. **services/nlp_to_python/nl_to_python_workflow.py**:
   - `_build_operation_params()`: Check for active_tableau_filters first
   - `_get_smart_default_year()`: Add fallback logic, not primary logic
   - `_build_temporal_filter_expression()`: Use Tableau filters when available

4. **services/data_exploration/data_exploration_no_chart.py**:
   - Pass filter context through to NL-to-Python workflow

---

## Conclusion

**Aggregation semantics is working perfectly.** The issue is **temporal filter selection (year defaulting)**.

The system is answering "twc count in march **2025**" when the user asked "twc count in march" and is viewing a chart that shows different data (possibly different year, possibly additional filters).

**The fix requires chart-context awareness, not aggregation semantics changes.**

**No bandaids. No hardcoding. Use Tableau's actual filter state.**
