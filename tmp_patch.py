import os
import sys

# Find site-packages
for p in sys.path:
    target = os.path.join(p, 'pyncm_async', 'apis', 'cloud.py')
    if os.path.exists(target):
        with open(target, 'r', encoding='utf-8') as f:
            code = f.read()
        
        if 'objectKey.replace("/", "%2F")' in code:
            code = code.replace('objectKey.replace("/", "%2F")', "objectKey.replace('/', '%2F')")
            with open(target, 'w', encoding='utf-8') as f:
                f.write(code)
            print('Patched cloud.py successfully!')
            break
else:
    print('cloud.py not found')
