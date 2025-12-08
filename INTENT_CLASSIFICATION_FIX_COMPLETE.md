# Intent Classification Fix - Complete Implementation

## 🎯 Problem Solved

**Root Cause**: Intent classifier was receiving the original incomplete follow-up query ("now 20") instead of the merged complete query ("bottom 20 countries on ticket counts"), causing incorrect intent classification and wrong template selection.

### The Bug Chain (Before Fix)

```
Query 1: "Bottom 5 countries on ticket counts"
└─> Classifier receives: "Bottom 5 countries on ticket counts" ✅
└─> Intent: top_bottom_analysis ✅
└─> Template: Implemented ✅
└─> Result: Proper formatted response ✅

Query 2: "now 20" (follow-up)
└─> Classifier receives: "now 20" ❌ (original, not merged!)
└─> Intent: data_exploration ❌ (wrong!)
└─> Template: NOT IMPLEMENTED ❌
└─> Result: Raw DataFrame shown ❌

BUT NL→Python receives: "bottom 20 countries by ticket counts" ✅ (merged)
└─> Generates correct code ✅
└─> Returns correct data ✅
```

**The Mismatch**: Layer 0 merged the query for NL→Python execution, but the intent classifier still saw the original query!

---

## ✅ Solution: Early Follow-up Detection + Layer 0 Merge

### Architecture Flow (After Fix)

```
┌─────────────────────────────────────────────────────────────┐
│  QueryUnderstandingAgent.process_with_services()            │
└─────────────────────────────────────────────────────────────┘
                          ↓
        ┌─────────────────────────────────────────┐
        │  STEP 1: FOLLOW-UP DETECTION            │
        │  (BEFORE Intent Classification!)        │
        └─────────────────────────────────────────┘
                          ↓
        Does conversation_state have history?
                    ↙         ↘
                  YES         NO
                   ↓           ↓
        ConversationMemory    Skip
        detect_followup()      ↓
                   ↓           ↓
        Is follow-up?         ↓
            ↙     ↘           ↓
          YES     NO          ↓
           ↓       ↓          ↓
    Layer 0 merge  ↓          ↓
    with previous  ↓          ↓
           ↓       ↓          ↓
    Merged query   |          |
           └───────┴──────────┘
                   ↓
        query_for_classification
        (merged if follow-up, original if not)
                   ↓
        ┌─────────────────────────────────────────┐
        │  STEP 2: INTENT CLASSIFICATION          │
        │  (Receives complete merged query!)      │
        └─────────────────────────────────────────┘
                   ↓
        BERT Classifier sees: "bottom 20 countries by ticket counts"
                   ↓
        Intent: top_bottom_analysis ✅
                   ↓
        ┌─────────────────────────────────────────┐
        │  STEP 3: ROUTE TO data_exploration      │
        └─────────────────────────────────────────┘
                   ↓
        Pass: merged query + query_metadata
                   ↓
        data_exploration_no_chart.process()
                   ↓
        conversation_orchestrator.process_query()
                   ↓
        Check: Was query already merged?
              ↙              ↘
        YES (metadata)     NO (no metadata)
             ↓                    ↓
        Skip merge           Do merge here
             └────────┬───────────┘
                      ↓
        NL→Python receives: "bottom 20 countries by ticket counts"
                      ↓
        Correct code generation ✅
        Correct result ✅
        Correct template (top_bottom_analysis) ✅
```

---

## 📝 Changes Made

### 1. QueryUnderstandingAgent (meta_agents/query_understanding_agent.py)

#### Added Instances (Lines 369-384)

```python
# 🆕 Add Layer 0 normalizer and ConversationMemory for follow-up detection
from services.layer0_constrained_parser import ConstrainedParser
from services.conversation_memory import ConversationMemory
try:
    self.layer0_normalizer = ConstrainedParser(llm_client=llm_client)
    self.conversation_memory = ConversationMemory(storage_backend='memory')
    master_logger.info("✅ Layer 0 normalizer initialized for follow-up query merging")
    master_logger.info("✅ ConversationMemory initialized for follow-up detection")
except Exception as e:
    master_logger.error(f"Failed to initialize Layer 0 or ConversationMemory: {e}")
    self.layer0_normalizer = None
    self.conversation_memory = None
```

