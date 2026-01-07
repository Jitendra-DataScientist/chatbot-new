# TODO: Implement Tableau Filter State Capture

## Priority 1: MVP Implementation (Week 1)

### Chrome Extension Tasks
- [ ] Research Tableau JavaScript API
  - [ ] Test `getFiltersAsync()` method on live dashboard
  - [ ] Document available filter properties and methods
  - [ ] Test across different Tableau views (Cloud vs Server)

- [ ] Implement filter capture in content script
  - [ ] Add function to detect when Tableau viz is loaded
  - [ ] Capture active filters using Tableau JS API
  - [ ] Format filter data into standardized JSON structure
  - [ ] Handle errors gracefully (API not available, no filters, etc.)

- [ ] Update data transmission
  - [ ] Add `active_filters` to `tableau_context` object
  - [ ] Ensure data is sent with each query/interaction
  - [ ] Add timestamp for filter capture time

### Backend Tasks
- [ ] Update data models
  - [ ] Define TypedDict/dataclass for filter state schema
  - [ ] Add validation for incoming filter data
  - [ ] Document expected data structure

- [ ] Implement filter application logic
  - [ ] Create `_apply_tableau_context_filters()` method
  - [ ] Handle categorical filters (include/exclude)
  - [ ] Handle date filters
  - [ ] Map Tableau field names to DataFrame column names
  - [ ] Add comprehensive logging

- [ ] Integrate into query workflow
  - [ ] Read filter state from user_frontend_data.json
  - [ ] Apply filters after data load, before query filters
  - [ ] Preserve existing behavior when no Tableau context available
  - [ ] Add debug logging showing filter application

### Testing
- [ ] Unit tests for filter application
  - [ ] Test categorical include filters
  - [ ] Test categorical exclude filters
  - [ ] Test date range filters
  - [ ] Test empty filter list
  - [ ] Test invalid column names

- [ ] Integration tests
  - [ ] Test end-to-end with mocked Tableau context
  - [ ] Verify results match expected filtered data
  - [ ] Test with complex filter combinations

- [ ] Manual testing
  - [ ] Test with "twc count in march" query
  - [ ] Verify result matches 678k when filters active
  - [ ] Test with different filter combinations
  - [ ] Test with no filters (should match current behavior)

---

## Priority 2: Enhanced Features (Week 2-3)

### Filter Types Support
- [ ] Numeric range filters
- [ ] Date range filters (not just categorical dates)
- [ ] Wildcard/pattern matching filters
- [ ] Top N filters
- [ ] Relative date filters (last 7 days, etc.)

### Field Mapping
- [ ] Create robust Tableau field → column name mapper
  - [ ] Handle calculated fields
  - [ ] Handle aliases
  - [ ] Handle fields from blended data sources
- [ ] Build mapping cache/lookup table
- [ ] Handle edge cases (special characters, spaces, etc.)

### User Feedback
- [ ] Add filter state to chat response
  - [ ] Show which filters were applied
  - [ ] Indicate filter source (Tableau vs query)
- [ ] Add warning when filters can't be applied
- [ ] Suggest filter corrections when field not found

---

## Priority 3: Polish & Optimization (Week 4+)

### Performance
- [ ] Benchmark filter application overhead
- [ ] Optimize filter logic for large datasets
- [ ] Consider caching filter results
- [ ] Profile and optimize field mapping

### Robustness
- [ ] Handle multiple worksheets in dashboard
- [ ] Support dashboard-level vs sheet-level filters
- [ ] Handle filter conflicts (Tableau vs query)
- [ ] Add fallback for Tableau API unavailable
- [ ] Comprehensive error handling

### User Experience
- [ ] Allow override of Tableau filters via NL
  - Example: "ignore client filter" or "show all clients"
- [ ] Detect filter changes and refresh context
- [ ] Provide filter summary in UI
- [ ] Add filter debugging mode

### Documentation
- [ ] Document filter capture architecture
- [ ] Create troubleshooting guide
- [ ] Add examples of supported filter types
- [ ] Document limitations and known issues

---

## Quick Wins (Do Immediately)

1. **Verify Tableau API Access**
   - Open Tableau dashboard in browser
   - Open browser console
   - Run: `tableau.VizManager.getVizs()[0].getWorkbook().getActiveSheet().getFiltersAsync()`
   - Document what's returned

2. **Check existing extension code**
   - Review current Chrome extension implementation
   - Identify where to inject filter capture logic
   - Check if Tableau API is already accessible

3. **Create test data**
   - Export small subset of data with known filters
   - Create test cases with expected results
   - Build validation suite

---

## Blockers & Dependencies

### Potential Blockers
- Tableau JavaScript API may not be available in embedded views
- CORS or security policies may block API access
- Filter names may not match column names directly
- Some filter types may not be supported by API

### Dependencies
- Chrome extension must be running when user views dashboard
- User must be viewing Tableau Cloud/Server (not static images)
- Backend must have matching column mappings for workbook

---

## Rollout Strategy

### Phase 1: Internal Testing
- Deploy to dev environment
- Test with known dashboards and queries
- Gather logs and metrics
- Fix critical bugs

### Phase 2: Limited Beta
- Roll out to small user group
- Monitor for errors and edge cases
- Collect user feedback
- Iterate on UX

### Phase 3: Full Deployment
- Roll out to all users
- Monitor performance and accuracy
- Continuous improvement based on usage

---

## Success Criteria

**Must Have:**
- [ ] Filter capture works for 90%+ of common dashboard views
- [ ] Results match Tableau view within 5% for filtered queries
- [ ] Zero regressions for queries without Tableau context
- [ ] Graceful degradation when filters unavailable

**Nice to Have:**
- [ ] Support for all Tableau filter types
- [ ] < 50ms overhead for filter application
- [ ] User-friendly filter state display
- [ ] Natural language filter overrides

---

## Resources Needed

### Development
- 1 Full-stack engineer (Chrome extension + backend)
- Estimated: 2-3 weeks for MVP
- Estimated: 4-6 weeks for full featured release

### Testing
- Access to multiple Tableau dashboards for testing
- Test accounts across different Tableau environments
- Automated testing infrastructure

### Documentation
- Technical documentation for implementation
- User-facing docs for how filters work
- Troubleshooting guide

---

## Open Questions

1. **API Availability**: Can we reliably access Tableau JavaScript API from Chrome extension?
2. **Filter Naming**: How do we map Tableau's internal field names to our column names?
3. **Calculated Fields**: Can we capture filters on calculated fields? How do we apply them?
4. **Multiple Sheets**: How do we handle dashboards with multiple worksheets?
5. **Performance**: What's the performance impact of filter capture and application?
6. **Caching**: Should we cache filter state or capture fresh each time?
7. **Conflicts**: What happens when query specifies filters that conflict with Tableau filters?

---

## Notes

- The aggregation semantics implementation is working correctly - this is purely a filter context issue
- The current system defaults to most recent year when none specified - this is reasonable behavior
- The metadata parsing infrastructure is already in place - we just need to capture VALUES not just SCHEMA
- This solution is scalable and addresses the root cause rather than being a workaround
