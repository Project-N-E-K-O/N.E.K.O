import json
import os
import sys

def sync_dict(base_dict, target_dict):
    """Recursively sync dictionary keys from base to target."""
    added_count = 0
    for key, value in base_dict.items():
        if key not in target_dict:
            # If the key is missing in target, add it
            target_dict[key] = value
            added_count += 1
        else:
            # If the key exists and value is a dict, recurse
            if isinstance(value, dict) and isinstance(target_dict[key], dict):
                added_count += sync_dict(value, target_dict[key])
    return added_count

def sort_dict(base_dict, target_dict):
    """Sort the target dictionary to match the order of the base dictionary."""
    sorted_dict = {}
    for key in base_dict:
        if key in target_dict:
            if isinstance(base_dict[key], dict) and isinstance(target_dict[key], dict):
                sorted_dict[key] = sort_dict(base_dict[key], target_dict[key])
            else:
                sorted_dict[key] = target_dict[key]
    # Add any extra keys that might exist in target but not in base (optional, normally we might delete them)
    # the user just wants to ensure the new ones are added. We will preserve extra keys at the end.
    for key in target_dict:
        if key not in sorted_dict:
            sorted_dict[key] = target_dict[key]
    return sorted_dict

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    locales_dir = os.path.join(os.path.dirname(script_dir), 'static', 'locales')
    
    zh_path = os.path.join(locales_dir, 'zh-CN.json')
    if not os.path.exists(zh_path):
        print(f"Error: {zh_path} not found")
        sys.exit(1)
        
    with open(zh_path, 'r', encoding='utf-8') as f:
        zh_data = json.load(f)
        
    targets = ['en.json', 'ja.json', 'ko.json', 'zh-TW.json']
    
    for target in targets:
        target_path = os.path.join(locales_dir, target)
        if not os.path.exists(target_path):
            print(f"Warning: {target_path} not found, skipping.")
            continue
            
        with open(target_path, 'r', encoding='utf-8') as f:
            try:
                target_data = json.load(f)
            except json.JSONDecodeError:
                print(f"Error decoding {target_path}. Skip.")
                continue
                
        added = sync_dict(zh_data, target_data)
        
        # Sort keys to match zh-CN.json
        target_data = sort_dict(zh_data, target_data)
        
        if added > 0:
            with open(target_path, 'w', encoding='utf-8') as f:
                json.dump(target_data, f, ensure_ascii=False, indent=2)
            print(f"Synced {added} missing keys to {target}. Done sorting.")
        else:
            # Just re-sort to be safe and format properly
            with open(target_path, 'w', encoding='utf-8') as f:
                json.dump(target_data, f, ensure_ascii=False, indent=2)
            print(f"{target} was already up to date, just re-sorted and formatted.")

if __name__ == "__main__":
    main()
