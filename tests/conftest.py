import os

# Ensure a deterministic encryption key is present before any crypto import.
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")
