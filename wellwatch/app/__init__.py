"""WellWatch desktop app (PySide6).

Run from the repository root:  python -m wellwatch.app

Layout:
    settings.py     app settings (cache/app_settings.json, no secrets)
    services.py     all data access (Canary, alert store, planner, engine processes); no Qt, testable
    workers.py      background tasks (QThreadPool) so the window never freezes
    theme.py        light/dark palettes and the alert-level colours
    widgets.py      shared widgets (KPI tiles, pandas table model, busy indicator)
    screens/        one module per screen (overview, inbox, well, planner, engine, settings)
    main_window.py  navigation, status bar, tray notifications, shortcuts
"""
