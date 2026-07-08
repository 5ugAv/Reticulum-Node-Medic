import os
import sys

# Ensure the project root is importable so tests can `import node_profile`,
# `from transport.connection import ...`, etc. regardless of invocation cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
