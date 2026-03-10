import os
import sys


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.external_skill_candidate_importer import main


if __name__ == "__main__":
    raise SystemExit(main())
