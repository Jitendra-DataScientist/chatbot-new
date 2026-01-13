"""
Phase 2 Implementation Script
Adds dashboard filter parameter handling to Flask API

This script modifies app.py to extract and pass filter parameters
from the request to the EnhancedChatRequest object.

Usage:
    python implement_phase2.py

The script will:
1. Read app.py
2. Insert filter parameter extraction code
3. Update EnhancedChatRequest instantiations
4. Create backup of original file
5. Write modified file

Before running, ensure:
- Phase 1 is complete and tested
- app.py exists in the current directory
"""

import os
import re
import shutil
from datetime import datetime

def backup_file(filepath):
    """Create a timestamped backup of the file"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{filepath}.backup_{timestamp}"
    shutil.copy2(filepath, backup_path)
    print(f"✅ Backup created: {backup_path}")
    return backup_path

def read_file(filepath):
    """Read file content"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()

def write_file(filepath, content):
    """Write content to file"""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"✅ File written: {filepath}")

def insert_filter_extraction(content):
    """Insert filter parameter extraction code after chart_context extraction"""

    # Find the location after chart_context extraction
    pattern = r'(chart_context = data\.get\("chart_context", \{\}\)\s*\n)'

    insertion_code = '''
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

'''

    # Check if already inserted
    if "Phase 2: Extract dashboard filter parameters" in content:
        print("⚠️  Filter extraction code already present, skipping...")
        return content, False

    modified_content = re.sub(pattern, r'\1' + insertion_code, content)

    if modified_content != content:
        print("✅ Inserted filter parameter extraction code")
        return modified_content, True
    else:
        print("❌ Could not find insertion point for filter extraction")
        return content, False

def update_enhanced_chat_request(content):
    """Add filter parameters to EnhancedChatRequest instantiation"""

    # Pattern to find EnhancedChatRequest instantiation (regular queries)
    # Looking for: source=source followed by closing parenthesis
    pattern1 = r'(chat_request = EnhancedChatRequest\([^)]+source=source)\s*(\))'

    addition = ''',
            # Phase 2: Dashboard filter support
            use_dashboard_filters=use_dashboard_filters,
            dashboard_filters=dashboard_filters,
            query_filters=query_filters'''

    # Check if already added
    if "# Phase 2: Dashboard filter support" in content and "use_dashboard_filters=use_dashboard_filters" in content:
        print("⚠️  EnhancedChatRequest already updated, skipping...")
        return content, False

    modified_content = re.sub(pattern1, r'\1' + addition + r'\2', content, count=1)

    if modified_content != content:
        print("✅ Updated EnhancedChatRequest instantiation (regular queries)")

        # Also update AUTO_ANALYSIS EnhancedChatRequest
        # Pattern to find auto_analysis_request instantiation
        pattern2 = r'(auto_analysis_request = EnhancedChatRequest\([^)]+source=source)\s*(\))'

        modified_content = re.sub(pattern2, r'\1' + addition + r'\2', modified_content, count=1)

        if "auto_analysis_request = EnhancedChatRequest" in modified_content:
            print("✅ Updated EnhancedChatRequest instantiation (AUTO_ANALYSIS)")

        return modified_content, True
    else:
        print("❌ Could not find EnhancedChatRequest instantiation to update")
        return content, False

def main():
    """Main implementation function"""
    print("=" * 80)
    print("Phase 2 Implementation: Dashboard Filter API Integration")
    print("=" * 80)
    print()

    # File path
    app_py_path = "app.py"

    # Check if file exists
    if not os.path.exists(app_py_path):
        print(f"❌ ERROR: {app_py_path} not found in current directory")
        print(f"   Current directory: {os.getcwd()}")
        return False

    print(f"📄 Found {app_py_path}")

    # Create backup
    print("\n📦 Creating backup...")
    backup_path = backup_file(app_py_path)

    # Read original content
    print("\n📖 Reading original file...")
    original_content = read_file(app_py_path)
    print(f"   File size: {len(original_content)} bytes")

    # Apply modifications
    print("\n🔧 Applying modifications...")
    print()

    # Modification 1: Insert filter extraction
    modified_content, changed1 = insert_filter_extraction(original_content)

    # Modification 2: Update EnhancedChatRequest
    modified_content, changed2 = update_enhanced_chat_request(modified_content)

    # Check if any changes were made
    if not (changed1 or changed2):
        print("\n⚠️  No modifications were applied.")
        print("   This could mean:")
        print("   - Phase 2 changes are already implemented")
        print("   - The code structure has changed (manual review needed)")
        return False

    # Write modified content
    print("\n💾 Writing modified file...")
    write_file(app_py_path, modified_content)

    # Summary
    print()
    print("=" * 80)
    print("✅ Phase 2 Implementation Complete!")
    print("=" * 80)
    print()
    print("Changes made:")
    if changed1:
        print("  ✅ Added filter parameter extraction from request")
    if changed2:
        print("  ✅ Updated EnhancedChatRequest instantiations with filter params")
    print()
    print("Next steps:")
    print("  1. Review the changes in app.py")
    print("  2. Run the server and test with existing queries (backward compatibility)")
    print("  3. Test with filter parameters (see PHASE2_IMPLEMENTATION_PLAN.md)")
    print("  4. Check master_debug.log for filter application logs")
    print()
    print(f"Backup saved to: {backup_path}")
    print()

    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
