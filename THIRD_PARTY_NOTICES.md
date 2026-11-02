# Third-party components

SignalQC combines the authors' application and trained models with scientific routines and third-party libraries. Bundled components retain their respective copyright and license terms; this document does not relicense them.

- Python and R include their license information in `runtime/python` and `runtime/R` in the portable distribution.
- Python packages include their license metadata in their `.dist-info` directories. The exact portable versions are listed in `runtime/requirements-portable.lock.txt` and `runtime/portable_manifest.json`.
- R packages retain their `DESCRIPTION`, `LICENSE`/`LICENCE` and citation files. Filtering and IM calculation use separate RCTC versions to retain the existing implementations.
- The local SciPy compatibility routine has its license in `engines/sanity/scipy_signal_compat_LICENSE.txt`.
- Original algorithm and model attribution remains in the processing source files and the accompanying manuscript. Preserve those notices when redistributing.

The sample recordings and metadata originate from the UAE National Center of Meteorology (NCM). They are separated from the software in `test` and are not automatically covered by the license of a software dependency.

No blanket license for the authors' code, trained models or NCM data is added by this preparation step. The release owners must state their chosen licensing and permitted sample-data use when publishing the repository.