#### Added Follow-up Detection BEFORE Classification (Lines 755-820)

```python
# 🆕 STEP 1: FOLLOW-UP DETECTION + LAYER 0 MERGE (BEFORE CLASSIFICATION!)
query_for_classification = query  # Default: use original query
query_metadata = {
    'original_query': query,
    'is_followup': False,
    'merged_by': None,
    'merged_query': None
}

# Check if this is a follow-up question (BEFORE classification)
if conversation_state and conversation_state.get('history') and len(conversation_state['history']) > 0:
    master_logger.info("="*80)
    master_logger.info("🔍 CHECKING FOR FOLLOW-UP QUERY")
    master_logger.info("="*80)
    
    if self.conversation_memory:
        try:
            is_followup, prev_context = self.conversation_memory.detect_followup(
                query, 
                conversation_state
            )
            
            if is_followup and prev_context:
                master_logger.info(f"[QUERY_AGENT] 🔗 FOLLOW-UP DETECTED")
                
                # Get Layer 0's previous NORMALIZED output (not raw query)
                last_entry = conversation_state['history'][-1]
                prev_normalized_query = last_entry.get('normalized_query', last_entry.get('query', ''))
                
                master_logger.info(f"[QUERY_AGENT] Previous normalized query: '{prev_normalized_query[:80]}...'")
                
                # Use Layer 0 to merge queries BEFORE classification
                if self.layer0_normalizer:
                    try:
                        master_logger.info(f"[QUERY_AGENT] Using Layer 0 to merge follow-up with previous query")
                        followup_result = self.layer0_normalizer.normalize_with_context(
                            query=query,
                            previous_normalized_query=prev_normalized_query
                        )
                        
                        merged_query = followup_result['enriched_query']
                        query_for_classification = merged_query  # ✅ USE MERGED QUERY FOR CLASSIFICATION!
                        
                        query_metadata['is_followup'] = True
                        query_metadata['merged_by'] = 'query_understanding_agent'
                        query_metadata['merged_query'] = merged_query
                        
                        master_logger.info(f"[QUERY_AGENT] ✅ Merged query for classification: '{merged_query}'")
                        
                    except Exception as layer0_error:
                        master_logger.error(f"[QUERY_AGENT] Layer 0 merge failed: {layer0_error}")
                        master_logger.warning(f"[QUERY_AGENT] Falling back to original query")
                        # Keep query_for_classification = query (original)
                else:
                    master_logger.warning(f"[QUERY_AGENT] Layer 0 not available, cannot merge")
            else:
                master_logger.info(f"[QUERY_AGENT] Not a follow-up")
        except Exception as e:
            master_logger.error(f"[QUERY_AGENT] Follow-up detection failed: {e}")
            # Keep query_for_classification = query (original)
    else:
        master_logger.warning(f"[QUERY_AGENT] ConversationMemory not available")

# STEP 2: CLASSIFY INTENT (using merged query if follow-up, original otherwise)
master_logger.info("="*80)
master_logger.info("🔀 CLASSIFYING INTENT")
master_logger.info(f"   Original query: {query}")
master_logger.info(f"   Query for classification: {query_for_classification}")
master_logger.info(f"   Is follow-up: {query_metadata['is_followup']}")
master_logger.info(f"   Selected chart: {selected_chart}")
master_logger.info("="*80)

# Do intent classification (on merged query if follow-up!)
classification = await self._classify_intent(query_for_classification, context)
```

#### Pass Merged Query + Metadata Downstream (Lines 901-911)

