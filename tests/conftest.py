import os
import tempfile

# Every test runs against a throwaway data folder, never the user's.
os.environ.setdefault("LUMINA_DATA", tempfile.mkdtemp(prefix="lumina-test-"))
