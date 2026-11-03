# Third-party components

Mohamed T. Hussein and Tadahiro Kishida developed SignalQC. The third-party software distributed with SignalQC retains its respective copyright notices and license terms.

- Python and R include their license information in `runtime/python` and `runtime/R` in the portable distribution.
- Python packages include their license metadata in their `.dist-info` directories. The exact portable versions are listed in `runtime/requirements-portable.lock.txt` and `runtime/portable_manifest.json`.
- R packages retain their `DESCRIPTION`, `LICENSE`/`LICENCE` and citation files. Filtering uses the included RCTC 0.1.0.
- The local SciPy compatibility routine has its license in `engines/sanity/scipy_signal_compat_LICENSE.txt`.
- The processing source files and the accompanying manuscript credit the original algorithms and models.

The UAE National Center of Meteorology (NCM) supplied the sample recordings and metadata in `test`.
