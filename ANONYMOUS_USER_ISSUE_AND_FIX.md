# 🔍 WHY YOU GET "ANONYMOUS" USER - ROOT CAUSE & FIX

**Date:** December 11, 2025  
**Issue:** Backend receives `anonymous_8phn10` / `anonymous@local` instead of real Tableau user  
**Status:** ✅ FIX IMPLEMENTED IN CHROME EXTENSION

---

## 🎯 ROOT CAUSE

### The Chrome Extension Can't Extract Real User Data

**What's happening:**

1. **Chrome extension loads** on Tableau page
2. **Tries to extract user info** from `window.bootstrapData.user`
3. **bootstrapData doesn't exist** (or user not in it)
4. **Falls back to creating fake "anonymous" user**
5. **Sends fake user to backend**
6. **Backend stores and uses fake user**

### Evidence from Logs

```json
{
  "user": {
    "luid": "anonymous_8phn10",        // ← Fake LUID
    "username": "anonymous@local",      // ← Fake username
    "displayName": "Anonymous User",    // ← Fake display name
    "systemUserId": null,
    "domainName": "anonymous"
  }
}
```

---

## 🐛 WHY `bootstrapData` DOESN'T EXIST

Several possible reasons:

### 1. **Tableau Server vs Cloud**
- `window.bootstrapData` exists on **Tableau Cloud**
- May not exist or have different structure on **Tableau Server**

### 2. **Timing Issue**
- Extension loads before Tableau's JavaScript initializes
- `bootstrapData` not yet available when extension runs

### 3. **Embedded Dashboard**
- Dashboard embedded in iframe
- `bootstrapData` in parent frame, not current frame

### 4. **Tableau Version**
- Different Tableau versions expose user data differently
- Older versions may use `tsConfig` instead of `bootstrapData`

### 5. **Security/Sandboxing**
- Browser security blocking access to Tableau's internal objects
- Content Security Policy restrictions

---

## ✅ THE FIX - Multiple User Extraction Methods

### Enhanced Chrome Extension (content-script.js lines 209-340)

**Added 4 methods to extract real user (tries in order):**

#### Method 1: `window.bootstrapData.user` (Tableau Cloud)
```javascript
if (window.bootstrapData && window.bootstrapData.user) {
  const user = window.bootstrapData.user;
  // Extract: luid, username, displayName, systemUserId, domainName
}
```
**Works for:** Tableau Cloud, modern versions

---

#### Method 2: `window.parent.bootstrapData.user` (Embedded Dashboards)
```javascript
if (window.parent && window.parent.bootstrapData && window.parent.bootstrapData.user) {
  const user = window.parent.bootstrapData.user;
  // Extract same fields from parent frame
}
```
**Works for:** Dashboards embedded in iframes

---

#### Method 3: DOM Extraction (Scrape from UI)
```javascript
const userFromDOM = this._extractUserFromDOM();
```

**Tries 3 sub-methods:**

**3a. User menu button:**
```javascript
const userMenuButton = document.querySelector('[data-tb-test-id="account-button"]');
const username = userMenuButton.getAttribute('aria-label');
```

**3b. Meta tags:**
```javascript
const userMeta = document.querySelector('meta[name="user-name"]');
const username = userMeta.getAttribute('content');
```

**3c. User display elements:**
```javascript
const userDisplayElements = document.querySelectorAll('[class*="user"]');
// Find elements containing email addresses
```

**Works for:** When user info is visible in Tableau UI

---

#### Method 4: `window.tsConfig` (Tableau Server)
```javascript
if (window.tsConfig && window.tsConfig.username) {
  const user = {
    luid: window.tsConfig.userId,
    username: window.tsConfig.username,
    displayName: window.tsConfig.displayName,
    ...
  };
}
```
**Works for:** Tableau Server, older versions

---

#### Method 5: Anonymous Fallback (Last Resort)
```javascript
// Only if ALL above methods fail
this.userIdentity = await this._createAnonymousUser();
```
**Creates:** Consistent anonymous user based on browser fingerprint

---

## 🔍 ADDED DIAGNOSTIC LOGGING

When user extraction fails, extension now logs:

```javascript
[SessionManager] ⚠️ Could not extract Tableau user, creating anonymous session
[SessionManager] Checked: bootstrapData, parent.bootstrapData, DOM, tsConfig
[SessionManager] Diagnostic info:
  - window.bootstrapData: undefined MISSING
  - window.parent.bootstrapData: undefined MISSING
  - window.tsConfig: object EXISTS
  - User menu elements: 0
```