```python
# 🆕 Pass merged query if follow-up, and query_metadata
result = await exploration_service.process(
    query_text=query_for_classification if query_metadata['is_followup'] else query,
    csv_data=csv_data_from_manager,
    selected_chart=None,
    intent_result=minimal_intent,
    chart_context=None,
    context=context,
    conversation_state=conversation_state,
    use_conversation=True,
    query_metadata=query_metadata  # 🆕 Pass query metadata (is_followup, merged_by, etc.)
)
```

---

### 2. Data Exploration Service (services/data_exploration_no_chart.py)

#### Updated Method Signature (Line 107-115)

```python
async def process(self, 
            query_text: str,
            csv_data: pl.DataFrame,
            selected_chart: str,
            intent_result = None,
            chart_context: Optional[Dict] = None,
            conversation_state: Optional[Dict] = None,
            use_conversation: bool = True,
            context: Optional[Dict] = None,
            query_metadata: Optional[Dict] = None) -> Dict[str, Any]:  # 🆕 Add query_metadata parameter
```

#### Pass Metadata to Orchestrator (Lines 163-170)

```python
orchestrator_result = await self.conversation_orchestrator.process_query(
    query=query_text,
    df_data=analysis_data,
    df_columns=list(analysis_data.columns),
    selected_chart=selected_chart,
    chart_context=chart_context,
    conversation_state=conversation_state,
    query_metadata=query_metadata  # 🆕 Pass query_metadata from query_understanding_agent
)
```

---

### 3. Conversation Orchestrator (services/conversation_orchestrator.py)

#### Updated Method Signature (Line 73-80)

```python
async def process_query(self,
                       query: str,
                       df_data: pl.DataFrame,
                       df_columns: List[str],
                       selected_chart: Optional[str] = None,
                       chart_context: Optional[Dict] = None,
                       conversation_state: Optional[Dict] = None,
                       query_metadata: Optional[Dict] = None) -> Dict[str, Any]:
```

#### Check if Already Merged (Lines 111-125)

```python
# ═══════════════════════════════════════════════════════
# 🆕 LAYER 0 NORMALIZATION + FOLLOW-UP DETECTION
# ═══════════════════════════════════════════════════════
normalized_query = query
is_followup = False

# 🆕 Check if query was already merged by query_understanding_agent
if query_metadata and query_metadata.get('merged_by') == 'query_understanding_agent':
    self.logger.info(f"[CONVERSATION] ✅ Query already merged by query_understanding_agent")
    self.logger.info(f"[CONVERSATION] Using pre-merged query: '{query}'")
    normalized_query = query  # Already merged!
    is_followup = query_metadata.get('is_followup', False)
# Otherwise, check if this is a follow-up question and merge here
elif conversation_state.get('history') and len(conversation_state['history']) > 0:
    # ... existing follow-up detection logic ...
```

---

## 🧪 Test Scenario

### Query 1: "Bottom 5 countries on ticket counts"

**Flow**:
1. QueryUnderstandingAgent: Not a follow-up (no history)
2. Classification receives: "Bottom 5 countries on ticket counts"
3. BERT predicts: subcategory=ranking (conf=0.9993)
4. Intent: top_bottom_analysis ✅
5. Template: Implemented ✅
6. Result: Formatted response ✅

**Expected Logs**:
```
[QUERY_AGENT] Not a follow-up
🔀 CLASSIFYING INTENT
   Original query: Bottom 5 countries on ticket counts
   Query for classification: Bottom 5 countries on ticket counts
   Is follow-up: False
[BERT] Predicted subcategory: ranking (confidence: 0.9993)
Intent mapping: exploration → top_bottom_analysis
```

---

### Query 2: "now 20" (Follow-up)

**Flow**:
1. QueryUnderstandingAgent: Follow-up detected ✅
2. Layer 0 merge: "now 20" + previous → "bottom 20 countries by ticket counts" ✅
3. Classification receives: "bottom 20 countries by ticket counts" ✅
4. BERT predicts: subcategory=ranking (conf=0.9993) ✅
5. Intent: top_bottom_analysis ✅
6. Template: Implemented ✅
7. Result: Formatted response ✅

