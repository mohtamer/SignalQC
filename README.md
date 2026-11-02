# SignalQC

SignalQC is a Windows desktop application for automated seismic waveform processing and quality control. It guides users through accept/reject screening, corner-frequency selection, filtering and trimming, baseline correction, and sanity checks.

**Developed by Mohamed T. Hussein & Tadahiro Kishida.**

Repository: [github.com/mohtamer/SignalQC](https://github.com/mohtamer/SignalQC)

## Start here

**[Download the complete Windows app](https://github.com/mohtamer/SignalQC/releases/download/v2026.11.02/SignalQC_Public_2026-11-02.zip)** for offline use, or follow the source instructions below.

| Package | How to open it | What you need |
|---|---|---|
| **Windows portable ZIP** | Extract the entire ZIP to a local folder, then double-click **Launch SignalQC.cmd**. | The ZIP includes Python, R and their packages. No separate installation, PyCharm or MATLAB is required. |
| **GitHub source ZIP / cloned repository** | Run **Launch SignalQC - Installed Python and R.py** with your installed Python. Browse to `python.exe` and `Rscript.exe`, then choose **Set up and open SignalQC**. | Standard 64-bit Windows Python 3.11–3.14, R 4.5 or later, and internet access for the first package setup. Startup checks must pass. |

The portable package also includes **Launch SignalQC.py** and **Launch SignalQC.pyw**. These select the bundled runtime even when opened with another Python interpreter. The separate **Installed Python and R** launcher uses the executables you select and installs packages into your own user folder. Use that launcher again for subsequent sessions.

The reference environment is Python **3.12.14** and R **4.5.1**. Later interpreter versions require compatible compiled packages; support for every future version is not implied. A university or employer may need to permit the executables and package libraries. The launchers do not require administrator elevation or bypass organizational restrictions.

**Keep the complete folder together.** Do not run files from inside the ZIP. Choose a writable local folder for processing; allow cloud synchronization to finish after a run rather than editing active HDF5 files simultaneously on multiple computers.

## Try the included example

The `test` folder contains one earthquake: **11 May 2013, 02:08:12 UTC**, magnitude **6.3 Mwp**. There are **18 waveform components** from **AE.AJN, AE.ALN and AE.MSF**, including HH and HN recordings. The input HDF5 already contains response-corrected acceleration and the original counts required by the clipping check. Instrument-response files are therefore unnecessary for this demonstration.

1. Open SignalQC and select **Process existing HDF5 files**.
2. Go to **Input data** and click **Use included sample inputs**. This loads the HDF5, station sheet and recorded processing settings.
3. Click **Save project** and choose a **new empty folder**, for example `Documents/SignalQC sample run`. Keep this outside the supplied `test` folder.
4. Go to **Review and run**, scan the inputs, review the counts, then start processing. Leave the sample settings unchanged for comparison.
5. When processing finishes, open **Results**. Compare your results with `test/reference_results` using **Compare sample results**. You can also use **Browse existing results folder** to view the reference tables and HDF5 directly.

The reference outcome is **18 accepted components at Stage 1**, followed by **12 passes and 6 failures at Stage 3**. This example demonstrates rejection during the sanity checks; it does not demonstrate a Stage 1 rejection. A QC failure is a recorded scientific outcome, not an application failure.

The sample preserves the earlier run's **0.3 Hz high-pass fallback**. The normal new-project default remains **0.1 Hz**. The sample button applies the recorded value automatically. The low-pass minimum/fallback is **8 Hz**, all eight accept/reject methods and all five corner methods are enabled, the vote setting is **5**, and sanity check **5 is disabled**. Sample settings are recorded in `test/sample_settings.json`.

Expected numerical values are supplied in the reference workbooks and processed HDF5. Compare decisions and identifiers exactly; compare numeric outputs with the documented tolerance in [the sample guide](test/README.md). File paths, timestamps, worker ordering and ZIP/HDF5 binary hashes can differ between computers. The comparison reports those distinctions rather than treating different output paths as scientific differences.

## Use your own data

Start with **Select task**. The following screens show only the inputs required for that task.

| Task | Inputs |
|---|---|
| **Process existing HDF5 files** | HDF5 containing calibrated acceleration, sampling rate and usable record timing. Metadata stored in the HDF5 are used directly. |
| **Create HDF5 files** | Raw recordings, an earthquake catalogue, and instrument responses when converting counts. Station coordinates may come from a station sheet or response inventory. |
| **Create HDF5 files and process** | The conversion inputs above; select the processing stages to run afterwards. |
| **Reader** | Any supported existing HDF5. Browse its structure and plot waveforms without starting processing or creating a project. |

For raw recordings, **Add folder** searches subfolders. Supported ObsPy formats include SEED, miniSEED, SAC and recognized `.output` recordings. A filename extension alone does not guarantee a valid waveform format. Files may contain one trace or many stations/components. Enable **Attempt decoding files with unrecognized extensions** only when needed.

For a different HDF5 structure, select **My HDF5 files use a different structure**, then **Review data mapping**. Match the waveform dataset, sampling information, units and available metadata. Review automatic suggestions before applying them. Normalization writes a working copy; source data are preserved. Missing optional identity labels do not prevent processing, but scientifically required information such as sampling rate and calibrated units must be available. Checks that lack their required inputs are reported explicitly.

### Catalogue and station spreadsheets

Use `test/metadata/catalogue.xlsx` and `test/metadata/stations.xlsx` as small examples. Replace the sample rows with your own data in your own copies. They contain **one event and three stations only**. There is no bundled full NCM catalogue, station database or response collection.

| Catalogue column | Meaning |
|---|---|
| `event_id` | Your stable earthquake identifier. |
| `origin_time` | UTC origin date and time. Separate year/month/day/hour/minute/second columns are also supported. |
| `latitude`, `longitude` | Event coordinates in decimal degrees. |
| `depth_km` | Event depth in kilometres. |
| `magnitude`, `magnitude_type` | Magnitude and, when known, its type. |
| `file name` | Optional raw-event filename or stem, e.g. `event001` or `event001.mseed`. Blank in the prepared-HDF5 demonstration. |

| Station column | Meaning |
|---|---|
| `network`, `station` | Acquisition codes matching the waveform headers. Preserve leading zeros in identifier fields as text. |
| `latitude`, `longitude` | Station coordinates in decimal degrees. |
| `elevation_m` | Station elevation in metres; retained as metadata. It is not used in hypocentral distance. |
| `bits` | ADC resolution, when known, for the clipping check. If absent, the configured fallback is used and reported. |
| `location`, `channel`, `start_date`, `end_date` | Optional qualifiers for channel- or epoch-specific metadata. |

Common column names are matched automatically. Use **Map event columns** or **Map station columns** if the headings differ. Numeric text such as `10 km` is recognized when unambiguous; ambiguous values are flagged rather than guessed. Save changes to Excel before scanning: the selected catalogue is read again for the run.

Raw-event association tries normalized catalogue filenames first. For unmatched recordings, it considers events from **2 minutes before to 1 hour after the trace start**. When several events match, the full untrimmed record is associated with each matched event. Review the scan before running to catch unintended associations.

If recordings are in instrument counts, supply the correct RESP, dataless SEED or StationXML response files and enable response removal. If recordings are already calibrated acceleration, choose the correct input units and disable response removal. Renaming counts as acceleration does not calibrate a waveform.

## Processing choices and outputs

- **Stage 1:** eight original quality methods, with the original voting/early-stopping rules. Methods and the vote threshold are selectable. Some individual scores can be blank because early stopping made further evaluation unnecessary.
- **Stage 2:** the selected SNR–Kishida, empirical, displacement–Ancheta, Guo and Hussein/Kishida methods. Guo runs in Python; MATLAB is not required. **USGS remains disabled.** Missing valid corner estimates use the configured fallbacks and are reported.
- **Stage 3:** individual check outcomes and a final outcome. Check 5 is off by default. A disabled or unavailable check must not be read as a pass.

The project stores one consolidated **`*_results.xlsx`** per processed input/grouping, including classifier scores, corner estimates from each enabled method, final corners, windows and sanity outcomes. Processed waveforms are saved in **`*_processed_2.h5`** with acceleration, velocity and displacement. The suffix is configurable. Diagnostic plots remain available in the Figures folders; they do not crowd the Results file list. Intermediate logs and checkpoints support diagnosis and resuming.

For newly converted data, choose monthly, yearly or another available grouping under HDF5 preparation. Existing HDF5 inputs keep their input grouping. Hypocentral distance uses epicentral distance and event depth, **without station elevation**.

Start with **2 workers** on a laptop. Increase gradually if memory permits. Workers divide waveform work while keeping dependent component groups together; output writes are coordinated. More workers do not necessarily make slow network or cloud storage faster.

## Resume a run or view older results

- Select **Open project** and choose that project's **`project.json`**. Keep its output/checkpoint folders together. Scan again, leave **Resume** enabled, then run. Verified completed work is reused where compatible with the inputs and settings.
- To inspect saved results without processing, select **Results → Browse existing results folder**.
- To inspect an HDF5 independently, use **Reader**.
- To stop safely, use the app's pause control and wait for the current safe checkpoint. A crash or closed app may require rerunning unfinished work; source recordings remain separate.

## Troubleshooting

| Situation | Action |
|---|---|
| The `.cmd` launcher is blocked | Try the `.py` launcher with an allowed Python. If bundled executables are also blocked, use the **Installed Python and R** launcher and approved installed interpreters. |
| Packages cannot be installed | Check the setup log and network/proxy access. The portable ZIP avoids first-run downloads; organizational policies still apply. |
| R shortcut selected instead of Rscript | Select a path such as `C:\Program Files\R\R-4.5.1\bin\Rscript.exe`, not the Start Menu shortcut folder. |
| No events match raw files | Check catalogue filename stems and UTC origin/start times, then rescan. |
| HDF5 has unexpected names or layers | Use the optional data-mapping review. |
| Results differ from the example | Use the sample button, a new empty project, identical input data/settings and the bundled reference runtime. Run the included comparison and inspect any reported differences. |

See [installed-runtime setup](docs/installed_runtime_launcher.txt) for the alternate launcher. Use **Check SignalQC.py** for runtime diagnostics without processing waveforms.

## Scientific code and attribution

The public edition retains the NCM edition's processing engines, trained models and scientific defaults. It changes the packaged data and input interface, not the computational equations. Historical `ncm` suffixes in engine filenames identify their development lineage; they do not select a hidden NCM dataset. Method names such as **NCM NN** identify the trained classifier and are retained.

Please cite the accompanying manuscript and the version of SignalQC used. No DOI is assigned by this package. Third-party runtimes, libraries and routines retain their own notices; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). NCM waveform and metadata access remains subject to NCM's data-access terms; inclusion of this example does not grant access to the wider archive.
