"""Back-compat shim: the one-liner analyst now lives in `diracdata.agents`.

Import from `diracdata.agents` in new code.
"""

from diracdata.agents.analyst import DataAnalyst, data_analyst

__all__ = ["data_analyst", "DataAnalyst"]