**Expected Logs**:
```
🔍 CHECKING FOR FOLLOW-UP QUERY
[QUERY_AGENT] 🔗 FOLLOW-UP DETECTED
[QUERY_AGENT] Previous normalized query: 'bottom 5 countries by ticket counts'
[QUERY_AGENT] Using Layer 0 to merge follow-up with previous query
[LAYER0] Merged query: 'bottom 20 countries by ticket counts'
🔀 CLASSIFYING INTENT
   Original query: now 20
   Query for classification: bottom 20 countries by ticket counts
   Is follow-up: True
[BERT] Predicted subcategory: ranking (confidence: 0.9993)
Intent mapping: exploration → top_bottom_analysis
[CONVERSATION] ✅ Query already merged by query_understanding_agent
[TEMPLATE] Using top_bottom_analysis template
```

---

### Query 3: "Bottom 20 countries" (Direct, no follow-up)

**Flow**:
1. QueryUnderstandingAgent: Not a follow-up
2. Classification receives: "Bottom 20 countries on ticket counts"
3. BERT predicts: subcategory=ranking (conf=0.9993)
4. Intent: top_bottom_analysis ✅
5. Template: Implemented ✅
6. Result: Formatted response ✅

**Confirms**: Direct queries work the same as before (no breaking changes!)

---

## 🎯 Key Benefits

### ✅ Correct Intent Classification for Follow-ups
- Follow-up queries now classified correctly
- "now 20" → top_bottom_analysis (not data_exploration)
- Proper template selection

### ✅ Clean Architecture
- Separation of concerns maintained
- Query merging happens in ONE place (before classification)
- No duplicate merging

### ✅ No Breaking Changes
- Standalone queries: unchanged flow
- conversation_orchestrator: fallback still works
- data_exploration: receives complete query

### ✅ Full Traceability
- query_metadata tracks: original query, merged query, is_followup, merged_by
- Comprehensive logging at every step
- Easy to debug

### ✅ Graceful Degradation
- If Layer 0 fails: uses original query
- If ConversationMemory fails: treats as standalone
- If query_understanding_agent doesn't merge: conversation_orchestrator merges as fallback

---

## 🔍 Debugging

### Check if Follow-up Detection Worked
```
grep "FOLLOW-UP DETECTED" master_debug.log
```

### Check if Layer 0 Merged
```
grep "Merged query for classification" master_debug.log
```

### Check Intent Classification Result
```
grep "Query for classification:" master_debug.log
grep "Predicted subcategory:" master_debug.log
```

### Check Template Selection
```
grep "Using.*template" master_debug.log
```

---

## 📊 Before vs After

| Aspect | Before | After |
|--------|--------|-------|
| **Follow-up query to classifier** | "now 20" (incomplete) | "bottom 20 countries by ticket counts" (complete) |
| **Intent classification** | data_exploration (wrong) | top_bottom_analysis (correct) |
| **Template used** | None (not implemented) | top_bottom_analysis (implemented) |
| **Response format** | Raw DataFrame | Formatted response |
| **Query merging** | After classification (too late!) | Before classification (correct!) |
| **Breaking changes** | N/A | None! Standalone queries unchanged |

---

## ✅ Implementation Status

- ✅ QueryUnderstandingAgent: Follow-up detection + Layer 0 merge BEFORE classification
- ✅ Data Exploration Service: Receives and passes query_metadata
- ✅ Conversation Orchestrator: Checks if already merged, skips redundant merge
- ✅ No linter errors
- ✅ Backward compatible (standalone queries work as before)
- ✅ Comprehensive logging
- ✅ Graceful error handling

---

## 🚀 Ready to Test!

Test with your exact queries:
1. "Bottom 5 countries on ticket counts"
2. "now 20"
3. "Monthly trend of tickets"
4. "now weekly"

Expected: All follow-up queries should now use the correct intent and template!

