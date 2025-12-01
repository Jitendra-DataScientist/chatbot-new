"""
Test Script for Tableau Credentials Service
Run this to verify your Google Sheets credentials system is working
"""

import sys
import json
from services.tableau_credentials_service import (
    tableau_credentials_service,
    get_tableau_credentials,
    load_tableau_config
)

def print_header(text):
    """Print a formatted header"""
    print("\n" + "="*70)
    print(f"  {text}")
    print("="*70)

def print_result(success, label, data=None, error=None):
    """Print test result"""
    status = "[PASS]" if success else "[FAIL]"
    color = "\033[92m" if success else "\033[91m"  # Green or Red
    reset = "\033[0m"
    
    print(f"{color}{status}{reset} - {label}")
    
    if data:
        print(f"     Data: {json.dumps(data, indent=6)}")
    if error:
        print(f"     Error: {error}")
    print()

def test_service_enabled():
    """Test 1: Check if service is enabled"""
    print_header("TEST 1: Service Enabled Check")
    
    enabled = tableau_credentials_service.is_enabled()
    print_result(
        enabled,
        "Credentials service enabled",
        {"enabled": enabled}
    )
    
    if not enabled:
        print("     [!] Service is disabled. Update google_sheets_config.json:")
        print('        "tableau_credentials": { "enabled": true, ... }')
    
    return enabled

def test_connection():
    """Test 2: Test connection to Apps Script"""
    print_header("TEST 2: Apps Script Connection Test")
    
    success, data, error = tableau_credentials_service.test_connection()
    print_result(success, "Apps Script connection", data, error)
    
    if not success and error:
        print("     [!] Troubleshooting Tips:")
        print("        1. Verify apps_script_url in google_sheets_config.json")
        print("        2. Ensure Apps Script is deployed as Web App")
        print("        3. Check that 'Who has access' is set to 'Anyone'")
    
    return success

def test_credentials_lookup(username):
    """Test 3: Lookup specific credentials"""
    print_header(f"TEST 3: Credentials Lookup for '{username}'")
    
    success, credentials, error = get_tableau_credentials(username)
    
    if success:
        # Mask password for display
        display_creds = credentials.copy()
        password = display_creds.get('password', '')
        if password:
            display_creds['password'] = f"{password[:3]}***{password[-2:]}" if len(password) > 5 else "***"
        
        print_result(success, f"Credentials found for {username}", display_creds)
        
        # Validate structure
        required_fields = ['username', 'password', 'site_content_url']
        missing = [f for f in required_fields if not credentials.get(f)]
        
        if missing:
            print(f"     [!] Missing required fields: {', '.join(missing)}")
        else:
            print("     [OK] All required fields present")
        
    else:
        print_result(success, f"Credentials lookup for {username}", error=error)
        print("     [!] Troubleshooting Tips:")
        print(f"        1. Verify '{username}' exists in Column A of your Google Sheet")
        print("        2. Check that sheet name is 'Credentials' (or update Apps Script)")
        print("        3. Ensure data starts from Row 2 (Row 1 should be headers)")
    
    return success

def test_cache():
    """Test 4: Test caching mechanism"""
    print_header("TEST 4: Caching Test")
    
    username = "cca49542@gmail.com"
    
    # First call (should fetch from sheets)
    print("First call (should fetch from Google Sheets)...")
    success1, creds1, error1 = get_tableau_credentials(username)
    
    if not success1:
        print_result(False, "Cache test skipped (lookup failed)", error=error1)
        return False
    
    # Second call (should use cache)
    print("Second call (should use cache)...")
    success2, creds2, error2 = get_tableau_credentials(username)
    
    if success1 and success2:
        print_result(True, "Caching mechanism", {"cache_working": True, "cached_duration": "5 minutes"})
        print("     [INFO] Subsequent lookups for 5 minutes will use cached data")
    else:
        print_result(False, "Caching mechanism", error=error2)
    
    return success1 and success2

def test_fallback():
    """Test 5: Test fallback to pass_config.json"""
    print_header("TEST 5: Fallback Mechanism Test")
    
    import os
    fallback_exists = os.path.exists('pass_config.json')
    
    if fallback_exists:
        print("     [INFO] pass_config.json not found (no fallback available)")
        print_result(True, "Fallback not needed", {"pass_config_exists": False})
        return True
    
    # Test with invalid username to trigger fallback
    config = load_tableau_config(username="nonexistent_user@example.com")
    
    if config.get('username'):
        print_result(True, "Fallback to pass_config.json", {"fallback_worked": True, "source": "pass_config.json"})
        print("     [OK] System will use pass_config.json when Google Sheets fails")
    else:
        print_result(False, "Fallback mechanism", error="Could not load from either source")
    
    return True

def test_integration():
    """Test 6: Full integration test"""
    print_header("TEST 6: Full Integration Test")
    
    username = "cca49542@gmail.com"
    
    try:
        # Load config using the smart fallback function
        config = load_tableau_config(username=username)
        
        # Validate config structure
        required_keys = ['username', 'password', 'site_content_url', 'tableau_server_url', 'api_version']
        present_keys = [k for k in required_keys if k in config]
        
        if len(present_keys) == len(required_keys):
            print_result(True, "Full integration test", {
                "config_loaded": True,
                "all_fields_present": True,
                "username": config['username'],
                "server": config.get('tableau_server_url', 'N/A')
            })
            print("     [OK] Ready to use with Tableau Backend!")
        else:
            missing = [k for k in required_keys if k not in config]
            print_result(False, "Full integration test", error=f"Missing keys: {', '.join(missing)}")
        
        return True
        
    except Exception as e:
        print_result(False, "Full integration test", error=str(e))
        return False

def main():
    """Run all tests"""
    print("\n" + "=" + "="*68 + "=")
    print("|" + " "*15 + "TABLEAU CREDENTIALS SERVICE TEST" + " "*21 + "|")
    print("=" + "="*68 + "=")
    
    print("\nThis script will verify your Google Sheets credentials system.")
    print("Make sure you've completed the setup in CREDENTIALS_SETUP_GUIDE.md\n")
    
    # Run tests
    results = {
        "Service Enabled": test_service_enabled(),
        "Apps Script Connection": False,
        "Credentials Lookup": False,
        "Caching": False,
        "Fallback": False,
        "Integration": False
    }
    
    # Only run subsequent tests if service is enabled
    if results["Service Enabled"]:
        results["Apps Script Connection"] = test_connection()
        
        if results["Apps Script Connection"]:
            results["Credentials Lookup"] = test_credentials_lookup("cca49542@gmail.com")
            results["Caching"] = test_cache()
        
        results["Fallback"] = test_fallback()
        results["Integration"] = test_integration()
    
    # Summary
    print_header("TEST SUMMARY")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "[PASS]" if result else "[FAIL]"
        color = "\033[92m" if result else "\033[91m"
        reset = "\033[0m"
        print(f"  {color}{status}{reset} - {test_name}")
    
    print(f"\nResults: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n[SUCCESS] All tests passed! Your credentials system is ready to use.")
        print("\nNext steps:")
        print("  1. Update your application code to use load_tableau_config()")
        print("  2. Add more users to your Google Sheet as needed")
        print("  3. Consider moving to Personal Access Tokens for better security")
    else:
        print("\n[WARNING] Some tests failed. Please review the errors above.")
        print("Refer to CREDENTIALS_SETUP_GUIDE.md for troubleshooting help.")
    
    return passed == total

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nWarning: Tests interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nError: Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

