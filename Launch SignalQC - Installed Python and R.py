"""Optional first-run setup using the computer's installed Python and R."""
import sys
sys.dont_write_bytecode = True

# Optional: paste actual paths between the quotes. Leaving them blank opens
# the setup window, where you can browse or paste the paths without editing code.
PYTHON_EXE = r""
RSCRIPT_EXE = r""

if __name__ == '__main__':
    from signalqc.external_setup_v1 import main
    raise SystemExit(main(PYTHON_EXE, RSCRIPT_EXE))
