# May 2013 example

This example contains one earthquake, **11 May 2013 at 02:08:12 UTC** (event `20130511_020812`, magnitude 6.3 Mwp). It includes 18 components: E, N and Z for the HH and HN channels at stations **AE.AJN, AE.ALN and AE.MSF**.

## Run it

1. Extract the complete app folder and open **Launch SignalQC.cmd**, or use one of the Python launchers described in the main README.
2. On the opening **Overview** screen, click **Run example**. SignalQC checks the preloaded inputs, creates a fresh project folder and runs all three stages.
3. When **Results** opens, click **Compare sample results**. Use **Review and run → Open project folder** to find the generated files.

The Reader can also open either supplied HDF5 directly. Running the example does not overwrite the reference files.

## Expected outcomes

| Step | Expected result |
|---|---|
| Input | 1 earthquake, 3 stations, 18 waveform components |
| Stage 1: accept/reject | 18 accepted, 0 rejected |
| Stage 2: filtering | 18 components processed |
| Stage 3: sanity checks | 12 pass, 6 fail |

The sample demonstrates both final QC outcomes, but **no Stage 1 rejections**. The six failed components fail check 7; one also fails check 3. Check 5 is disabled. Review QC before using the processed records for scientific analysis.

These reference results were generated with this public edition on 26 October 2026, using the included Python 3.12.14 and R 4.5.1 runtimes. The original waveforms and scientific methods are preserved.

## Included files

| File or folder | Purpose |
|---|---|
| `input/2013_05.h5` | Prepared input: calibrated acceleration and original counts for clipping checks. |
| `metadata/catalogue.xlsx` | Example catalogue containing this event only; all times are UTC. |
| `metadata/stations.xlsx` | The three sample stations, coordinates and ADC resolution. |
| `sample_settings.json` | The settings loaded by the sample button. |
| `reference_results/2013_05_results.xlsx` | Consolidated scores, selected corners, windows and sanity outcomes; includes a column guide and processing settings. |
| `reference_results/2013_05_processed_2.h5` | The expected trimmed and filtered acceleration, velocity and displacement. |
| `reference_results/Figures/` | Diagnostic figures from the verified run. |
| `reference_manifest.json` | Expected counts, file references and comparison tolerance. |
| `SHA256SUMS.txt` | Checksums for the supplied sample files, for checking transfer integrity. |

The input already has its instrument response removed. **No raw SEED files, response collection or full NCM catalogue/station collection is included.** The catalogue and station workbooks show the format to use with your own raw recordings; the sample starts from prepared HDF5.

## Settings and comparison

The sample uses all eight Stage 1 methods with the vote setting 5, all five enabled corner-picking methods, low-pass minimum/fallback 8 Hz, and sanity checks 1, 2, 3, 4, 6 and 7. USGS is disabled. The sample's high-pass fallback is **0.3 Hz**, matching its earlier processing configuration; the normal new-project default is **0.1 Hz**. The sample button applies 0.3 Hz automatically. It selects two workers.

The comparison checks event/trace identities and categorical outcomes exactly, and numeric result cells and waveform samples with **relative tolerance 1e-7 and absolute tolerance 1e-10**. This allows small floating-point differences; it is not a guarantee that every future library or interpreter will reproduce identical results. Start with the bundled runtime for the closest comparison. Paths, file timestamps and workbook/HDF5 binary layout are not scientific comparison criteria. Missing values must agree in both results.

All 54 processed waveform datasets (18 × acceleration/velocity/displacement) are compared. A blank individual classifier score may reflect early stopping; its adjacent status explains this.

The reference workbook uses relative links, so keep the complete test folder together. To repeat the example in a fresh project, choose **Overview → Run example**.

NCM supplied the sample waveforms and metadata. Their data-access terms remain applicable; the example does not provide access to the wider archive.
