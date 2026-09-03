"""Environment shims.

librosa 0.10.1 calls scipy.signal.hann, which scipy >= 1.13 moved to
scipy.signal.windows.hann. Patch it here instead of upgrading librosa, because
other tools in this machine's Python environment pin against the installed
versions. Import this module before importing librosa anywhere.
"""

import warnings

import scipy.signal

if not hasattr(scipy.signal, "hann"):
    scipy.signal.hann = scipy.signal.windows.hann

warnings.filterwarnings("ignore", message="pkg_resources is deprecated")

SHIMS_APPLIED = ["scipy.signal.hann -> scipy.signal.windows.hann"]
