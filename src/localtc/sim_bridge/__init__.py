"""Live MSFS bridge over SimConnect (2020 or 2024: the one running). Windows only at runtime.

Nothing outside this package and ``localtc.app`` may import it (enforced by
import-linter and tests/test_architecture.py). Importing is safe on any OS;
only loading ``SimConnect.dll`` needs Windows.
"""
