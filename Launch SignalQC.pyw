"""Double-click with an approved Python installation; no CMD or PowerShell needed."""
import sys
sys.dont_write_bytecode = True

if __name__ == '__main__':
    try:
        from signalqc.launcher import main
        raise SystemExit(main())
    except Exception as exc:
        # Even a missing application folder/module must not disappear silently in pythonw.
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                'SignalQC could not start: ' + str(exc) +
                '\n\nKeep this launcher inside the complete SignalQC_App folder. '
                'Use an organization-approved Python installation.', 'SignalQC startup problem', 0x10)
        raise
