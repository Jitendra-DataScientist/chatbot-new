# Layer 0 Follow-up Query Implementation

## Overview

Implemented proper follow-up query handling using Layer 0 normalization with LLM-based query merging.

## The Problem (Before)

### Query Flow:
1. User: "Bottom 5 countries on ticket counts"
   - Stored: Raw user query
   - Entity extraction failed (nl_result fields empty)
   - Context saved: Empty entities

2. User: "now 20"
   - Follow-up detected ✅
   - Retrieved: Raw previous query
   - Manual regex parsing attempted ❌
   - Result: "bottom 20" (missing "countries" and "tickets")
   - Wrong output returned

### Root Issues:
- Raw user queries stored instead of normalized Layer 0 output
- Manual regex parsing instead of LLM understanding
- Entity extraction relied on empty nl_result metadata
- Follow-up enrichment had no proper context

## The Solution (After)

### New Query Flow:

**Query 1:** "Bottom 5 countries on ticket counts"
1. Layer 0 normalizes → "bottom 5 countries on ticket counts"
2. Layer 0 normalized output stored as `normalized_query`
3. NL→Python processes normalized query
4. Executes successfully

**Query 2:** "now 20"
1. Follow-up detected ✅
2. Retrieved: Layer 0's **normalized output** from Query 1
3. Layer 0 LLM merges:
   - Previous: "bottom 5 countries on ticket counts"
   - Current: "now 20"
   - Merged: "bottom 20 countries on ticket counts"
4. NL→Python processes merged query
5. Returns correct result ✅

## Changes Made

### 1. Layer 0 - LLM-Based Query Merging

**File:** `services/layer0_constrained_parser.py`

**Updated:** `normalize_with_context()` method

**Before:** Manual regex parsing + string reconstruction
```python
def normalize_with_context(query, previous_query, previous_entities):
    # Detect changes with regex
    changes = _detect_query_changes(query, previous_entities)
    # Merge entities
    merged = previous_entities.copy()
    merged.update(changes)
    # Reconstruct from entities
    return _reconstruct_query(merged)
```

**After:** LLM-based intelligent merging
```python
def normalize_with_context(query, previous_normalized_query, previous_entities=None):
    # Use GPT-4o-mini to merge queries
    prompt = """Previous: "bottom 5 countries on ticket counts"
                Current: "now 20"
                Merge into complete query:"""
    
    response = llm.chat.completions.create(
        model="gpt-4o-mini",
        messages=[system_prompt, user_prompt],
        temperature=0.1
    )
    
    return response.choices[0].message.content  # "bottom 20 countries on ticket counts"
```

**Benefits:**
- ✅ Intelligent understanding of intent
- ✅ Handles complex modifications
- ✅ No fragile regex patterns
- ✅ Cost-effective (GPT-4o-mini)

### 2. Conversation Orchestrator - Layer 0 Integration

**File:** `services/conversation_orchestrator.py`

**Changes:**

**A. ALL queries normalized through Layer 0:**
```python
# Before: Only follow-ups enriched
enriched_query = query
if is_followup:
    enriched_query = enrich_with_context(...)

# After: All queries through Layer 0
if is_followup:
    # Merge with previous Layer 0 output
    normalized_query = layer0.normalize_with_context(
        query=query,
        previous_normalized_query=prev_entry['normalized_query']
    )
else:
    # Standalone normalization
    normalized_query = layer0.normalize(query)
```

**B. Pass normalized query to NL→Python:**
```python
# Before: Mixed queries (some enriched, some raw)
result = execute_pandas_fn(query=enriched_query, ...)

# After: Always normalized
result = execute_pandas_fn(query=normalized_query, ...)
```

**C. Store Layer 0 output:**
```python
# Before: Stored raw query
conversation_memory.add_query(
    query=query,
    enriched_query=enriched_query if enriched_query != query else None
)

# After: Store normalized output
conversation_memory.add_query(
    query=query,  # Original user input
    enriched_query=normalized_query  # Layer 0 normalized output
)
```

### 3. Conversation Memory - Store Normalized Output

**File:** `services/context_manager/conversation_memory.py`

**Changes:**

```python
entry = {
    'query': query,  # Original user query
    'normalized_query': enriched_query if enriched_query else query,  # Layer 0 output (for follow-ups)
    'enriched_query': enriched_query if enriched_query else query,  # Backward compatibility
    'timestamp': ...,
    'entities': ...,
    ...
}
```

**Key:** Store both raw query and normalized output for future reference.

## Architecture

```
Query 1: "Bottom 5 countries on ticket counts"
    ↓
Layer 0: normalize(query)
    ↓ "bottom 5 countries on ticket counts"
NL→Python: Process
    ↓ Success
Store: {
    query: "Bottom 5 countries on ticket counts",
    normalized_query: "bottom 5 countries on ticket counts",
    entities: {...}
}

Query 2: "now 20"
    ↓
Follow-up Detection: ✅ Yes
    ↓
Retrieve: previous['normalized_query']
    ↓ "bottom 5 countries on ticket counts"
Layer 0: normalize_with_context(
    query="now 20",
    previous="bottom 5 countries on ticket counts"
)
    ↓ GPT-4o-mini merges
    ↓ "bottom 20 countries on ticket counts"
NL→Python: Process
    ↓ Success
Return: Bottom 20 countries ✅
```

## Benefits

### 1. **Robust Follow-up Handling**
- LLM understands intent, not just pattern matching
- Handles complex modifications: "what about products", "instead show top", "for closed status"
- No fragile regex patterns to maintain

### 2. **Clean Architecture**
- Layer 0: Query normalization (one responsibility)
- Orchestrator: Workflow coordination
- Memory: Context storage
- Clear separation of concerns

### 3. **No Unnecessary Metadata**
- Don't need nl_result.group_by_columns
- Don't need nl_result.metric_column
- Don't need complex entity extraction
- Just need: previous normalized query + current query

### 4. **Cost-Effective**
- GPT-4o-mini for merging: ~$0.0001 per follow-up
- Only called on follow-ups (maybe 20% of queries)
- 1000 queries → ~200 follow-ups → ~$0.02

### 5. **Maintainable**
- Single LLM prompt to maintain
- No regex patterns to update
- No entity reconstruction logic
- Self-documenting with examples in prompt

## Testing

**Test Case:** "Bottom 5 countries on ticket counts" → "now 20"

**Expected Behavior:**
1. Query 1 returns 5 countries ✅
2. Query 2 detects follow-up ✅
3. Layer 0 merges to "bottom 20 countries on ticket counts" ✅
4. Query 2 returns 20 countries ✅

**What to Verify:**
- Log shows Layer 0 normalization
- Log shows LLM merge
- Log shows merged query
- Result shows correct 20 countries

## Key Takeaway

**The simplest solution:** Let Layer 0 normalize ALL queries and store its output. For follow-ups, pass Layer 0's previous output back to Layer 0 for LLM-based merging.

No complex entity extraction. No manual reconstruction. Just two text strings merged by LLM.

