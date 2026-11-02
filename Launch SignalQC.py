
import sys
sys.dont_write_bytecode = True

if __name__ == '__main__':
    try:
        from signalqc.launcher import main
        raise SystemExit(main())
    except Exception as exc:
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                'SignalQC could not start: ' + str(exc) +
                '\n\nKeep this launcher inside the complete SignalQC_App folder. '
                'Open the launcher using Python 3.11 or newer, or use the included runtime/python/python.exe.', 'SignalQC startup problem', 0x10)
        raise
