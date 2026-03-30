import sys
import os

# Add the plugin root to sys.path so we can import permission, qq_client, etc. directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
