# Attempted Fixes Session - December 16, 2025

## Issue Report
**Query:** "twc value in may"
**Errors:**
1. `'NoneType' object has no attribute 'get'` at `data_exploration.py:756`
2. `❌ CRITICAL: workbook_name not provided!` at `nl_to_python_workflow.py:604`
3. `Expected bytes, got a 'bool' object` - PyArrow error in DataManager and Polars conversion

---

## Fixes Attempted (FAILED)

### Fix #1: Enhanced Boolean Conversion in csv_data_loader.py
**File:** `services/csv_data_loader.py:139-167`

**What Was Done:**
- Added explicit detection of string boolean columns (`"True"`, `"False"`)
- Convert string booleans to actual `boolean` dtype before PyArrow/Polars conversion
- Applied `.convert_dtypes()` after manual conversion

**Code Added:**
```python
# Lines 143-161
for col in self.data.columns:
    if self.data[col].dtype == 'object':
        # Check if column only contains "True", "False", or nulls (case-insensitive)
        unique_vals = set(self.data[col].dropna().unique())
        # Convert to strings and normalize
        unique_str_vals = {str(v).strip() for v in unique_vals}

        # Check if it's a boolean column (only True/False values)
        if unique_str_vals.issubset({'True', 'False', 'true', 'false', '', 'TRUE', 'FALSE'}):
            # Convert string booleans to actual boolean dtype
            self.data[col] = self.data[col].replace({
                'True': True, 'true': True, 'TRUE': True,
                'False': False, 'false': False, 'FALSE': False,
                '': None, 'nan': None
            })
            self.data[col] = self.data[col].astype('boolean')
            self.logger.info(f"✓ Converted column '{col}' from string booleans to boolean dtype")
```

**Expected Result:**
- Columns like 'od', 'cl', 'noce', etc. would be converted from `dtype='object'` to `dtype='boolean'`
- PyArrow/Polars conversion would succeed
- No "Expected bytes, got a 'bool' object" errors

**Actual Result:**
- **STILL FAILING** - Same errors persist
- Server restarted multiple times
- Python cache cleared
- Disk cache (`data_cache/default.parquet`) deleted
- Code changes are on disk (verified with `sed` command)

---

## Why The Fix Did Not Work

### Possible Root Causes:

#### 1. **The Boolean Conversion Code Is Not Being Executed**
**Evidence:**
- No log lines showing: `✓ Converted column 'od' from string booleans to boolean dtype`
- The `logger.info()` statement on line 161 should appear in logs but doesn't
- This suggests the condition on line 153 is **never true**

**Why the condition might fail:**
```python
if unique_str_vals.issubset({'True', 'False', 'true', 'false', '', 'TRUE', 'FALSE'}):
```
- The actual CSV data might contain additional unexpected values (spaces, nulls represented differently, etc.)
- The column might not actually be `dtype='object'` (could already be something else)
- The `dropna()` call might not handle all null representations

#### 2. **Data Is Being Loaded From Elsewhere**
**Evidence:**
- `DataManager` has disk cache at `data_cache/default.parquet`
- Cache was deleted but might be recreated with old data immediately
- There might be multiple data loading paths that bypass `csv_data_loader.py`

**Potential bypass locations:**
- `meta_agents/query_understanding_agent.py:737` - `data_manager.register_data(csv_data, connection_key)`
- The `csv_data` might be loaded elsewhere and passed in already with wrong dtypes

#### 3. **The Real Issue Is AFTER Data Loading**
**Evidence from logs:**
- `19:06:03` - DataManager fails to persist: `"Expected bytes, got a 'bool' object", 'Conversion failed for column od with type object'`
- This happens at `data_manager.py:261` in `_persist_to_disk()`
- The error says `'column od with type object'` - meaning it STILL has `object` dtype

**This proves:**
- The boolean conversion in `csv_data_loader.py` is **NOT being applied** to the data
- OR the data is being transformed back to object dtype somewhere between loading and DataManager

#### 4. **Multiple CSV Loader Instances**
The data might be loaded in multiple places:
- `app.py:1814` - `csv_data_loader.load_data()`
- Auto-export process at `app.py:18:30:28`
- Each might create separate instances with different data

---

## The Error Chain (ACTUAL PROBLEM)

### Step-by-Step Breakdown:

1. **CSV Loaded** (19:05:42)
   - `csv_data_loader.py:158` - "Successfully loaded CSV with 2464795 rows and 48 columns"
   - **Problem:** NO logs showing boolean conversion happened
   - **Result:** Column 'od' still has `dtype='object'` containing string "False"

2. **DataManager Registration** (19:06:03)
   - `query_understanding_agent.py:737` - `data_manager.register_data(csv_data, connection_key)`
   - Tries to persist to disk using `df.to_parquet()` at `data_manager.py:258`
   - **PyArrow Error:** `"Expected bytes, got a 'bool' object"`
   - **Why:** PyArrow expects `object` columns to contain strings, but gets bool objects (somehow the string "False" became bool False but dtype is still 'object')

3. **Polars Conversion Fails** (19:06:11)
   - `data_exploration_no_chart.py:183` - `pl.from_pandas(df_normalized, include_index=False)`
   - **Same PyArrow Error**
   - **Result:** Falls back to old code

4. **Fallback to data_exploration.py** (19:06:15)
   - Old code path doesn't properly pass `workbook_name`
   - `nl_to_python_workflow.py:604` - `workbook_name not provided!`
   - Identifier detection disabled
   - Wrong results generated

