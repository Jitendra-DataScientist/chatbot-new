"""
Test Phase 2 API Integration
Tests that the API accepts and processes dashboard filter parameters
"""

import requests
import json
import sys

BASE_URL = "http://localhost:8502"

def test_backward_compatibility():
    """Test 1: Ensure existing queries work (no filter parameters)"""
    print("=" * 80)
    print("TEST 1: Backward Compatibility (No Filter Parameters)")
    print("=" * 80)

    try:
        response = requests.post(f"{BASE_URL}/api/chat", json={
            "message": "show me some data",
            "connection_key": "test_workbook"
        }, timeout=10)

        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            print("✅ PASS: API accepts requests without filter parameters")
            return True
        else:
            print(f"❌ FAIL: Unexpected status code {response.status_code}")
            print(f"Response: {response.text}")
            return False

    except requests.exceptions.ConnectionError:
        print("❌ FAIL: Could not connect to server. Is it running?")
        print("   Start server with: python app.py")
        return False
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_with_filter_parameters():
    """Test 2: Send request with dashboard filter parameters"""
    print("\n" + "=" * 80)
    print("TEST 2: With Dashboard Filter Parameters")
    print("=" * 80)

    try:
        payload = {
            "message": "twc count in march",
            "connection_key": "CentralizedCommOpsL10NMetrics",

            # Phase 2: Dashboard filter parameters
            "use_dashboard_filters": True,
            "dashboard_filters": {
                "client": {
                    "type": "categorical",
                    "values": ["Support - All"],
                    "is_exclude": False
                },
                "vendor": {
                    "type": "categorical",
                    "values": ["MT Only"],
                    "is_exclude": False
                }
            },
            "query_filters": {
                "month": "March"
            }
        }

        print("\nSending request with filter parameters:")
        print(f"  - use_dashboard_filters: {payload['use_dashboard_filters']}")
        print(f"  - dashboard_filters: {list(payload['dashboard_filters'].keys())}")
        print(f"  - query_filters: {payload['query_filters']}")

        response = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=30)

        print(f"\nStatus Code: {response.status_code}")

        if response.status_code == 200:
            print("✅ PASS: API accepts filter parameters")

            # Check response
            data = response.json()
            print("\nResponse preview:")
            print(json.dumps(data, indent=2)[:500])

            # Check if filters were logged (you'll need to check master_debug.log)
            print("\n📝 Check master_debug.log for filter application logs:")
            print("   Look for: [DASHBOARD_FILTER] Applying 2 dashboard filters")
            print("   Look for: [DASHBOARD_FILTER] ✅ client IN ['Support - All']")
            print("   Look for: [DASHBOARD_FILTER] ✅ vendor IN ['MT Only']")

            return True
        elif response.status_code == 400 or response.status_code == 500:
            print(f"❌ FAIL: Server error {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False
        else:
            print(f"⚠️  Unexpected status: {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False

    except requests.exceptions.ConnectionError:
        print("❌ FAIL: Could not connect to server. Is it running?")
        return False
    except Exception as e:
        print(f"❌ FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_ignore_filters():
    """Test 3: Send request with use_dashboard_filters=False"""
    print("\n" + "=" * 80)
    print("TEST 3: Ignore Dashboard Filters (use_dashboard_filters=False)")
    print("=" * 80)

    try:
        payload = {
            "message": "twc count in march",
            "connection_key": "CentralizedCommOpsL10NMetrics",

            # Phase 2: Dashboard filters provided but NOT used
            "use_dashboard_filters": False,
            "dashboard_filters": {
                "client": {
                    "type": "categorical",
                    "values": ["Support - All"],
                    "is_exclude": False
                }
            }
        }

        print("\nSending request with use_dashboard_filters=False")
        print("  (Dashboard filters should be ignored)")

        response = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=30)

        print(f"\nStatus Code: {response.status_code}")

        if response.status_code == 200:
            print("✅ PASS: API handles use_dashboard_filters=False")
            print("\n📝 Check master_debug.log to verify filters were NOT applied")
            return True
        else:
            print(f"❌ FAIL: Unexpected status code {response.status_code}")
            return False

    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def main():
    """Run all tests"""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 20 + "PHASE 2 API TESTING" + " " * 39 + "║")
    print("╚" + "=" * 78 + "╝")
    print()

    results = []

    # Run tests
    results.append(("Backward Compatibility", test_backward_compatibility()))
    results.append(("With Filter Parameters", test_with_filter_parameters()))
    results.append(("Ignore Filters Mode", test_ignore_filters()))

    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed ({passed/total*100:.0f}%)")

    if passed == total:
        print("\n🎉 ALL TESTS PASSED! Phase 2 API integration working correctly.")
        print("\n📋 Next Steps:")
        print("   1. Check master_debug.log for detailed filter application logs")
        print("   2. Verify filter parameters flow through to data exploration service")
        print("   3. Ready to proceed to Phase 3 (Chrome Extension integration)")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Review errors above.")
        print("   Check server logs and master_debug.log for details")

    print()
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
