import json

# Read the user frontend data
with open('user_frontend_data.json', encoding='utf-8') as f:
    data = json.load(f)

# Check the structure
print(f"Data type: {type(data)}")
if isinstance(data, list) and len(data) > 0:
    print(f"List length: {len(data)}")
    print(f"First item type: {type(data[0])}")
    if isinstance(data[0], dict):
        print(f"First item keys: {list(data[0].keys())[:10]}")

        # Look for chatState or relevant info
        for item in data[:5]:
            if 'chatState' in item:
                print(f"\nFound chatState!")
                state = item['chatState']
                print(f"  Workbook: {state.get('workbookName', 'N/A')}")
                print(f"  Selected chart: {state.get('selectedChart', 'N/A')}")
                print(f"  Filters: {list(state.get('filters', {}).keys()) if 'filters' in state else 'None'}")
                break
elif isinstance(data, dict):
    print(f"Keys: {list(data.keys())[:20]}")
