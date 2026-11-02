"""Double-click to check Python availability without a command prompt."""
import sys
sys.dont_write_bytecode = True

if __name__ == '__main__':
    try:
        from signalqc.launcher import main
        raise SystemExit(main(['--diagnose', *sys.argv[1:]]))
    except Exception as exc:
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc) +
                '\n\nKeep this file inside the complete SignalQC_App folder.', 'SignalQC check failed', 0x10)
        raise
