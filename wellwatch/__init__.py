"""WellWatch: week-ahead well-failure early warning built on Canary Labs.

Packages:
    wellwatch.config   - settings (Canary host/ports, dataset and tag naming)
    wellwatch.canary   - minimal Canary Read/Write API client
    wellwatch.data     - access to the competition data and the shared cleaning code
    wellwatch.backfill - loads the historical daily data into the Canary Historian
"""

__version__ = "0.1.0"