5. **NoneType Error** (19:06:36)
   - `data_exploration.py:756` - `result_type = result_data.get('type')`
   - `result_data` is `None` because analysis failed
   - **Final Error:** `'NoneType' object has no attribute 'get'`

---

## What ACTUALLY Needs To Be Fixed

### The Real Root Cause:
**The boolean conversion code IS NOT BEING EXECUTED** because:

1. **The condition is failing** - The actual unique values in the column don't match the expected set
2. **OR the data type detection is wrong** - The column might not have `dtype='object'` initially

### How to Diagnose:

Add debug logging to see what's actually in the column:

```python
for col in self.data.columns:
    if self.data[col].dtype == 'object':
        unique_vals = set(self.data[col].dropna().unique())
        unique_str_vals = {str(v).strip() for v in unique_vals}

        # ADD THIS DEBUG:
        self.logger.info(f"DEBUG: Column '{col}' has unique values: {unique_str_vals}")
        self.logger.info(f"DEBUG: Column '{col}' dtype: {self.data[col].dtype}")

        if unique_str_vals.issubset({'True', 'False', 'true', 'false', '', 'TRUE', 'FALSE'}):
            # conversion code...
```

This will reveal:
- What values are actually in the 'od' column
- Why the subset check is failing

### Alternative Fix (More Aggressive):

**Don't check for subset - just try to convert known boolean columns:**

```python
# Hardcode known boolean columns from the CSV header
BOOLEAN_COLUMNS = ['od', 'cl', 'noce', 'gca', 'urg', 'l10nfix', 'cleanup', 'tcr8',
                   'dtp', 'nocharge', 'rosetta_suggestion']

for col in BOOLEAN_COLUMNS:
    if col in self.data.columns:
        try:
            # Force convert to boolean
            self.data[col] = self.data[col].replace({
                'True': True, 'true': True, 'TRUE': True,
                'False': False, 'false': False, 'FALSE': False,
                '': pd.NA, 'nan': pd.NA, None: pd.NA
            })
            self.data[col] = self.data[col].astype('boolean')
            self.logger.info(f"✓ FORCED conversion of '{col}' to boolean dtype")
        except Exception as e:
            self.logger.error(f"Failed to convert '{col}' to boolean: {e}")
```

This bypasses the detection logic and just forces conversion of known columns.

---

## Other Potential Issues

### 1. **NoneType Safety in data_exploration.py**

The error at line 756:
```python
result_type = result_data.get('type')
```

This assumes `result_data` is not None. According to the fix summary, this was already addressed at lines 160-170, but clearly it's still happening.

**Location:** `services/data_exploration.py:756`

**Current code might be:**
```python
result_data = pandas_exec.get('result', {})
result_type = result_data.get('type')  # CRASHES if result_data is None
```

**Should be:**
```python
result_data = pandas_exec.get('result', {}) if pandas_exec else {}
result_type = result_data.get('type') if result_data else None
```

But this is a **symptom**, not the root cause. The real issue is that the analysis is failing upstream.

### 2. **workbook_name Not Being Passed**

**Location:** `nl_to_python_workflow.py:604`

The old `data_exploration.py` doesn't pass `workbook_name` to the NL service.

**In logs:**
```
2025-12-16 19:06:15 | ERROR | services.NL_to_python | nl_to_python_workflow.py:604 |
❌ CRITICAL: workbook_name not provided!
```

**Why it happens:**
- The system falls back to old `data_exploration.py` after Polars conversion fails
- Old code doesn't have the parameter in its method signature
- This disables identifier detection → produces wrong results

**Should be using:** `data_exploration_no_chart.py` (which has workbook_name support)

**Is blocked by:** PyArrow error in Polars conversion

---

## Summary

### What Was Attempted:
1. ✅ Boolean string detection and conversion in CSV loader
2. ✅ Disk cache deletion
3. ✅ Python bytecode cache clearing
4. ✅ Multiple server restarts

### Why It Failed:
1. ❌ The boolean conversion code is **not executing** (no logs)
2. ❌ The subset check condition is failing (unknown why)
3. ❌ Data still has `dtype='object'` when it reaches DataManager
4. ❌ PyArrow error still occurs → cascade of failures

### What's Needed:
1. **Debug logging** to see actual column values and why subset check fails
2. **OR hardcode** the list of boolean columns and force conversion
3. **OR fix at DataManager level** to handle object dtype with bool values
4. **OR fix at Polars conversion level** in `data_exploration_no_chart.py`

### The Core Issue:
**The CSV data has string booleans, but the conversion code added to handle them is not being triggered.** This could be due to:
- Unexpected values in the columns
- Different data types than expected
- Code not being executed at all (import/module issues)
- Data being loaded from a different code path

---

## Files Modified

| File | Lines | Status | Purpose |
|------|-------|--------|---------|
| `services/csv_data_loader.py` | 143-161 | Modified | Added boolean string to boolean dtype conversion |

## Files That Need Investigation

| File | Line | Issue |
|------|------|-------|
| `services/data_manager.py` | 258 | `.to_parquet()` fails with PyArrow error |
| `services/data_exploration_no_chart.py` | 183 | `pl.from_pandas()` fails with PyArrow error |
| `services/data_exploration.py` | 756 | NoneType error (symptom) |
| `services/nl_to_python_workflow.py` | 604 | workbook_name not provided (symptom) |

---

**Date:** December 16, 2025, 19:15
**Status:** UNRESOLVED - Boolean conversion not executing despite code being in place