**This helps debug WHY real user wasn't found.**

---

## 🧪 HOW TO TEST THE FIX

### Step 1: Reload Extension

1. Go to `chrome://extensions/`
2. Find "Tableau Analysis Assistant"
3. Click reload button 🔄
4. Refresh Tableau page

### Step 2: Check Console Logs

Open DevTools (F12) and look for:

```
[SessionManager] Extracting user identity...
[SessionManager] ✅ User extracted from bootstrapData: your.email@company.com
```

**Or if still anonymous:**

```
[SessionManager] ⚠️ Could not extract Tableau user, creating anonymous session
[SessionManager] Diagnostic info:
  - window.bootstrapData: undefined MISSING
  - window.parent.bootstrapData: undefined MISSING
  - window.tsConfig: object EXISTS
```

### Step 3: Check Backend Logs

After initialization, `app.py` should log:

```
NEW ARCH - User: your.email@company.com, Session: fd8f179d...
```

**If still anonymous:**
```
NEW ARCH - User: anonymous@local, Session: fd8f179d...
```

---

## 🔧 IF STILL GETTING ANONYMOUS

### Option 1: Check What's Available

Open browser console on Tableau page and run:

```javascript
// Check what's available
console.log('bootstrapData:', window.bootstrapData);
console.log('parent.bootstrapData:', window.parent?.bootstrapData);
console.log('tsConfig:', window.tsConfig);
console.log('User buttons:', document.querySelectorAll('[data-tb-test-id="account-button"]'));
```

**Share the output** so we can see what Tableau exposes.

### Option 2: Manual User Injection (Temporary)

If your Tableau instance doesn't expose user data, you can manually configure:

**In `chrome-extension/content-script.js`, line 260:**

```javascript
// TEMPORARY: Hardcode your user info
async _createAnonymousUser() {
  return {
    luid: 'YOUR_ACTUAL_LUID',           // Get from Tableau admin
    username: 'your.email@company.com',  // Your real email
    displayName: 'Your Name',            // Your real name
    systemUserId: 12345,                 // Your Tableau user ID
    domainName: 'company.com'            // Your domain
  };
}
```

**This is a TEMPORARY workaround**, not a production solution.

### Option 3: Backend User Mapping

If user extraction is impossible, map anonymous fingerprints to real users in backend:

**In `app.py`, add user mapping:**

```python
USER_MAPPING = {
    'anonymous_8phn10': {
        'luid': 'real_luid_12345',
        'username': 'user@company.com',
        'displayName': 'Real User Name'
    }
}

# In initialize_tableau, after line 736:
if user_info.get('username') == 'anonymous@local':
    fingerprint = user_info.get('luid', 'unknown')
    if fingerprint in USER_MAPPING:
        user_info = USER_MAPPING[fingerprint]
        master_logger.info(f"Mapped anonymous user to: {user_info['username']}")
```

---

## ✅ SUCCESS CRITERIA

Fix is working when you see:

1. ✅ **Console shows real username:**
   ```
   [SessionManager] ✅ User extracted from bootstrapData: your.email@company.com
   ```

2. ✅ **Backend logs show real user:**
   ```
   NEW ARCH - User: your.email@company.com, Session: ...
   ```

3. ✅ **Session keys use real user ID:**
   ```
   Registration session_key: <real_luid>:workbook:session
   ```

4. ✅ **No more "anonymous_" prefix** in logs

---

## 📝 FILES MODIFIED

| File | Lines | Description |
|------|-------|-------------|
| `chrome-extension/content-script.js` | 209-340 | Added 4 user extraction methods |
| `chrome-extension/content-script.js` | 252-269 | Added diagnostic logging |

---

## 🎓 WHY THIS MATTERS

**Real user data is critical for:**
1. ✅ Audit trails (who queried what)
2. ✅ User-specific dashboards (personalization)
3. ✅ Access control (permissions)
4. ✅ Analytics (user behavior tracking)
5. ✅ Debugging (identify problematic users)

**Anonymous users are OK for:**
- Development/testing
- Public dashboards
- Demo environments

**But NOT for production enterprise deployments.**

---

## 📞 NEXT STEPS

1. **Reload extension** (chrome://extensions → Reload)
2. **Refresh Tableau page**
3. **Check console** for diagnostic logs
4. **Test initialization** and check backend logs
5. **Share diagnostic output** if still getting anonymous

---

**Document Version:** 1.0  
**Status:** ✅ FIX IMPLEMENTED - READY TO TEST  
**Impact:** Real user extraction for proper session isolation

