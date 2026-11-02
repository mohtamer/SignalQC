if __name__=='__main__':
    import os
    import sys
    sys.dont_write_bytecode = True
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    from pathlib import Path
    from signalqc.user_paths import user_state_dir
    # pythonw has no console streams. Keep dependency errors and warnings visible in a log.
    startup_log = None
    try:
        startup_log = Path(os.environ.get('SIGNALQC_STARTUP_LOG') or (user_state_dir()/'startup_error.log'))
        with startup_log.open('a',encoding='utf-8') as entry_log:
            entry_log.write(f'SignalQC starting; PID={os.getpid()}; Python={sys.executable}\n')
        if sys.stdout is None or sys.stderr is None:
            log_handle = startup_log.open('a',encoding='utf-8',buffering=1)
            if sys.stdout is None: sys.stdout=log_handle
            if sys.stderr is None: sys.stderr=log_handle
        from signalqc.gui import main
        raise SystemExit(main())
    except Exception:
        import traceback
        if startup_log:
            try:
                with startup_log.open('a',encoding='utf-8') as log: log.write(traceback.format_exc())
            except OSError: pass
        # The Python launcher reports child startup failures; direct/CMD launches need a dialog too.
        if not os.environ.get('SIGNALQC_STARTUP_READY_FILE'):
            from signalqc.launcher import show_message
            show_message('SignalQC could not open',str(sys.exc_info()[1])+f'\n\nStartup log: {startup_log}')
        raise
