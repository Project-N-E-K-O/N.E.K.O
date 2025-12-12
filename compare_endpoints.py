"""
Comprehensive endpoint comparison between old main_server.py and new routers.
This script handles router prefixes correctly by applying them.
"""
import subprocess
import re
import os
from pathlib import Path
from collections import defaultdict

def extract_endpoints_from_content(content, decorator_prefix='app'):
    """Extract endpoints from Python file content"""
    pattern = rf'@{decorator_prefix}\.(get|post|websocket|put|delete)\s*\(["\']([^"\'\s\),]+)'
    matches = re.findall(pattern, content)
    return list(matches)

def get_router_prefix(content):
    """Extract router prefix from router file"""
    match = re.search(r'APIRouter\s*\([^)]*prefix\s*=\s*["\']([^"\']+)', content)
    return match.group(1) if match else ''

def normalize_path(path):
    """Normalize path for comparison"""
    path = path.rstrip('/').lower()
    if not path:
        path = '/'
    # Normalize path variable syntax
    path = re.sub(r'\{[^}]+\}', '{VAR}', path)
    return path

# Get old endpoints from git
print("Reading old main_server.py from git...")
result = subprocess.run(['git', 'show', 'c122403:main_server.py'], capture_output=True)
old_content = result.stdout.decode('utf-8', errors='ignore')
old_raw_endpoints = extract_endpoints_from_content(old_content, 'app')

# Create structured old endpoints
old_endpoints = {}
for method, path in old_raw_endpoints:
    key = (method.lower(), normalize_path(path))
    old_endpoints[key] = path

# Get new endpoints from all routers
print("Reading router files...")
new_endpoints = {}
router_details = {}

router_files = list(Path('main_routers').glob('*.py'))
for router_file in router_files:
    if router_file.name in ('__init__.py', 'shared_state.py'):
        continue
    
    with open(router_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    prefix = get_router_prefix(content)
    endpoints = extract_endpoints_from_content(content, 'router')
    
    for method, path in endpoints:
        # Apply prefix to get full path
        if path.startswith('/'):
            # Path is already absolute, but needs prefix if present
            full_path = prefix + path if prefix and not path.startswith(prefix) else path
        else:
            full_path = prefix + '/' + path if prefix else '/' + path
        
        # Special case: if path already starts with /api and prefix is /api, don't double it
        if prefix and path.startswith(prefix):
            full_path = path
        
        key = (method.lower(), normalize_path(full_path))
        new_endpoints[key] = (full_path, router_file.name)
        
        if router_file.name not in router_details:
            router_details[router_file.name] = {'prefix': prefix, 'endpoints': []}
        router_details[router_file.name]['endpoints'].append((method, full_path))

# Compare
missing_in_new = []
found_in_new = []
extra_in_new = []

for (method, norm_path), orig_path in old_endpoints.items():
    if (method, norm_path) in new_endpoints:
        found_in_new.append((method, orig_path, new_endpoints[(method, norm_path)][0], new_endpoints[(method, norm_path)][1]))
    else:
        missing_in_new.append((method, orig_path))

for (method, norm_path), (full_path, router_file) in new_endpoints.items():
    if (method, norm_path) not in old_endpoints:
        extra_in_new.append((method, full_path, router_file))

# Write report
with open('endpoint_audit_report.txt', 'w', encoding='utf-8') as f:
    f.write("=" * 80 + "\n")
    f.write("ENDPOINT MIGRATION AUDIT REPORT\n")
    f.write("=" * 80 + "\n\n")
    
    f.write(f"Old main_server.py endpoints: {len(old_endpoints)}\n")
    f.write(f"New routers endpoints: {len(new_endpoints)}\n")
    f.write(f"Successfully migrated: {len(found_in_new)}\n")
    f.write(f"Missing from routers: {len(missing_in_new)}\n")
    f.write(f"Extra in routers (new): {len(extra_in_new)}\n\n")
    
    if missing_in_new:
        f.write("=" * 80 + "\n")
        f.write("❌ MISSING ENDPOINTS (need to be added to routers):\n")
        f.write("=" * 80 + "\n")
        for method, path in sorted(missing_in_new, key=lambda x: x[1]):
            f.write(f"  {method.upper():10} {path}\n")
        f.write(f"\nTotal missing: {len(missing_in_new)}\n\n")
    else:
        f.write("✅ All endpoints migrated successfully!\n\n")
    
    if extra_in_new:
        f.write("=" * 80 + "\n")
        f.write("➕ EXTRA ENDPOINTS (new in routers, not in old):\n")
        f.write("=" * 80 + "\n")
        for method, path, router in sorted(extra_in_new, key=lambda x: x[1]):
            f.write(f"  {method.upper():10} {path} [{router}]\n")
        f.write(f"\nTotal extra: {len(extra_in_new)}\n\n")
    
    # Show detailed analysis
    f.write("=" * 80 + "\n")
    f.write("DETAILED ANALYSIS BY CATEGORY:\n")
    f.write("=" * 80 + "\n")
    
    # Group missing by prefix
    categories = defaultdict(list)
    for method, path in missing_in_new:
        parts = path.split('/')
        if len(parts) >= 3:
            cat = '/'.join(parts[:3])  # e.g., /api/agent
        else:
            cat = path
        categories[cat].append((method, path))
    
    for cat in sorted(categories.keys()):
        f.write(f"\n{cat}:\n")
        for method, path in sorted(categories[cat]):
            f.write(f"  {method.upper():10} {path}\n")
    
    f.write("\n" + "=" * 80 + "\n")
    f.write("NEW ROUTER FULL PATHS:\n")
    f.write("=" * 80 + "\n")
    for router_file, details in sorted(router_details.items()):
        f.write(f"\n{router_file} (prefix: '{details['prefix']}')\n")
        f.write("-" * 40 + "\n")
        for method, path in sorted(details['endpoints'], key=lambda x: x[1]):
            f.write(f"  {method.upper():10} {path}\n")

print("Report written to endpoint_audit_report.txt")
print(f"\nSummary:")
print(f"  - Old endpoints: {len(old_endpoints)}")
print(f"  - New endpoints: {len(new_endpoints)}")
print(f"  - Missing: {len(missing_in_new)}")
print(f"  - Extra: {len(extra_in_new)}")
