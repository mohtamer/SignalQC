# NCM application integration of IMs_FINAL_v4.R; numerical functions preserved.
args <- commandArgs(trailingOnly=TRUE)
if(length(args)!=1L)stop("Usage: Rscript IMs_FINAL_v5_app.R config.json")
entry <- sub("^--file=","",commandArgs()[grepl("^--file=",commandArgs())][1])
engine_dir <- dirname(normalizePath(entry,winslash="/",mustWork=TRUE))
app_dir <- normalizePath(file.path(engine_dir,"../.."),winslash="/")
app_libraries <- c(file.path(app_dir,"runtime","R-library"),file.path(app_dir,"runtime","R-IM-library"))
.libPaths(c(app_libraries,.libPaths()))
Sys.setenv(R_LIBS_USER=paste(app_libraries,collapse=.Platform$path.sep))
cfg <- jsonlite::fromJSON(normalizePath(args[1],winslash="/",mustWork=TRUE))
im_cancelled <- function() !is.null(cfg$cancel_file) && file.exists(cfg$cancel_file)
im_emit <- function(type,...)cat("SIGNALQC_EVENT ",jsonlite::toJSON(c(list(stage="intensity",type=type),list(...)),auto_unbox=TRUE,null="null"),"\n",sep="")


## Last modified by: MT Hussein 08-23-2026

## ============================================================
## UPDATED MAIN INTENSITY-MEASURE COMPUTATION SCRIPT
##
## This revision aligns record assembly and workbook formatting with the
## successful fixer workflow while retaining the established IM calculations.
##
## Changes relative to the previous version:
##   - All existing non-Fourier IM calculations are retained.
##   - EAS and FAV are written in wide format, one RSN per row.
##   - Common project log grid: 0.01-50 Hz, 100 points per decade.
##   - Konno-Ohmachi power smoothing, bw = 1/30 decade.
##   - Physical FAS scaling: dt * abs(FFT), units g*s.
##   - Full common-grid EAS/FAV values are retained; filter-based LUF/HUF remain metadata.
##   - Component PGV and PGD are added to metadata from the stored
##     processed velocity/displacement histories.
##   - Recursive directory discovery and adaptive RSN/chunk-level parallel processing.
##
## Fourier / EAS methodology audit:
##   - Native acceleration FAS is calculated as dt * abs(FFT(acceleration in g)).
##     This is equivalent to the NGA-East definition FAS_k = T*|C_k|, because
##     C_k contains the 1/N discrete-Fourier normalization and T = N*dt.
##   - Horizontal EAS^2 = (FAS_H1^2 + FAS_H2^2) / 2, followed by a square root.
##     This preserves the mean spectral power of the two as-recorded horizontals.
##   - Konno-Ohmachi smoothing is applied to spectral power (EAS^2 or FAS_V^2).
##   - Smoothing width bw = 1/30 decade and down-sampling = 100 frequency
##     points per decade, consistent with the NGA-East recommendation.
##   - The original NGA-East archive used standardized long zero padding to
##     obtain a common native df. This project instead pads each trace to the
##     next power of two and obtains a common grid through smoothing/down-sampling.
##   - The 0.01-50 Hz output grid and retention of values outside LUF/HUF are
##     project choices. LUF_Hz and HUF_Hz remain available for later screening.
##   - The sheet called FAV contains the vertical single-component FAS processed
##     with the same power smoothing and logarithmic down-sampling. FAV is a
##     project label; the NGA EAS definition itself applies to the horizontal pair.
##
## IM / METADATA UNITS QUICK REFERENCE:
##   Input acceleration_waveform              : m/s^2
##   Input velocity_waveform                  : m/s
##   Input displacement_waveform              : m
##   raw PGA and processed component PGA      : g
##   Component PGV (H1, H2, V)                : m/s
##   Component PGD (H1, H2, V)                : m
##   Component PSA (H1, H2, V; 5% damping)    : g
##   RotD50 / RotD100 / gmRotD50 PGA and PSA  : g
##   RotD50 / RotD100 / gmRotD50 PGV          : m/s
##   RotD50 / RotD100 / gmRotD50 PGD          : m
##   Horizontal EAS                           : g*s
##   Vertical FAV (vertical FAS)               : g*s
##   Fourier frequency, fcHP, fcLP, LUF, HUF  : Hz
##   Arias intensity and 5%-95% AI increment  : m/s
##   Arias-intensity crossing times/durations : s
##   CAV, CAV5 (>=5 cm/s^2), and CAV0.05g   : g*s
##   tmax4penalty                              : s
##   epidis and hypdis                         : km
##
## Unit equivalence for Fourier amplitudes:
##   1 g*s = 9.80665 m/s = 980.665 cm/s. NGA products expressed from
##   acceleration in cm/s^2 therefore have equivalent FAS/EAS units of cm/s
##   (equivalently gal*s, because 1 gal = 1 cm/s^2).
##
## Primary Fourier/EAS references:
##   Goulet et al. (2014), PEER NGA-East Database, PEER 2014/17.
##   Kottke et al. (2018), PEER 2018/05, doi:10.55461/LTMU9309.
##   Kottke et al. (2021), doi:10.1177/87552930211019052.
## ============================================================

library(rhdf5)
library(RCTC)
library(readxl)
library(dplyr)
library(tidyr)
library(stringr)
library(openxlsx)
library(parallel)

## -------------------- Constants --------------------

G_SI <- 9.80665

## -------------------- DIRECTORY / PARALLEL SETTINGS --------------------

## Add one or more root folders. The script searches recursively for
## *_processed_2.h5 files and pairs each one with its filmeta workbook.
ROOT_DIRS <- character(0)

RECURSIVE <- TRUE
USE_PARALLEL <- TRUE
AVAILABLE_CORES <- parallel::detectCores(logical = FALSE)
if (is.na(AVAILABLE_CORES) || AVAILABLE_CORES < 1L) AVAILABLE_CORES <- 2L

## A single reusable PSOCK cluster is created. Files are handled one at a time,
## while the RSNs inside each file are distributed dynamically among workers.
## This avoids the old imbalance where one worker could remain occupied by a
## 4,000-RSN month while other workers had already finished smaller months.
`%or_im%` <- function(a,b) if(is.null(a)||!length(a)) b else a
N_WORKERS <- max(1L,as.integer(cfg$workers %or_im% 1L))

## Adaptive chunk sizing keeps enough independent tasks available for load
## balancing without creating one parallel task for every individual RSN.
RSN_PARALLEL_THRESHOLD <- 2L  # Scheduling only: distribute independent record sets.
TARGET_CHUNKS_PER_WORKER <- 4L
MAX_RSN_CHUNK_SIZE <- 50L

## Fast per-chunk RDS checkpoints replace repeated full-workbook Excel writes.
## A rerun resumes valid completed chunks when the HDF5, metadata, record sets,
## switches, periods, and Fourier settings are unchanged.
RESUME_RSN_CHECKPOINTS <- isTRUE(cfg$resume)
KEEP_RSN_CHECKPOINTS_AFTER_SUCCESS <- TRUE
RSN_CHECKPOINT_VERSION <- "IM_NCM_V5_20261005"

## TRUE prints live master-process progress whenever a parallel RSN chunk
## finishes. With several workers there is no single current RSN, so progress is
## reported as completed RSNs / total RSNs, rate, elapsed time, and ETA.
SHOW_LIVE_RSN_PROGRESS <- TRUE

## -------------------- FOURIER / EAS SETTINGS --------------------
## NGA-East recommendation: Konno-Ohmachi power smoothing with
## bw = 1/30 decade and 100 frequency points per decade.
## The project output grid is 0.01-50 Hz. The 0.01-Hz lower bound is
## a project extension and does not imply that every record is reliable
## to 0.01 Hz; use LUF_Hz/HUF_Hz and record duration when screening data.
## The 50-Hz upper bound also respects the Nyquist limit of 100-sps records.

FOURIER_FMIN_HZ <- 0.01
FOURIER_FMAX_HZ <- 50.0
FOURIER_POINTS_PER_DECADE <- 100L
FOURIER_KO_BW_DECADES <- 1 / 30
FOURIER_KO_HALF_WIDTH_DECADES <- 0.5
## Retain all calculated values on the common frequency grid. The
## LUF/HUF fields remain available for optional screening later.
MASK_OUTSIDE_USABLE_BAND <- FALSE

periods <- c(
  0.01, 0.02, 0.022, 0.025, 0.029, 0.03, 0.032, 0.035, 0.036, 0.04, 0.042, 0.044, 0.045, 0.046, 0.048,
  0.05, 0.055, 0.06, 0.065, 0.067, 0.07, 0.075, 0.08, 0.085, 0.09, 0.095, 0.1, 0.11, 0.12, 0.13,
  0.133, 0.14, 0.15, 0.16, 0.17, 0.18, 0.19, 0.2, 0.22, 0.24, 0.25, 0.26, 0.28, 0.29, 0.3,
  0.32, 0.34, 0.35, 0.36, 0.38, 0.4, 0.42, 0.44, 0.45, 0.46, 0.48, 0.5, 0.55, 0.6, 0.65,
  0.667, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7,
  1.8, 1.9, 2, 2.2, 2.4, 2.5, 2.6, 2.8, 3, 3.2, 3.4, 3.5, 3.6, 3.8, 4,
  4.2, 4.4, 4.6, 4.8, 5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5, 9, 9.5, 10,
  11, 12, 13, 14, 15, 20
)

nper <- length(periods)
per_cols <- paste0("PER", sprintf("%05.3f", periods))

DURATION_END_LEVELS <- seq(0.1, 0.95, by = 0.05)
DURATION_LEVELS <- c(0.05, DURATION_END_LEVELS)
DURATION_RESULT_COLS <- c(
  "H1-Start Time 0.05",
  paste0("H1-End Time ", DURATION_END_LEVELS),
  "H2-Start Time 0.05",
  paste0("H2-End Time ", DURATION_END_LEVELS),
  "V-Start Time 0.05",
  paste0("V-End Time ", DURATION_END_LEVELS),
  "H1-Arias Intensity (m/s) 0.05-0.95",
  "H2-Arias Intensity (m/s) 0.05-0.95",
  "V-Arias Intensity (m/s) 0.05-0.95"
)

META_PEAK_COLS <- c(
  "pgv.mps H1", "pgd.m H1",
  "pgv.mps H2", "pgd.m H2",
  "pgv.mps V",  "pgd.m V"
)

CAV_RESULT_COLS <- c(
  "CAV_H1", "CAV5_H1", "CAV0.05g_H1",
  "CAV_H2", "CAV5_H2", "CAV0.05g_H2",
  "CAV_V",  "CAV5_V",  "CAV0.05g_V"
)

## -------------------- IM ON/OFF SWITCHES --------------------
## TRUE = calculate/write this IM
## FALSE = skip this IM

DO_PSA_H1    <- TRUE
DO_PSA_H2    <- TRUE
DO_PSA_V     <- TRUE

DO_ROTD50    <- TRUE
DO_ROTD100   <- TRUE
DO_GMROTD50  <- TRUE

DO_EAS       <- TRUE
DO_FAV       <- TRUE
DO_DURATION  <- TRUE
DO_CAV       <- TRUE

## The Excel workbook is written once, after all RSN chunks for a file have
## completed and been merged. Progress/restart protection is provided by the
## much faster RDS chunk checkpoints rather than repeated XLSX reconstruction.

DO_PSA_H1 <- "psa_h1" %in% cfg$enabled_measures
DO_PSA_H2 <- "psa_h2" %in% cfg$enabled_measures
DO_PSA_V <- "psa_v" %in% cfg$enabled_measures
DO_ROTD50 <- "rotd50" %in% cfg$enabled_measures
DO_ROTD100 <- "rotd100" %in% cfg$enabled_measures
DO_GMROTD50 <- "gmrotd50" %in% cfg$enabled_measures
DO_EAS <- "eas" %in% cfg$enabled_measures
DO_FAV <- "fav" %in% cfg$enabled_measures
DO_DURATION <- "duration" %in% cfg$enabled_measures
DO_CAV <- "cav" %in% cfg$enabled_measures

## -------------------- Utility functions --------------------

valid_dt <- function(dt) {
  length(dt) == 1L && !is.na(dt) && is.finite(dt) && dt > 0 && dt != -999
}

valid_series <- function(x) {
  !is.null(x) && length(x) >= 10L && all(is.finite(as.numeric(x)))
}

na_median <- function(x) {
  if (length(x) == 0L || all(is.na(x))) return(NA_real_)
  median(x, na.rm = TRUE)
}

na_max <- function(x) {
  if (length(x) == 0L || all(is.na(x))) return(NA_real_)
  max(x, na.rm = TRUE)
}

scalar_character <- function(x) {
  if (length(x) == 0L || all(is.na(x))) return(NA_character_)
  trimws(as.character(x[1L]))
}

scalar_numeric <- function(x) {
  if (length(x) == 0L || all(is.na(x))) return(NA_real_)
  suppressWarnings(as.numeric(x[1L]))
}

normalize_trace_id <- function(x) {
  x <- trimws(as.character(x))
  x[x %in% c("", "NA", "NaN", "-999")] <- NA_character_

  ## Metadata trace IDs may contain fractional seconds while the HDF5 group
  ## names do not. Use the same normalization employed when processed_2 HDF5
  ## files were prepared.
  x <- sub("(T[0-9]{6})\\.[0-9]+$", "\\1", x)
  x
}

normalize_event_id <- function(x) {
  x <- trimws(as.character(x))
  x[x %in% c("", "NA", "NaN", "-999")] <- NA_character_
  x
}

extract_record_key <- function(trace_key, rec_id, event_id) {
  trace_key <- normalize_trace_id(trace_key)
  rec_id <- trimws(as.character(rec_id))
  rec_id[rec_id %in% c("", "NA", "NaN", "-999")] <- NA_character_
  event_id <- normalize_event_id(event_id)

  timestamp <- stringr::str_extract(trace_key, "[0-9]{8}T[0-9]{6}$")
  dplyr::coalesce(timestamp, rec_id, event_id, trace_key)
}

read_trim_flag_from_attributes <- function(atts) {
  ## Prefer the attribute written by the current preprocessing code. Use the
  ## older reversed-name attribute only as a fallback; do not let it overwrite
  ## a valid Trimmed_Filtered value when both happen to exist.
  if (!is.null(atts$Trimmed_Filtered)) {
    return(toupper(trimws(as.character(atts$Trimmed_Filtered[1L]))))
  }
  if (!is.null(atts$Filtered_Trimmed)) {
    return(toupper(trimws(as.character(atts$Filtered_Trimmed[1L]))))
  }
  # Current app marks stored trimmed/filtered histories with stage2_complete.
  # Existing legacy Yes/No flags above keep precedence.
  if(!is.null(atts$stage2_complete)) {
    complete <- tolower(trimws(as.character(atts$stage2_complete[1L])))
    return(if(complete %in% c("true","1","yes")) "YES" else "NO")
  }
  NA_character_
}

is_usable_trace <- function(flag) {
  flag <- trimws(as.character(flag))
  !is.na(flag) & toupper(flag) == "YES"
}

empty_waveform_set <- function() {
  list(acc = NULL, vel = NULL, disp = NULL, dt = NA_real_)
}

read_waveform_set <- function(h5_file,
                              acc_path,
                              group_path,
                              trim_flag,
                              fallback_dt = NA_real_,
                              fallback_sampling_rate = NA_real_) {
  if (!is_usable_trace(trim_flag)) return(empty_waveform_set())

  acc_path <- scalar_character(acc_path)
  group_path <- scalar_character(group_path)
  fallback_dt <- scalar_numeric(fallback_dt)
  fallback_sampling_rate <- scalar_numeric(fallback_sampling_rate)

  if (is.na(acc_path) || acc_path == "") return(empty_waveform_set())

  acc <- tryCatch(rhdf5::h5read(h5_file, acc_path), error = function(e) NULL)

  vel_path  <- sub("acceleration_waveform$", "velocity_waveform", acc_path)
  disp_path <- sub("acceleration_waveform$", "displacement_waveform", acc_path)

  vel  <- tryCatch(rhdf5::h5read(h5_file, vel_path), error = function(e) NULL)
  disp <- tryCatch(rhdf5::h5read(h5_file, disp_path), error = function(e) NULL)

  sr_attr <- NA_real_
  if (!is.na(group_path) && group_path != "") {
    atts <- tryCatch(
      rhdf5::h5readAttributes(file = h5_file, name = group_path),
      error = function(e) list()
    )
    if (!is.null(atts$sampling_rate)) {
      sr_attr <- suppressWarnings(as.numeric(atts$sampling_rate[1L]))
    }
  }

  dt <- NA_real_
  if (valid_dt(fallback_dt)) {
    dt <- fallback_dt
  } else if (is.finite(fallback_sampling_rate) && fallback_sampling_rate > 0) {
    dt <- 1 / fallback_sampling_rate
  } else if (is.finite(sr_attr) && sr_attr > 0) {
    dt <- 1 / sr_attr
  }

  list(acc = acc, vel = vel, disp = disp, dt = dt)
}

find_matching_meta <- function(h5_file) {
  return(cfg$metadata_file)

  folder <- dirname(h5_file)
  prefix <- basename(h5_file)
  prefix <- sub("_processed_2\\.h5$", "", prefix, ignore.case = TRUE)

  expected <- file.path(folder, paste0(prefix, ".h5filmeta.xlsx"))
  if (file.exists(expected)) return(expected)

  candidates <- list.files(
    folder,
    pattern = "\\.h5filmeta\\.xlsx$",
    full.names = TRUE,
    ignore.case = TRUE
  )

  candidates <- candidates[
    grepl(prefix, basename(candidates), ignore.case = TRUE) &
      !grepl("_sc|backup|fixed|^~\\$", basename(candidates), ignore.case = TRUE)
  ]

  if (length(candidates) > 0L) return(candidates[1L])
  NA_character_
}

valid_corner <- function(x) {
  length(x) == 1L && !is.na(x) && is.finite(x) && x > 0 && x != -999
}

safe_peak_abs <- function(x) {
  if (!valid_series(x)) return(NA_real_)
  max(abs(as.numeric(x)), na.rm = TRUE)
}

safe_pair_tmax <- function(hp1, hp2) {
  hp1 <- suppressWarnings(as.numeric(hp1))
  hp2 <- suppressWarnings(as.numeric(hp2))
  hp1[!is.finite(hp1) | hp1 <= 0 | hp1 == -999] <- NA_real_
  hp2[!is.finite(hp2) | hp2 <= 0 | hp2 == -999] <- NA_real_

  hp_max <- pmax(hp1, hp2, na.rm = TRUE)
  hp_max[!is.finite(hp_max) | hp_max <= 0] <- NA_real_
  1 / (1.25 * hp_max)
}

safe_single_tmax <- function(hp) {
  hp <- suppressWarnings(as.numeric(hp))
  hp[!is.finite(hp) | hp <= 0 | hp == -999] <- NA_real_
  1 / (1.25 * hp)
}

make_log_frequency_grid <- function(fmin, fmax, points_per_decade) {
  if (!is.finite(fmin) || !is.finite(fmax) || fmin <= 0 || fmax <= fmin) {
    stop("Invalid Fourier frequency limits.")
  }
  if (points_per_decade < 1L) stop("points_per_decade must be positive.")

  step_log10 <- 1 / as.numeric(points_per_decade)
  n_steps <- floor(log10(fmax / fmin) / step_log10 + 1e-10)
  out <- fmin * 10^((0:n_steps) * step_log10)

  if (tail(out, 1L) < fmax * (1 - 1e-12)) out <- c(out, fmax)
  out <- out[out <= fmax * (1 + 1e-12)]
  unique(as.numeric(out))
}

FOURIER_FREQS_HZ <- make_log_frequency_grid(
  FOURIER_FMIN_HZ,
  FOURIER_FMAX_HZ,
  FOURIER_POINTS_PER_DECADE
)
FOURIER_COLS <- paste0("FREQ", sprintf("%.5f", FOURIER_FREQS_HZ))

usable_frequency_bounds <- function(hp_values, lp_values, nyquist_hz) {
  hp_values <- as.numeric(hp_values)
  lp_values <- as.numeric(lp_values)

  hp_values <- hp_values[is.finite(hp_values) & hp_values > 0 & hp_values != -999]
  lp_values <- lp_values[is.finite(lp_values) & lp_values > 0 & lp_values != -999]

  luf <- FOURIER_FMIN_HZ
  huf <- min(FOURIER_FMAX_HZ, nyquist_hz)

  if (length(hp_values) > 0L) luf <- max(luf, 1.25 * max(hp_values))
  if (length(lp_values) > 0L) huf <- min(huf, min(lp_values) / 1.25)

  if (!is.finite(luf) || !is.finite(huf) || huf <= luf) {
    return(c(LUF_Hz = NA_real_, HUF_Hz = NA_real_))
  }

  c(LUF_Hz = luf, HUF_Hz = huf)
}

build_h5_trace_index <- function(h5_file) {
  h5_info <- rhdf5::h5ls(h5_file, recursive = TRUE)
  acc_rows <- h5_info[h5_info$name == "acceleration_waveform", , drop = FALSE]

  if (nrow(acc_rows) == 0L) {
    stop("No acceleration_waveform datasets found in: ", h5_file)
  }

  out <- lapply(seq_len(nrow(acc_rows)), function(k) {
    grp <- as.character(acc_rows$group[k])
    grp_clean <- sub("^/", "", grp)
    parts <- strsplit(grp_clean, "/", fixed = TRUE)[[1L]]
    if (length(parts) < 2L) return(NULL)

    event_id <- normalize_event_id(parts[1L])
    trace_id <- trimws(parts[length(parts)])

    atts <- tryCatch(
      rhdf5::h5readAttributes(h5_file, grp),
      error = function(e) list()
    )

    sr <- if (!is.null(atts$sampling_rate)) {
      suppressWarnings(as.numeric(atts$sampling_rate[1L]))
    } else {
      NA_real_
    }

    hypdis_h5 <- if (!is.null(atts$hypdis)) {
      suppressWarnings(as.numeric(atts$hypdis[1L]))
    } else {
      NA_real_
    }

    data.frame(
      event_id_h5 = event_id,
      trace.id_h5 = trace_id,
      trace_key = normalize_trace_id(trace_id),
      group_path = grp,
      acc_path = paste0(grp, "/acceleration_waveform"),
      sampling_rate = sr,
      hypdis_h5 = hypdis_h5,
      Trimmed_Filtered = read_trim_flag_from_attributes(atts),
      stringsAsFactors = FALSE,
      check.names = FALSE
    )
  })

  dplyr::bind_rows(out)
}

match_metadata_to_h5 <- function(meta, h5_index) {
  exact_meta <- paste(meta$event_id, meta$trace_key, sep = "\r")
  exact_h5 <- paste(h5_index$event_id_h5, h5_index$trace_key, sep = "\r")

  # Match the stored identifier first; preserve subsecond keys in new app files.
  # Legacy normalized fallbacks are allowed only for unique HDF5 candidates.
  unique_match <- function(keys,candidates) {
    index <- match(keys,candidates)
    duplicate <- duplicated(candidates)|duplicated(candidates,fromLast=TRUE)
    index[!is.na(index)&duplicate[index]] <- NA_integer_
    index
  }
  idx <- unique_match(paste(meta$event_id,meta$trace.id,sep="\r"),
                      paste(h5_index$event_id_h5,h5_index$trace.id_h5,sep="\r"))
  idx_exact <- unique_match(exact_meta,exact_h5)
  idx_trace <- unique_match(meta$trace_key,h5_index$trace_key)
  idx[is.na(idx)] <- idx_exact[is.na(idx)]
  idx[is.na(idx)] <- idx_trace[is.na(idx)]

  matched <- h5_index[idx, c(
    "event_id_h5", "trace.id_h5", "group_path", "acc_path",
    "sampling_rate", "hypdis_h5", "Trimmed_Filtered"
  ), drop = FALSE]

  rownames(matched) <- NULL
  dplyr::bind_cols(meta, matched) %>%
    dplyr::mutate(h5_matched = !is.na(idx))
}

ensure_set_columns <- function(sets) {
  roles <- c("H1", "H2", "V")
  char_bases <- c("trace.id", "group_path", "acc_path", "Trimmed_Filtered")
  num_bases <- c(
    "sampling_rate", "timeStep", "fcHP", "fcLP", "pga", "rawpga",
    "hypdis", "epidis", "est.hypdis.Pwave.duration"
  )

  for (base in char_bases) {
    for (role in roles) {
      nm <- paste0(base, "_", role)
      if (!nm %in% names(sets)) sets[[nm]] <- rep(NA_character_, nrow(sets))
    }
  }

  for (base in num_bases) {
    for (role in roles) {
      nm <- paste0(base, "_", role)
      if (!nm %in% names(sets)) sets[[nm]] <- rep(NA_real_, nrow(sets))
    }
  }

  sets
}

## -------------------- IM functions --------------------

compute_psa_component <- function(acc, dt, periods) {
  if (!valid_series(acc) || !valid_dt(dt)) {
    return(rep(NA_real_, length(periods)))
  }

  acc_g <- as.numeric(acc) / G_SI
  accts <- ts(acc_g, delta = dt)

  tmp <- tryCatch({
    PS_cal_cpp(
      data = accts,
      period_t = periods,
      damping = 0.05,
      time_dt = dt,
      type_return = 1
    )
  }, error = function(e) NULL)

  if (is.null(tmp)) return(rep(NA_real_, length(periods)))

  tmp <- as.numeric(tmp)
  nper <- length(periods)

  if (length(tmp) == 2 * nper) {
    mat <- matrix(tmp, ncol = 2, byrow = TRUE)
    return(as.numeric(mat[, 2]))
  }

  if (length(tmp) == 4 * nper) {
    mat <- matrix(tmp, ncol = 4, byrow = TRUE)
    return(as.numeric(mat[, 2]))
  }

  cat("PS_cal_cpp unexpected length:", length(tmp), "for", nper, "periods\n")
  rep(NA_real_, nper)
}

compute_rotd <- function(acc1, acc2, dt, periods, combine_index) {
  if (!valid_series(acc1) || !valid_series(acc2) || !valid_dt(dt)) {
    return(rep(NA_real_, length(periods)))
  }
  
  n <- min(length(acc1), length(acc2))
  acc1_g <- as.numeric(acc1[1:n]) / G_SI
  acc2_g <- as.numeric(acc2[1:n]) / G_SI
  
  tryCatch({
    res <- RotD_cal_PSA(
      data1 = acc1_g,
      data2 = acc2_g,
      period = periods,
      damping = 0.05,
      combine_index = combine_index,
      dt = dt
    )
    as.numeric(res$PSA_RotDxx)
  }, error = function(e) {
    rep(NA_real_, length(periods))
  })
}

compute_rotd_motion_measures <- function(acc1, acc2, vel1, vel2, disp1, disp2, angles_deg = 0:179) {
  out <- c(PGA50 = NA_real_, PGA100 = NA_real_,
           PGV50 = NA_real_, PGV100 = NA_real_,
           PGD50 = NA_real_, PGD100 = NA_real_)

  if (valid_series(acc1) && valid_series(acc2)) {
    n <- min(length(acc1), length(acc2))
    a1 <- as.numeric(acc1)[1:n]
    a2 <- as.numeric(acc2)[1:n]
    pga_vec <- rep(NA_real_, length(angles_deg))

    for (k in seq_along(angles_deg)) {
      th <- angles_deg[k] * pi / 180
      a_rot <- a1 * cos(th) + a2 * sin(th)
      pga_vec[k] <- max(abs(a_rot), na.rm = TRUE) / G_SI
    }

    out["PGA50"] <- na_median(pga_vec)
    out["PGA100"] <- na_max(pga_vec)
  }

  if (valid_series(vel1) && valid_series(vel2)) {
    n <- min(length(vel1), length(vel2))
    v1 <- as.numeric(vel1)[1:n]
    v2 <- as.numeric(vel2)[1:n]
    pgv_vec <- rep(NA_real_, length(angles_deg))

    for (k in seq_along(angles_deg)) {
      th <- angles_deg[k] * pi / 180
      v_rot <- v1 * cos(th) + v2 * sin(th)
      pgv_vec[k] <- max(abs(v_rot), na.rm = TRUE)
    }

    out["PGV50"] <- na_median(pgv_vec)
    out["PGV100"] <- na_max(pgv_vec)
  }

  if (valid_series(disp1) && valid_series(disp2)) {
    n <- min(length(disp1), length(disp2))
    d1 <- as.numeric(disp1)[1:n]
    d2 <- as.numeric(disp2)[1:n]
    pgd_vec <- rep(NA_real_, length(angles_deg))

    for (k in seq_along(angles_deg)) {
      th <- angles_deg[k] * pi / 180
      d_rot <- d1 * cos(th) + d2 * sin(th)
      pgd_vec[k] <- max(abs(d_rot), na.rm = TRUE)
    }

    out["PGD50"] <- na_median(pgd_vec)
    out["PGD100"] <- na_max(pgd_vec)
  }

  out
}

compute_gmrotd50 <- function(acc1, acc2, vel1, vel2, disp1, disp2, dt, periods, angles_deg = 0:89) {
  empty <- list(
    PSA = rep(NA_real_, length(periods)),
    PGA = NA_real_, PGV = NA_real_, PGD = NA_real_
  )

  if (!valid_series(acc1) || !valid_series(acc2) || !valid_dt(dt)) return(empty)

  n_acc <- min(length(acc1), length(acc2))
  acc1 <- as.numeric(acc1)[1:n_acc]
  acc2 <- as.numeric(acc2)[1:n_acc]

  have_vel <- valid_series(vel1) && valid_series(vel2)
  have_disp <- valid_series(disp1) && valid_series(disp2)

  if (have_vel) {
    n_vel <- min(length(vel1), length(vel2))
    vel1 <- as.numeric(vel1)[1:n_vel]
    vel2 <- as.numeric(vel2)[1:n_vel]
  }

  if (have_disp) {
    n_disp <- min(length(disp1), length(disp2))
    disp1 <- as.numeric(disp1)[1:n_disp]
    disp2 <- as.numeric(disp2)[1:n_disp]
  }

  gm_spec_mat <- matrix(NA_real_, nrow = length(angles_deg), ncol = length(periods))
  gm_pga_vec <- rep(NA_real_, length(angles_deg))
  gm_pgv_vec <- rep(NA_real_, length(angles_deg))
  gm_pgd_vec <- rep(NA_real_, length(angles_deg))

  for (k in seq_along(angles_deg)) {
    th <- angles_deg[k] * pi / 180

    a1r <-  acc1 * cos(th) + acc2 * sin(th)
    a2r <- -acc1 * sin(th) + acc2 * cos(th)

    psa1 <- compute_psa_component(a1r, dt, periods)
    psa2 <- compute_psa_component(a2r, dt, periods)

    valid <- is.finite(psa1) & is.finite(psa2) & psa1 >= 0 & psa2 >= 0
    gm_spec <- rep(NA_real_, length(periods))
    gm_spec[valid] <- sqrt(psa1[valid] * psa2[valid])
    gm_spec_mat[k, ] <- gm_spec

    pga1 <- max(abs(a1r), na.rm = TRUE) / G_SI
    pga2 <- max(abs(a2r), na.rm = TRUE) / G_SI
    gm_pga_vec[k] <- sqrt(pga1 * pga2)

    if (have_vel) {
      v1r <-  vel1 * cos(th) + vel2 * sin(th)
      v2r <- -vel1 * sin(th) + vel2 * cos(th)
      gm_pgv_vec[k] <- sqrt(
        max(abs(v1r), na.rm = TRUE) * max(abs(v2r), na.rm = TRUE)
      )
    }

    if (have_disp) {
      d1r <-  disp1 * cos(th) + disp2 * sin(th)
      d2r <- -disp1 * sin(th) + disp2 * cos(th)
      gm_pgd_vec[k] <- sqrt(
        max(abs(d1r), na.rm = TRUE) * max(abs(d2r), na.rm = TRUE)
      )
    }
  }

  list(
    PSA = apply(gm_spec_mat, 2, na_median),
    PGA = na_median(gm_pga_vec),
    PGV = if (have_vel) na_median(gm_pgv_vec) else NA_real_,
    PGD = if (have_disp) na_median(gm_pgd_vec) else NA_real_
  )
}

compute_native_fas <- function(acc, dt) {
  if (!valid_series(acc) || !valid_dt(dt)) return(NULL)

  acc_g <- as.numeric(acc) / G_SI
  n <- length(acc_g)
  nfft <- 2^(ceiling(log2(n)))

  acc_pad <- c(acc_g, rep(0, nfft - n))
  fft_values <- fft(acc_pad)

  n_positive <- floor(nfft / 2) + 1L
  freq <- (0:(n_positive - 1L)) / (nfft * dt)

  ## Physical Fourier amplitude spectrum:
  ## FAS(f_k) = dt * |FFT_k|, with units g*s.
  fas <- dt * Mod(fft_values[seq_len(n_positive)])

  keep <- is.finite(freq) & is.finite(fas) & freq > 0

  list(
    freq = freq[keep],
    fas = fas[keep],
    nyquist = 1 / (2 * dt)
  )
}

ko_smooth_power <- function(native_freq,
                            native_power,
                            target_freq,
                            bw_decades = FOURIER_KO_BW_DECADES,
                            half_width_decades = FOURIER_KO_HALF_WIDTH_DECADES) {
  native_freq <- as.numeric(native_freq)
  native_power <- as.numeric(native_power)
  target_freq <- as.numeric(target_freq)

  ok <- is.finite(native_freq) & native_freq > 0 &
    is.finite(native_power) & native_power >= 0

  native_freq <- native_freq[ok]
  native_power <- native_power[ok]
  if (length(native_freq) < 2L) return(rep(NA_real_, length(target_freq)))

  ord <- order(native_freq)
  native_freq <- native_freq[ord]
  native_power <- native_power[ord]

  b <- 2 * pi / bw_decades
  output <- rep(NA_real_, length(target_freq))

  for (j in seq_along(target_freq)) {
    fc <- target_freq[j]
    if (!is.finite(fc) || fc <= 0) next

    f_lo <- fc * 10^(-half_width_decades)
    f_hi <- fc * 10^( half_width_decades)

    i1 <- max(1L, findInterval(f_lo, native_freq) + 1L)
    i2 <- min(length(native_freq), findInterval(f_hi, native_freq))
    if (i2 < i1) next

    f_sub <- native_freq[i1:i2]
    p_sub <- native_power[i1:i2]

    x <- b * log10(f_sub / fc)
    weights <- rep(1, length(x))
    nz <- abs(x) > 1e-14
    weights[nz] <- (sin(x[nz]) / x[nz])^4

    good <- is.finite(weights) & weights >= 0 & is.finite(p_sub)
    wsum <- sum(weights[good])
    if (wsum > 0) output[j] <- sum(weights[good] * p_sub[good]) / wsum
  }

  output
}

compute_eas_spectrum <- function(acc1, acc2, dt, hp1, hp2, lp1, lp2) {
  if (!valid_series(acc1) || !valid_series(acc2) || !valid_dt(dt)) return(NULL)

  n <- min(length(acc1), length(acc2))
  if (n < 10L) return(NULL)

  spec1 <- compute_native_fas(as.numeric(acc1)[1:n], dt)
  spec2 <- compute_native_fas(as.numeric(acc2)[1:n], dt)
  if (is.null(spec1) || is.null(spec2)) return(NULL)

  n_common <- min(length(spec1$freq), length(spec2$freq))
  native_freq <- spec1$freq[1:n_common]

  ## NGA-East EAS is the square root of mean horizontal spectral power.
  eas_power <- (spec1$fas[1:n_common]^2 + spec2$fas[1:n_common]^2) / 2

  ## NGA-East smoothing/down-sampling is applied to EAS^2.
  smoothed_power <- ko_smooth_power(
    native_freq = native_freq,
    native_power = eas_power,
    target_freq = FOURIER_FREQS_HZ
  )

  eas <- sqrt(pmax(smoothed_power, 0))
  bounds <- usable_frequency_bounds(
    hp_values = c(hp1, hp2),
    lp_values = c(lp1, lp2),
    nyquist_hz = min(spec1$nyquist, spec2$nyquist)
  )

  if (MASK_OUTSIDE_USABLE_BAND) {
    if (anyNA(bounds)) {
      eas[] <- NA_real_
    } else {
      outside <- FOURIER_FREQS_HZ < bounds["LUF_Hz"] |
        FOURIER_FREQS_HZ > bounds["HUF_Hz"]
      eas[outside] <- NA_real_
    }
  }

  list(
    freq = FOURIER_FREQS_HZ,
    eas = eas,
    LUF_Hz = unname(bounds["LUF_Hz"]),
    HUF_Hz = unname(bounds["HUF_Hz"])
  )
}

compute_fav_spectrum <- function(acc, dt, hp, lp) {
  spec <- compute_native_fas(acc, dt)
  if (is.null(spec)) return(NULL)

  ## FAV is the vertical single-component FAS. For consistency with EAS,
  ## its spectral power is smoothed and down-sampled with the same KO grid.
  smoothed_power <- ko_smooth_power(
    native_freq = spec$freq,
    native_power = spec$fas^2,
    target_freq = FOURIER_FREQS_HZ
  )

  fav <- sqrt(pmax(smoothed_power, 0))
  bounds <- usable_frequency_bounds(
    hp_values = hp,
    lp_values = lp,
    nyquist_hz = spec$nyquist
  )

  if (MASK_OUTSIDE_USABLE_BAND) {
    if (anyNA(bounds)) {
      fav[] <- NA_real_
    } else {
      outside <- FOURIER_FREQS_HZ < bounds["LUF_Hz"] |
        FOURIER_FREQS_HZ > bounds["HUF_Hz"]
      fav[outside] <- NA_real_
    }
  }

  list(
    freq = FOURIER_FREQS_HZ,
    fav = fav,
    LUF_Hz = unname(bounds["LUF_Hz"]),
    HUF_Hz = unname(bounds["HUF_Hz"])
  )
}

compute_arias <- function(acc, dt) {
  if (!valid_series(acc) || !valid_dt(dt)) {
    return(list(t = NA, Ia = NA, Ia_norm = NA))
  }
  
  acc <- as.numeric(acc)
  
  Ia <- cumsum((pi / (2 * G_SI)) * (acc^2) * dt)
  t <- seq(0, by = dt, length.out = length(acc))
  
  Ia_total <- Ia[length(Ia)]
  
  if (Ia_total <= 0) {
    Ia_norm <- Ia * NA
  } else {
    Ia_norm <- Ia / Ia_total
  }
  
  list(t = t, Ia = Ia, Ia_norm = Ia_norm)
}

get_duration_times <- function(Ia_norm_t, levels = seq(0.05, 0.95, by = 0.05)) {
  t <- Ia_norm_t$t
  Ia_norm <- Ia_norm_t$Ia_norm
  
  if (all(is.na(Ia_norm))) return(rep(NA_real_, length(levels)))
  
  sapply(levels, function(a) {
    idx <- which(Ia_norm >= a)[1]
    if (is.na(idx)) return(NA_real_)
    t[idx]
  })
}

get_arias_range <- function(Ia_norm_t, a1, a2) {
  Ia <- Ia_norm_t$Ia
  Ia_total <- Ia[length(Ia)]
  
  if (is.na(Ia_total) || Ia_total <= 0) return(NA_real_)
  
  Ia_total * (a2 - a1)
}

compute_cav <- function(acc, dt, threshold_cmps2 = 5.0, threshold_g = 0.05) {
  if (!valid_series(acc) || !valid_dt(dt)) {
    return(c(CAV = NA_real_, CAV5 = NA_real_, CAV0.05g = NA_real_))
  }
  
  acc <- as.numeric(acc)                         # m/s^2
  a_g <- acc / G_SI                             # g
  threshold_mps2 <- threshold_cmps2 / 100.0     # 5 cm/s^2 = 0.05 m/s^2
  
  cav <- sum(abs(a_g)) * dt
  cav5 <- sum(abs(a_g[abs(acc) >= threshold_mps2])) * dt
  cav005g <- sum(abs(a_g[abs(a_g) >= threshold_g])) * dt
  
  c(CAV = cav, CAV5 = cav5, CAV0.05g = cav005g)
}

## -------------------- RSN chunk / checkpoint helpers --------------------

atomic_save_rds <- function(object, path) {
  dir_path <- dirname(path)
  if (!dir.exists(dir_path)) dir.create(dir_path, recursive = TRUE, showWarnings = FALSE)

  tmp_path <- paste0(
    path,
    ".__tmp_", Sys.getpid(), "_", sample.int(.Machine$integer.max, 1L)
  )

  on.exit(if (file.exists(tmp_path)) unlink(tmp_path), add = TRUE)
  saveRDS(object, tmp_path, compress = FALSE)

  if (file.exists(path) && !file.remove(path)) {
    stop("Cannot replace RDS checkpoint: ", path)
  }

  if (!file.rename(tmp_path, path)) {
    copied <- file.copy(tmp_path, path, overwrite = TRUE)
    if (!copied) stop("Could not write RDS checkpoint: ", path)
    unlink(tmp_path)
  }

  invisible(path)
}

make_rsn_chunks <- function(n_sets, workers) {
  n_sets <- as.integer(n_sets)
  workers <- max(1L, min(as.integer(workers), n_sets))

  target_chunks <- max(1L, workers * as.integer(TARGET_CHUNKS_PER_WORKER))
  chunk_size <- ceiling(n_sets / target_chunks)
  chunk_size <- max(1L, min(as.integer(MAX_RSN_CHUNK_SIZE), chunk_size))

  indices <- seq_len(n_sets)
  split(indices, ceiling(indices / chunk_size))
}

build_checkpoint_manifest <- function(h5_file, meta_xlsx, sets, chunks) {
  h5_info <- file.info(h5_file)
  meta_info <- file.info(meta_xlsx)

  list(
    version = RSN_CHECKPOINT_VERSION,
    processing_signature = cfg$processing_signature,
    h5_file = normalizePath(h5_file, winslash = "/", mustWork = FALSE),
    h5_size = as.numeric(h5_info$size[1L]),
    h5_mtime = as.numeric(h5_info$mtime[1L]),
    metadata_file = normalizePath(meta_xlsx, winslash = "/", mustWork = FALSE),
    metadata_size = as.numeric(meta_info$size[1L]),
    metadata_mtime = as.numeric(meta_info$mtime[1L]),
    sets = as.data.frame(sets, check.names = FALSE, stringsAsFactors = FALSE),
    chunks = lapply(chunks, as.integer),
    switches = c(
      DO_PSA_H1 = DO_PSA_H1,
      DO_PSA_H2 = DO_PSA_H2,
      DO_PSA_V = DO_PSA_V,
      DO_ROTD50 = DO_ROTD50,
      DO_ROTD100 = DO_ROTD100,
      DO_GMROTD50 = DO_GMROTD50,
      DO_EAS = DO_EAS,
      DO_FAV = DO_FAV,
      DO_DURATION = DO_DURATION,
      DO_CAV = DO_CAV
    ),
    periods = periods,
    fourier_freqs = FOURIER_FREQS_HZ,
    fourier_bw = FOURIER_KO_BW_DECADES,
    fourier_half_width = FOURIER_KO_HALF_WIDTH_DECADES,
    mask_outside_usable_band = MASK_OUTSIDE_USABLE_BAND
  )
}

prepare_checkpoint_directory <- function(out_xlsx, manifest) {
  checkpoint_dir <- sub(
    "\\.xlsx$", "_RSN_checkpoints", out_xlsx,
    ignore.case = TRUE
  )
  manifest_file <- file.path(checkpoint_dir, "manifest.rds")

  valid_existing <- FALSE
  if (dir.exists(checkpoint_dir) && isTRUE(RESUME_RSN_CHECKPOINTS) &&
      file.exists(manifest_file)) {
    old_manifest <- tryCatch(readRDS(manifest_file), error = function(e) NULL)
    valid_existing <- !is.null(old_manifest) && identical(old_manifest, manifest)
  }

  if (dir.exists(checkpoint_dir) && !valid_existing) {
    unlink(checkpoint_dir, recursive = TRUE, force = TRUE)
  }

  if (!dir.exists(checkpoint_dir)) {
    dir.create(checkpoint_dir, recursive = TRUE, showWarnings = FALSE)
  }

  if (!valid_existing) atomic_save_rds(manifest, manifest_file)

  list(
    dir = checkpoint_dir,
    manifest_file = manifest_file,
    resumed = valid_existing
  )
}

make_chunk_jobs <- function(chunks, checkpoint_dir) {
  lapply(seq_along(chunks), function(j) {
    idx <- as.integer(chunks[[j]])
    list(
      chunk_id = as.integer(j),
      indices = idx,
      checkpoint_file = file.path(
        checkpoint_dir,
        sprintf(
          "chunk_%05d_RSN_%07d_%07d.rds",
          j, min(idx), max(idx)
        )
      )
    )
  })
}

format_progress_time <- function(seconds) {
  if (!is.finite(seconds) || seconds < 0) return("calculating")

  seconds <- as.integer(round(seconds))
  hours <- seconds %/% 3600L
  minutes <- (seconds %% 3600L) %/% 60L
  secs <- seconds %% 60L

  sprintf("%02d:%02d:%02d", hours, minutes, secs)
}

make_rsn_progress_reporter <- function(total_rsns,
                                       already_completed_rsns = 0L,
                                       file_label = "") {
  total_rsns <- as.integer(total_rsns)
  already_completed_rsns <- as.integer(already_completed_rsns)
  file_label <- as.character(file_label)
  started_at <- Sys.time()
  completed_this_run <- 0L
  completed_total <- already_completed_rsns

  function(status) {
    if (!isTRUE(SHOW_LIVE_RSN_PROGRESS)) return(invisible(NULL))

    if (!is.list(status) || !isTRUE(status$ok)) {
      if (is.list(status) && length(status$indices) > 0L) {
        cat(sprintf(
          "[%s] FAILED chunk %d (RSN %d-%d): %s\n",
          file_label,
          as.integer(status$chunk_id),
          min(status$indices),
          max(status$indices),
          as.character(status$error)
        ))
        flush.console()
      }
      return(invisible(NULL))
    }

    n_done <- length(status$indices)
    completed_this_run <<- completed_this_run + n_done
    completed_total <<- completed_total + n_done

    elapsed_sec <- as.numeric(difftime(Sys.time(), started_at, units = "secs"))
    rate_per_min <- if (elapsed_sec > 0 && completed_this_run > 0L) {
      completed_this_run / elapsed_sec * 60
    } else {
      NA_real_
    }

    remaining <- max(0L, total_rsns - completed_total)
    eta_sec <- if (is.finite(rate_per_min) && rate_per_min > 0) {
      remaining / rate_per_min * 60
    } else {
      NA_real_
    }

    percent <- if (total_rsns > 0L) {
      100 * completed_total / total_rsns
    } else {
      100
    }

    rate_text <- if (is.finite(rate_per_min)) {
      sprintf("%.2f RSN/min", rate_per_min)
    } else {
      "rate calculating"
    }

    cat(sprintf(
      "[%s] Finished chunk %d (RSN %d-%d) | %d/%d complete (%.1f%%) | elapsed %s | %s | ETA %s\n",
      file_label,
      as.integer(status$chunk_id),
      min(status$indices),
      max(status$indices),
      completed_total,
      total_rsns,
      percent,
      format_progress_time(elapsed_sec),
      rate_text,
      if (remaining == 0L) "00:00:00" else format_progress_time(eta_sec)
    ))
    flush.console()
    invisible(NULL)
  }
}

parallel_chunk_lapply_with_progress <- function(cl,
                                                jobs,
                                                fun,
                                                total_rsns,
                                                already_completed_rsns = 0L,
                                                file_label = "") {
  if (length(jobs) == 0L) return(list())

  ## Preserve the original parLapplyLB path when live progress is disabled.
  if (!isTRUE(SHOW_LIVE_RSN_PROGRESS)) {
    return(parallel::parLapplyLB(cl, jobs, fun))
  }

  n_jobs <- length(jobs)
  n_active_workers <- min(length(cl), n_jobs)
  results <- vector("list", n_jobs)
  report_progress <- make_rsn_progress_reporter(
    total_rsns = total_rsns,
    already_completed_rsns = already_completed_rsns,
    file_label = file_label
  )

  submit_job <- function(worker_id, job_id) {
    parallel:::sendCall(
      cl[[worker_id]],
      fun,
      list(jobs[[job_id]]),
      tag = job_id
    )
  }

  for (job_id in seq_len(n_active_workers)) {
    submit_job(job_id, job_id)
  }

  next_job_id <- n_active_workers + 1L

  for (received_id in seq_len(n_jobs)) {
    answer <- parallel:::recvOneResult(cl)
    job_id <- as.integer(answer$tag)
    results[[job_id]] <- answer$value

    if (!inherits(answer$value, "try-error")) {
      report_progress(answer$value)
    }

    if (next_job_id <= n_jobs) {
      submit_job(answer$node, next_job_id)
      next_job_id <- next_job_id + 1L
    }
  }

  parallel:::checkForRemoteErrors(results)
}

sequential_chunk_lapply_with_progress <- function(jobs,
                                                  fun,
                                                  total_rsns,
                                                  already_completed_rsns = 0L,
                                                  file_label = "") {
  if (length(jobs) == 0L) return(list())

  results <- vector("list", length(jobs))
  report_progress <- make_rsn_progress_reporter(
    total_rsns = total_rsns,
    already_completed_rsns = already_completed_rsns,
    file_label = file_label
  )

  for (j in seq_along(jobs)) {
    if(im_cancelled())stop("SIGNALQC_PAUSED")
    results[[j]] <- fun(jobs[[j]])
    report_progress(results[[j]])
  }

  results
}

read_valid_chunk_checkpoint <- function(job) {
  if (!file.exists(job$checkpoint_file)) return(NULL)

  payload <- tryCatch(readRDS(job$checkpoint_file), error = function(e) NULL)
  if (is.null(payload) || !is.list(payload)) return(NULL)
  if (!identical(payload$version, RSN_CHECKPOINT_VERSION)) return(NULL)
  if (!identical(as.integer(payload$indices), as.integer(job$indices))) return(NULL)
  if (!is.list(payload$results) || length(payload$results) != length(job$indices)) return(NULL)

  result_indices <- tryCatch(
    vapply(payload$results, function(x) as.integer(x$index), integer(1)),
    error = function(e) integer(0)
  )
  if (!identical(result_indices, as.integer(job$indices))) return(NULL)

  payload
}

new_empty_rsn_result <- function(i) {
  list(
    index = as.integer(i),
    meta_peaks = stats::setNames(rep(NA_real_, length(META_PEAK_COLS)), META_PEAK_COLS),
    psa_h1 = rep(NA_real_, nper),
    psa_h2 = rep(NA_real_, nper),
    psa_v = rep(NA_real_, nper),
    duration = stats::setNames(
      rep(NA_real_, length(DURATION_RESULT_COLS)),
      DURATION_RESULT_COLS
    ),
    cav = stats::setNames(rep(NA_real_, length(CAV_RESULT_COLS)), CAV_RESULT_COLS),
    rotd50_psa = rep(NA_real_, nper),
    rotd100_psa = rep(NA_real_, nper),
    gmrotd50_psa = rep(NA_real_, nper),
    rotd50_peaks = c(PGA = NA_real_, PGV = NA_real_, PGD = NA_real_),
    rotd100_peaks = c(PGA = NA_real_, PGV = NA_real_, PGD = NA_real_),
    gmrotd50_peaks = c(PGA = NA_real_, PGV = NA_real_, PGD = NA_real_),
    eas_bounds = c(LUF_Hz = NA_real_, HUF_Hz = NA_real_),
    eas_values = rep(NA_real_, length(FOURIER_COLS)),
    fav_bounds = c(LUF_Hz = NA_real_, HUF_Hz = NA_real_),
    fav_values = rep(NA_real_, length(FOURIER_COLS))
  )
}

compute_one_rsn_result <- function(h5_file, s, i) {
  result <- new_empty_rsn_result(i)

  h1 <- read_waveform_set(
    h5_file,
    s$acc_path_H1, s$group_path_H1, s$Trimmed_Filtered_H1,
    s$timeStep_H1, s$sampling_rate_H1
  )
  h2 <- read_waveform_set(
    h5_file,
    s$acc_path_H2, s$group_path_H2, s$Trimmed_Filtered_H2,
    s$timeStep_H2, s$sampling_rate_H2
  )
  vv <- read_waveform_set(
    h5_file,
    s$acc_path_V, s$group_path_V, s$Trimmed_Filtered_V,
    s$timeStep_V, s$sampling_rate_V
  )

  dt1 <- h1$dt
  dt2 <- h2$dt
  dtv <- vv$dt

  result$meta_peaks[c("pgv.mps H1", "pgd.m H1")] <- c(
    safe_peak_abs(h1$vel), safe_peak_abs(h1$disp)
  )
  result$meta_peaks[c("pgv.mps H2", "pgd.m H2")] <- c(
    safe_peak_abs(h2$vel), safe_peak_abs(h2$disp)
  )
  result$meta_peaks[c("pgv.mps V", "pgd.m V")] <- c(
    safe_peak_abs(vv$vel), safe_peak_abs(vv$disp)
  )

  if (valid_series(h1$acc) && valid_dt(dt1)) {
    if (DO_PSA_H1) {
      result$psa_h1 <- compute_psa_component(h1$acc, dt1, periods)
    }

    if (DO_DURATION) {
      ai <- compute_arias(h1$acc, dt1)
      tlev <- get_duration_times(ai, levels = DURATION_LEVELS)
      result$duration["H1-Start Time 0.05"] <- tlev[1L]
      result$duration[paste0("H1-End Time ", DURATION_END_LEVELS)] <- tlev[-1L]
      result$duration["H1-Arias Intensity (m/s) 0.05-0.95"] <-
        get_arias_range(ai, 0.05, 0.95)
    }

    if (DO_CAV) {
      cav <- compute_cav(h1$acc, dt1)
      result$cav[c("CAV_H1", "CAV5_H1", "CAV0.05g_H1")] <-
        unname(cav[c("CAV", "CAV5", "CAV0.05g")])
    }
  }

  if (valid_series(h2$acc) && valid_dt(dt2)) {
    if (DO_PSA_H2) {
      result$psa_h2 <- compute_psa_component(h2$acc, dt2, periods)
    }

    if (DO_DURATION) {
      ai <- compute_arias(h2$acc, dt2)
      tlev <- get_duration_times(ai, levels = DURATION_LEVELS)
      result$duration["H2-Start Time 0.05"] <- tlev[1L]
      result$duration[paste0("H2-End Time ", DURATION_END_LEVELS)] <- tlev[-1L]
      result$duration["H2-Arias Intensity (m/s) 0.05-0.95"] <-
        get_arias_range(ai, 0.05, 0.95)
    }

    if (DO_CAV) {
      cav <- compute_cav(h2$acc, dt2)
      result$cav[c("CAV_H2", "CAV5_H2", "CAV0.05g_H2")] <-
        unname(cav[c("CAV", "CAV5", "CAV0.05g")])
    }
  }

  if (valid_series(vv$acc) && valid_dt(dtv)) {
    if (DO_PSA_V) {
      result$psa_v <- compute_psa_component(vv$acc, dtv, periods)
    }

    if (DO_DURATION) {
      ai <- compute_arias(vv$acc, dtv)
      tlev <- get_duration_times(ai, levels = DURATION_LEVELS)
      result$duration["V-Start Time 0.05"] <- tlev[1L]
      result$duration[paste0("V-End Time ", DURATION_END_LEVELS)] <- tlev[-1L]
      result$duration["V-Arias Intensity (m/s) 0.05-0.95"] <-
        get_arias_range(ai, 0.05, 0.95)
    }

    if (DO_CAV) {
      cav <- compute_cav(vv$acc, dtv)
      result$cav[c("CAV_V", "CAV5_V", "CAV0.05g_V")] <-
        unname(cav[c("CAV", "CAV5", "CAV0.05g")])
    }
  }

  pair_acc_ok <- valid_series(h1$acc) && valid_series(h2$acc) &&
    valid_dt(dt1) && valid_dt(dt2) && abs(dt1 - dt2) < 1e-8

  if (pair_acc_ok) {
    if (DO_ROTD50) {
      result$rotd50_psa <- compute_rotd(h1$acc, h2$acc, dt1, periods, 50)
    }
    if (DO_ROTD100) {
      result$rotd100_psa <- compute_rotd(h1$acc, h2$acc, dt1, periods, 100)
    }

    if (DO_ROTD50 || DO_ROTD100) {
      rotd_m <- compute_rotd_motion_measures(
        h1$acc, h2$acc, h1$vel, h2$vel, h1$disp, h2$disp
      )

      if (DO_ROTD50) {
        result$rotd50_peaks <- c(
          PGA = unname(rotd_m["PGA50"]),
          PGV = unname(rotd_m["PGV50"]),
          PGD = unname(rotd_m["PGD50"])
        )
      }
      if (DO_ROTD100) {
        result$rotd100_peaks <- c(
          PGA = unname(rotd_m["PGA100"]),
          PGV = unname(rotd_m["PGV100"]),
          PGD = unname(rotd_m["PGD100"])
        )
      }
    }

    if (DO_GMROTD50) {
      gm <- compute_gmrotd50(
        h1$acc, h2$acc, h1$vel, h2$vel, h1$disp, h2$disp,
        dt1, periods
      )
      result$gmrotd50_psa <- gm$PSA
      result$gmrotd50_peaks <- c(PGA = gm$PGA, PGV = gm$PGV, PGD = gm$PGD)
    }

    if (DO_EAS) {
      eas <- compute_eas_spectrum(
        h1$acc, h2$acc, dt1,
        scalar_numeric(s$fcHP_H1), scalar_numeric(s$fcHP_H2),
        scalar_numeric(s$fcLP_H1), scalar_numeric(s$fcLP_H2)
      )
      if (!is.null(eas)) {
        result$eas_bounds <- c(LUF_Hz = eas$LUF_Hz, HUF_Hz = eas$HUF_Hz)
        result$eas_values <- eas$eas
      }
    }
  }

  if (DO_FAV && valid_series(vv$acc) && valid_dt(dtv)) {
    fav <- compute_fav_spectrum(
      vv$acc, dtv,
      scalar_numeric(s$fcHP_V), scalar_numeric(s$fcLP_V)
    )
    if (!is.null(fav)) {
      result$fav_bounds <- c(LUF_Hz = fav$LUF_Hz, HUF_Hz = fav$HUF_Hz)
      result$fav_values <- fav$fav
    }
  }

  result
}

safe_process_rsn_chunk <- function(h5_file, sets, job) {
  tryCatch({
    chunk_results <- lapply(job$indices, function(i) {
      compute_one_rsn_result(h5_file, sets[i, , drop = FALSE], i)
    })

    payload <- list(
      version = RSN_CHECKPOINT_VERSION,
      indices = as.integer(job$indices),
      results = chunk_results
    )
    atomic_save_rds(payload, job$checkpoint_file)

    try(rhdf5::h5closeAll(), silent = TRUE)

    list(
      ok = TRUE,
      chunk_id = job$chunk_id,
      indices = job$indices,
      checkpoint_file = job$checkpoint_file,
      error = NA_character_
    )
  }, error = function(e) {
    try(rhdf5::h5closeAll(), silent = TRUE)
    list(
      ok = FALSE,
      chunk_id = job$chunk_id,
      indices = job$indices,
      checkpoint_file = job$checkpoint_file,
      error = conditionMessage(e)
    )
  })
}

worker_process_rsn_chunk <- function(job) {
  safe_process_rsn_chunk(.current_h5_file, .current_sets, job)
}

load_all_rsn_results <- function(jobs, n_sets) {
  payloads <- lapply(jobs, function(job) {
    payload <- read_valid_chunk_checkpoint(job)
    if (is.null(payload)) {
      stop("Missing or invalid RSN checkpoint: ", job$checkpoint_file)
    }
    payload
  })

  results <- unlist(lapply(payloads, `[[`, "results"), recursive = FALSE)
  indices <- vapply(results, function(x) as.integer(x$index), integer(1))

  if (length(indices) != n_sets || anyDuplicated(indices) ||
      !setequal(indices, seq_len(n_sets))) {
    stop("RSN checkpoint set is incomplete or contains duplicate indices.")
  }

  results[order(indices)]
}

stack_rsn_field <- function(rsn_results, field) {
  do.call(rbind, lapply(rsn_results, `[[`, field))
}

merge_rsn_results_into_outputs <- function(rsn_results,
                                           meta_out,
                                           psa_h1_out,
                                           psa_h2_out,
                                           psa_v_out,
                                           rotd50_out,
                                           rotd100_out,
                                           gmrotd50_out,
                                           eas_out,
                                           fav_out,
                                           dur_out,
                                           cav_out) {
  idx <- vapply(rsn_results, function(x) as.integer(x$index), integer(1))

  meta_out[idx, META_PEAK_COLS] <- stack_rsn_field(rsn_results, "meta_peaks")

  if (DO_PSA_H1) psa_h1_out[idx, per_cols] <- stack_rsn_field(rsn_results, "psa_h1")
  if (DO_PSA_H2) psa_h2_out[idx, per_cols] <- stack_rsn_field(rsn_results, "psa_h2")
  if (DO_PSA_V)  psa_v_out[idx, per_cols]  <- stack_rsn_field(rsn_results, "psa_v")

  if (DO_DURATION) {
    dur_out[idx, DURATION_RESULT_COLS] <- stack_rsn_field(rsn_results, "duration")
  }

  if (DO_CAV) {
    cav_out[idx, CAV_RESULT_COLS] <- stack_rsn_field(rsn_results, "cav")
  }

  if (DO_ROTD50) {
    rotd50_out[idx, per_cols] <- stack_rsn_field(rsn_results, "rotd50_psa")
    rotd50_out[idx, c("PGA", "PGV", "PGD")] <-
      stack_rsn_field(rsn_results, "rotd50_peaks")
  }

  if (DO_ROTD100) {
    rotd100_out[idx, per_cols] <- stack_rsn_field(rsn_results, "rotd100_psa")
    rotd100_out[idx, c("PGA", "PGV", "PGD")] <-
      stack_rsn_field(rsn_results, "rotd100_peaks")
  }

  if (DO_GMROTD50) {
    gmrotd50_out[idx, per_cols] <- stack_rsn_field(rsn_results, "gmrotd50_psa")
    gmrotd50_out[idx, c("PGA", "PGV", "PGD")] <-
      stack_rsn_field(rsn_results, "gmrotd50_peaks")
  }

  if (DO_EAS) {
    eas_bounds <- stack_rsn_field(rsn_results, "eas_bounds")
    eas_out$LUF_Hz[idx] <- eas_bounds[, "LUF_Hz"]
    eas_out$HUF_Hz[idx] <- eas_bounds[, "HUF_Hz"]
    eas_out[idx, FOURIER_COLS] <- stack_rsn_field(rsn_results, "eas_values")
  }

  if (DO_FAV) {
    fav_bounds <- stack_rsn_field(rsn_results, "fav_bounds")
    fav_out$LUF_Hz[idx] <- fav_bounds[, "LUF_Hz"]
    fav_out$HUF_Hz[idx] <- fav_bounds[, "HUF_Hz"]
    fav_out[idx, FOURIER_COLS] <- stack_rsn_field(rsn_results, "fav_values")
  }

  list(
    meta_out = meta_out,
    psa_h1_out = psa_h1_out,
    psa_h2_out = psa_h2_out,
    psa_v_out = psa_v_out,
    rotd50_out = rotd50_out,
    rotd100_out = rotd100_out,
    gmrotd50_out = gmrotd50_out,
    eas_out = eas_out,
    fav_out = fav_out,
    dur_out = dur_out,
    cav_out = cav_out
  )
}

## -------------------- Excel writer --------------------

write_standard_sheet <- function(wb, sheet_name, data, header_style) {
  openxlsx::addWorksheet(wb, sheet_name)
  openxlsx::writeData(
    wb,
    sheet = sheet_name,
    x = data,
    headerStyle = header_style,
    withFilter = FALSE,
    keepNA = FALSE
  )
  openxlsx::freezePane(wb, sheet_name, firstRow = TRUE)

  if (ncol(data) > 0L) {
    first_cols <- seq_len(min(12L, ncol(data)))
    openxlsx::setColWidths(wb, sheet_name, cols = first_cols, widths = "auto")
    if (ncol(data) > 12L) {
      openxlsx::setColWidths(wb, sheet_name, cols = 13:ncol(data), widths = 12)
    }
  }
}

atomic_save_workbook <- function(wb, out_xlsx) {
  out_xlsx <- normalizePath(out_xlsx, winslash = "/", mustWork = FALSE)
  out_dir <- dirname(out_xlsx)
  if (!dir.exists(out_dir)) dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

  tmp_file <- file.path(
    out_dir,
    paste0(".__IM_tmp_", Sys.getpid(), "_", basename(out_xlsx))
  )

  on.exit(if (file.exists(tmp_file)) unlink(tmp_file), add = TRUE)
  openxlsx::saveWorkbook(wb, tmp_file, overwrite = TRUE)

  if (file.exists(out_xlsx) && !file.remove(out_xlsx)) {
    stop(
      "Cannot overwrite output workbook. Close it in Excel and rerun: ",
      out_xlsx
    )
  }

  if (!file.rename(tmp_file, out_xlsx)) {
    copied <- file.copy(tmp_file, out_xlsx, overwrite = TRUE)
    if (!copied) stop("Could not move completed workbook to: ", out_xlsx)
    unlink(tmp_file)
  }

  invisible(out_xlsx)
}

write_current_excel <- function(out_xlsx,
                                meta_out,
                                psa_h1_out,
                                psa_h2_out,
                                psa_v_out,
                                rotd50_out,
                                rotd100_out,
                                gmrotd50_out,
                                eas_out,
                                fav_out,
                                dur_out,
                                cav_out) {
  wb <- openxlsx::createWorkbook()
  header_style <- openxlsx::createStyle(
    textDecoration = "bold",
    halign = "center",
    valign = "center",
    wrapText = TRUE
  )

  write_standard_sheet(wb, "metadata", meta_out, header_style)

  if (DO_PSA_H1) write_standard_sheet(wb, "As_Recorded_PSA_H1", psa_h1_out, header_style)
  if (DO_PSA_H2) write_standard_sheet(wb, "As_Recorded_PSA_H2", psa_h2_out, header_style)
  if (DO_PSA_V)  write_standard_sheet(wb, "As_Recorded_PSA_V",  psa_v_out,  header_style)

  if (DO_ROTD50)   write_standard_sheet(wb, "RotD50",   rotd50_out,   header_style)
  if (DO_ROTD100)  write_standard_sheet(wb, "RotD100",  rotd100_out,  header_style)
  if (DO_GMROTD50) write_standard_sheet(wb, "gmRotD50", gmrotd50_out, header_style)

  if (DO_EAS)      write_standard_sheet(wb, "EAS",      eas_out, header_style)
  if (DO_FAV)      write_standard_sheet(wb, "FAV",      fav_out, header_style)
  if (DO_DURATION) write_standard_sheet(wb, "Duration", dur_out, header_style)
  if (DO_CAV)      write_standard_sheet(wb, "CAV",      cav_out, header_style)

  atomic_save_workbook(wb, out_xlsx)
}

## -------------------- Main processor for one H5 --------------------

process_one_h5 <- function(h5_file, cl = NULL) {
  cat("\n============================================================\n")
  cat("Processing:", h5_file, "\n")

  meta_xlsx <- find_matching_meta(h5_file)
  if (is.na(meta_xlsx) || !file.exists(meta_xlsx)) {
    stop("No matching metadata Excel found for: ", h5_file)
  }

  out_xlsx <- cfg$output_workbook
  cat("Metadata:", meta_xlsx, "\n")
  cat("Output:", out_xlsx, "\n")

  ## -------------------- Read metadata --------------------
  meta_raw <- readxl::read_excel(meta_xlsx, sheet = 1, .name_repair = "unique")

  required_cols <- c("trace.id", "event_id", "component", "station", "network")
  missing_required <- setdiff(required_cols, names(meta_raw))
  if (length(missing_required) > 0L) {
    stop("Missing required metadata columns: ", paste(missing_required, collapse = ", "))
  }

  optional_cols <- c(
    "rec_id", "timeStep", "pga", "rawpga", "hypdis", "epidis",
    "est.hypdis.Pwave.duration", "fcHP", "fcLP"
  )
  for (nm in optional_cols) {
    if (!nm %in% names(meta_raw)) meta_raw[[nm]] <- NA
  }

  meta <- meta_raw %>%
    dplyr::transmute(
      source_row = dplyr::row_number(),
      trace.id = trimws(as.character(`trace.id`)),
      trace_key = normalize_trace_id(`trace.id`),
      event_id = normalize_event_id(event_id),
      rec_id = trimws(as.character(rec_id)),
      component = toupper(trimws(as.character(component))),
      station = trimws(as.character(station)),
      network = trimws(as.character(network)),
      timeStep = suppressWarnings(as.numeric(timeStep)),
      fcHP = suppressWarnings(as.numeric(fcHP)),
      fcLP = suppressWarnings(as.numeric(fcLP)),
      pga = suppressWarnings(as.numeric(pga)),
      rawpga = suppressWarnings(as.numeric(rawpga)),
      hypdis = suppressWarnings(as.numeric(hypdis)),
      epidis = suppressWarnings(as.numeric(epidis)),
      est.hypdis.Pwave.duration = suppressWarnings(as.numeric(est.hypdis.Pwave.duration))
    ) %>%
    dplyr::filter(!is.na(trace_key) & trace_key != "")

  if (nrow(meta) == 0L) stop("Metadata contains no usable trace IDs: ", meta_xlsx)

  ## -------------------- Build HDF5 index and match metadata --------------------
  h5_index <- build_h5_trace_index(h5_file)
  meta_joined <- match_metadata_to_h5(meta, h5_index)

  meta_joined <- meta_joined %>%
    dplyr::mutate(
      trace.id = dplyr::coalesce(trace.id_h5, trace_key),
      timeStep = dplyr::case_when(
        is.finite(timeStep) & timeStep > 0 & timeStep != -999 ~ timeStep,
        is.finite(sampling_rate) & sampling_rate > 0 ~ 1 / sampling_rate,
        TRUE ~ NA_real_
      ),
      hypdis = dplyr::coalesce(hypdis, hypdis_h5),
      channel_family = substr(component, 1L, 2L),
      role = dplyr::case_when(
        stringr::str_detect(component, "E$") ~ "H1",
        stringr::str_detect(component, "N$") ~ "H2",
        stringr::str_detect(component, "Z$") ~ "V",
        TRUE ~ NA_character_
      ),
      record_key = extract_record_key(trace_key, rec_id, event_id),
      usable_rank = ifelse(is_usable_trace(Trimmed_Filtered), 1L, 0L),
      matched_rank = ifelse(h5_matched, 1L, 0L)
    ) %>%
    dplyr::filter(!is.na(role))

  cat("Metadata trace rows:", nrow(meta), "\n")
  cat("Matched to HDF5:", sum(meta_joined$h5_matched, na.rm = TRUE), "\n")
  cat("Trimmed_Filtered == Yes:",
      sum(toupper(trimws(meta_joined$Trimmed_Filtered)) == "YES", na.rm = TRUE), "\n")

  if (nrow(meta_joined) == 0L) stop("No H1/H2/V traces could be classified.")

  ## If duplicate candidates exist for the same component, prefer a trace that
  ## matched HDF5 and is marked usable, then keep the earliest metadata row.
  meta_joined <- meta_joined %>%
    dplyr::arrange(
      event_id, station, network, channel_family, role,
      dplyr::desc(usable_rank), dplyr::desc(matched_rank), source_row
    ) %>%
    dplyr::group_by(event_id, station, network, channel_family, role) %>%
    dplyr::slice_head(n = 1L) %>%
    dplyr::ungroup()

  ## -------------------- Build complete H1/H2/V record sets --------------------
  sets <- meta_joined %>%
    dplyr::select(
      event_id, station, network, channel_family, role,
      trace.id, group_path, acc_path, Trimmed_Filtered,
      sampling_rate, timeStep, fcHP, fcLP,
      pga, rawpga, hypdis, epidis, est.hypdis.Pwave.duration
    ) %>%
    tidyr::pivot_wider(
      id_cols = c(event_id, station, network, channel_family),
      names_from = role,
      values_from = c(
        trace.id, group_path, acc_path, Trimmed_Filtered,
        sampling_rate, timeStep, fcHP, fcLP,
        pga, rawpga, hypdis, epidis, est.hypdis.Pwave.duration
      ),
      names_sep = "_"
    )

  sets <- ensure_set_columns(sets) %>%
    dplyr::filter(
      !is.na(trace.id_H1) | !is.na(trace.id_H2) | !is.na(trace.id_V)
    ) %>%
    dplyr::arrange(event_id, station, network, channel_family) %>%
    dplyr::mutate(RSN = dplyr::row_number())

  n_sets <- nrow(sets)
  if (n_sets == 0L) stop("No record sets were built.")

  cat("Record sets:", n_sets, "\n")
  cat("  With H1:", sum(!is.na(sets$trace.id_H1)), "\n")
  cat("  With H2:", sum(!is.na(sets$trace.id_H2)), "\n")
  cat("  With V :", sum(!is.na(sets$trace.id_V)), "\n")
  cat("  With H1+H2:", sum(!is.na(sets$trace.id_H1) & !is.na(sets$trace.id_H2)), "\n")

  ## -------------------- Prepare outputs --------------------
  meta_out <- sets %>%
    dplyr::transmute(
      RSN = RSN,
      pathH1 = trace.id_H1,
      pathH2 = trace.id_H2,
      pathV = trace.id_V,
      eventid = event_id,
      station = station,
      network = network,
      channel_family = channel_family,
      component1 = "H1",
      component2 = "H2",
      componentV = "V",
      `rawpga.g H1` = rawpga_H1,
      `rawpga.g H2` = rawpga_H2,
      `rawpga.g V` = rawpga_V,
      `pga.g H1` = pga_H1,
      `pgv.mps H1` = NA_real_,
      `pgd.m H1` = NA_real_,
      `pga.g H2` = pga_H2,
      `pgv.mps H2` = NA_real_,
      `pgd.m H2` = NA_real_,
      `pga.g V` = pga_V,
      `pgv.mps V` = NA_real_,
      `pgd.m V` = NA_real_,
      fcHP1 = fcHP_H1,
      fcHP2 = fcHP_H2,
      fcHPV = fcHP_V,
      fcLP1 = fcLP_H1,
      fcLP2 = fcLP_H2,
      fcLPV = fcLP_V,
      epidis = dplyr::coalesce(epidis_H1, epidis_H2, epidis_V),
      `hypdis(km)` = dplyr::coalesce(hypdis_H1, hypdis_H2, hypdis_V),
      `est.hypdis.Pwave.duration(km)` = dplyr::coalesce(
        est.hypdis.Pwave.duration_H1,
        est.hypdis.Pwave.duration_H2,
        est.hypdis.Pwave.duration_V
      )
    )

  pair_tmax <- safe_pair_tmax(sets$fcHP_H1, sets$fcHP_H2)
  vertical_tmax <- safe_single_tmax(sets$fcHP_V)

  make_period_table <- function(base_data) {
    spectral <- as.data.frame(
      matrix(NA_real_, nrow = n_sets, ncol = length(per_cols)),
      check.names = FALSE
    )
    names(spectral) <- per_cols
    data.frame(base_data, spectral, check.names = FALSE, stringsAsFactors = FALSE)
  }

  psa_h1_out <- make_period_table(data.frame(
    RSN = sets$RSN,
    file1 = sets$trace.id_H1,
    fHP1 = sets$fcHP_H1,
    fHP2 = sets$fcHP_H2,
    tmax4penalty = pair_tmax,
    check.names = FALSE,
    stringsAsFactors = FALSE
  ))

  psa_h2_out <- make_period_table(data.frame(
    RSN = sets$RSN,
    file2 = sets$trace.id_H2,
    fHP1 = sets$fcHP_H1,
    fHP2 = sets$fcHP_H2,
    tmax4penalty = pair_tmax,
    check.names = FALSE,
    stringsAsFactors = FALSE
  ))

  psa_v_out <- make_period_table(data.frame(
    RSN = sets$RSN,
    file1 = sets$trace.id_V,
    fHPV = sets$fcHP_V,
    fcLPV = sets$fcLP_V,
    tmax4penalty = vertical_tmax,
    check.names = FALSE,
    stringsAsFactors = FALSE
  ))

  make_rotd_table <- function() {
    make_period_table(data.frame(
      RSN = sets$RSN,
      file1 = sets$trace.id_H1,
      file2 = sets$trace.id_H2,
      fHP1 = sets$fcHP_H1,
      fHP2 = sets$fcHP_H2,
      tmax4penalty = pair_tmax,
      PGA = NA_real_,
      PGV = NA_real_,
      PGD = NA_real_,
      check.names = FALSE,
      stringsAsFactors = FALSE
    ))
  }

  rotd50_out <- make_rotd_table()
  rotd100_out <- make_rotd_table()
  gmrotd50_out <- make_rotd_table()

  eas_spectral <- as.data.frame(
    matrix(NA_real_, nrow = n_sets, ncol = length(FOURIER_COLS)),
    check.names = FALSE
  )
  names(eas_spectral) <- FOURIER_COLS
  eas_out <- data.frame(
    RSN = sets$RSN,
    file1 = sets$trace.id_H1,
    file2 = sets$trace.id_H2,
    fHP1 = sets$fcHP_H1,
    fHP2 = sets$fcHP_H2,
    fLP1 = sets$fcLP_H1,
    fLP2 = sets$fcLP_H2,
    LUF_Hz = NA_real_,
    HUF_Hz = NA_real_,
    Units = "g*s",
    eas_spectral,
    check.names = FALSE,
    stringsAsFactors = FALSE
  )

  fav_spectral <- as.data.frame(
    matrix(NA_real_, nrow = n_sets, ncol = length(FOURIER_COLS)),
    check.names = FALSE
  )
  names(fav_spectral) <- FOURIER_COLS
  fav_out <- data.frame(
    RSN = sets$RSN,
    fileV = sets$trace.id_V,
    fHPV = sets$fcHP_V,
    fLPV = sets$fcLP_V,
    LUF_Hz = NA_real_,
    HUF_Hz = NA_real_,
    Units = "g*s",
    fav_spectral,
    check.names = FALSE,
    stringsAsFactors = FALSE
  )

  dur_cols <- c(
    "RSN",
    "H1-Start Time 0.05",
    paste0("H1-End Time ", seq(0.1, 0.95, by = 0.05)),
    "H2-Start Time 0.05",
    paste0("H2-End Time ", seq(0.1, 0.95, by = 0.05)),
    "V-Start Time 0.05",
    paste0("V-End Time ", seq(0.1, 0.95, by = 0.05)),
    "H1-Arias Intensity (m/s) 0.05-0.95",
    "H2-Arias Intensity (m/s) 0.05-0.95",
    "V-Arias Intensity (m/s) 0.05-0.95"
  )
  dur_out <- data.frame(
    matrix(NA_real_, nrow = n_sets, ncol = length(dur_cols)),
    check.names = FALSE
  )
  names(dur_out) <- dur_cols
  dur_out$RSN <- sets$RSN

  cav_out <- data.frame(
    RSN = sets$RSN,
    CAV_H1 = NA_real_, CAV5_H1 = NA_real_, `CAV0.05g_H1` = NA_real_,
    CAV_H2 = NA_real_, CAV5_H2 = NA_real_, `CAV0.05g_H2` = NA_real_,
    CAV_V = NA_real_, CAV5_V = NA_real_, `CAV0.05g_V` = NA_real_,
    check.names = FALSE,
    stringsAsFactors = FALSE
  )

  ## -------------------- Adaptive RSN/chunk computation --------------------
  ## Excel is intentionally not rebuilt during the RSN loop. Each completed
  ## chunk is saved quickly as RDS, and the complete XLSX is written once.

  on.exit({
    try(rhdf5::h5closeAll(), silent = TRUE)
    if (!is.null(cl)) {
      try(parallel::clusterEvalQ(cl, {
        try(rhdf5::h5closeAll(), silent = TRUE)
        if (exists(".current_h5_file", envir = .GlobalEnv, inherits = FALSE)) {
          rm(".current_h5_file", envir = .GlobalEnv)
        }
        if (exists(".current_sets", envir = .GlobalEnv, inherits = FALSE)) {
          rm(".current_sets", envir = .GlobalEnv)
        }
        gc()
        NULL
      }), silent = TRUE)
    }
  }, add = TRUE)

  parallel_for_file <- !is.null(cl) && isTRUE(USE_PARALLEL) &&
    N_WORKERS > 1L && n_sets >= RSN_PARALLEL_THRESHOLD

  workers_for_chunks <- if (parallel_for_file) min(N_WORKERS, n_sets) else 1L
  chunks <- make_rsn_chunks(n_sets, workers_for_chunks)
  manifest <- build_checkpoint_manifest(h5_file, meta_xlsx, sets, chunks)
  checkpoint <- prepare_checkpoint_directory(out_xlsx, manifest)
  jobs <- make_chunk_jobs(chunks, checkpoint$dir)

  completed <- vapply(
    jobs,
    function(job) !is.null(read_valid_chunk_checkpoint(job)),
    logical(1)
  )
  pending_jobs <- jobs[!completed]
  completed_rsn_count <- sum(vapply(
    jobs[completed],
    function(job) length(job$indices),
    integer(1)
  ))
  file_progress_label <- sub(
    "_processed_2\\.h5$", "", basename(h5_file),
    ignore.case = TRUE
  )

  cat("RSN chunks:", length(jobs), "total;", sum(completed), "already complete;",
      length(pending_jobs), "pending.\n")
  if (isTRUE(SHOW_LIVE_RSN_PROGRESS)) {
    cat(sprintf(
      "[%s] Starting at %d/%d RSNs complete (%.1f%%).\n",
      file_progress_label,
      completed_rsn_count,
      n_sets,
      if (n_sets > 0L) 100 * completed_rsn_count / n_sets else 100
    ))
    flush.console()
  }
  if (checkpoint$resumed && sum(completed) > 0L) {
    cat("Resuming from existing valid RDS checkpoints.\n")
  }

  if (length(pending_jobs) > 0L) {
    if (parallel_for_file) {
      cat(
        "Running RSN-level parallel processing with", workers_for_chunks,
        "workers for", n_sets, "RSNs.\n"
      )

      .current_h5_file <- h5_file
      .current_sets <- sets
      parallel::clusterExport(
        cl,
        c(".current_h5_file", ".current_sets"),
        envir = environment()
      )

      chunk_status <- parallel_chunk_lapply_with_progress(
        cl = cl,
        jobs = pending_jobs,
        fun = worker_process_rsn_chunk,
        total_rsns = n_sets,
        already_completed_rsns = completed_rsn_count,
        file_label = file_progress_label
      )
    } else {
      cat("Running RSN chunks sequentially for this small file.\n")
      chunk_status <- sequential_chunk_lapply_with_progress(
        jobs = pending_jobs,
        fun = function(job) safe_process_rsn_chunk(h5_file, sets, job),
        total_rsns = n_sets,
        already_completed_rsns = completed_rsn_count,
        file_label = file_progress_label
      )
    }

    chunk_ok <- vapply(chunk_status, function(x) isTRUE(x$ok), logical(1))
    if (any(!chunk_ok)) {
      failed <- chunk_status[!chunk_ok]
      details <- vapply(failed, function(x) {
        paste0(
          "chunk ", x$chunk_id,
          " (RSN ", min(x$indices), "-", max(x$indices), "): ",
          x$error
        )
      }, character(1))
      stop(
        "One or more RSN chunks failed. Completed checkpoints were retained. ",
        "Rerun after addressing the error. Details: ",
        paste(details, collapse = " | ")
      )
    }
  }

  rsn_results <- load_all_rsn_results(jobs, n_sets)
  merged <- merge_rsn_results_into_outputs(
    rsn_results,
    meta_out,
    psa_h1_out,
    psa_h2_out,
    psa_v_out,
    rotd50_out,
    rotd100_out,
    gmrotd50_out,
    eas_out,
    fav_out,
    dur_out,
    cav_out
  )

  meta_out <- merged$meta_out
  psa_h1_out <- merged$psa_h1_out
  psa_h2_out <- merged$psa_h2_out
  psa_v_out <- merged$psa_v_out
  rotd50_out <- merged$rotd50_out
  rotd100_out <- merged$rotd100_out
  gmrotd50_out <- merged$gmrotd50_out
  eas_out <- merged$eas_out
  fav_out <- merged$fav_out
  dur_out <- merged$dur_out
  cav_out <- merged$cav_out

  ## One final workbook write replaces the former full-XLSX save every 5 RSNs.
  write_current_excel(
    out_xlsx, meta_out, psa_h1_out, psa_h2_out, psa_v_out,
    rotd50_out, rotd100_out, gmrotd50_out,
    eas_out, fav_out, dur_out, cav_out
  )

  if (!isTRUE(KEEP_RSN_CHECKPOINTS_AFTER_SUCCESS) && dir.exists(checkpoint$dir)) {
    unlink(checkpoint$dir, recursive = TRUE, force = TRUE)
  }

  try(rhdf5::h5closeAll(), silent = TRUE)
  gc()
  cat("Finished:", out_xlsx, "\n")
  invisible(out_xlsx)
}

safe_process_one_h5 <- function(h5_file, cl = NULL) {
  tryCatch(
    list(ok = TRUE, file = h5_file, output = process_one_h5(h5_file, cl), error = NA_character_),
    error = function(e) {
      try(rhdf5::h5closeAll(), silent = TRUE)
      list(ok = FALSE, file = h5_file, output = NA_character_, error = conditionMessage(e))
    }
  )
}


parallel_chunk_lapply_with_progress <- function(cl,jobs,fun,total_rsns,already_completed_rsns=0L,file_label="") {
  if(!length(jobs))return(list())
  report <- make_rsn_progress_reporter(total_rsns,already_completed_rsns,file_label)
  results <- vector("list",length(jobs)); next_job <- 1L; active <- 0L
  send <- function(worker,job)parallel:::sendCall(cl[[worker]],fun,list(jobs[[job]]),tag=job)
  for(worker in seq_len(min(length(cl),length(jobs)))) {
    if(im_cancelled())break
    send(worker,next_job); next_job <- next_job+1L; active <- active+1L
  }
  while(active>0L) {
    answer <- parallel:::recvOneResult(cl); active <- active-1L
    results[[as.integer(answer$tag)]] <- answer$value
    if(!inherits(answer$value,"try-error"))report(answer$value)
    if(next_job<=length(jobs)&&!im_cancelled()) {
      send(answer$node,next_job);next_job <- next_job+1L;active <- active+1L
    }
  }
  if(im_cancelled())stop("SIGNALQC_PAUSED")
  parallel:::checkForRemoteErrors(results)
}

# Emit application progress while retaining the original reporter.
original_progress_reporter <- make_rsn_progress_reporter
make_rsn_progress_reporter <- function(total_rsns,already_completed_rsns=0L,file_label="") {
  original <- original_progress_reporter(total_rsns,already_completed_rsns,file_label)
  done <- as.integer(already_completed_rsns)
  function(status) {
    original(status)
    if(isTRUE(status$ok))done <<- done+length(status$indices)
    im_emit("record_sets_finished",current=done,total=total_rsns,message=paste(done,"of",total_rsns,"record sets complete"))
  }
}

h5_files <- normalizePath(cfg$input_hdf5,winslash="/",mustWork=TRUE)
cl <- NULL
result <- tryCatch({
  if(isTRUE(USE_PARALLEL)&&N_WORKERS>1L&&!im_cancelled()) {
    cl <- parallel::makeCluster(N_WORKERS,outfile="")
    parallel::clusterEvalQ(cl,{
      suppressPackageStartupMessages({library(rhdf5);library(RCTC);library(readxl);library(dplyr);library(tidyr);library(stringr);library(openxlsx)})
      NULL
    })
    parallel::clusterExport(cl,setdiff(ls(envir=.GlobalEnv),c("cl","results","h5_files")),envir=.GlobalEnv)
  }
  safe_process_one_h5(h5_files,cl)
},error=function(e)list(ok=FALSE,error=conditionMessage(e)),
finally={if(!is.null(cl))try(parallel::stopCluster(cl),silent=TRUE)})
status <- if(im_cancelled())"cancelled" else if(isTRUE(result$ok))"completed" else "failed"
summary <- list(stage="intensity",status=status,input=h5_files,
  output_files=if(isTRUE(result$ok))cfg$output_workbook else character(),
  message=if(isTRUE(result$ok))"Intensity measures complete" else result$error,
  workers=N_WORKERS,enabled_measures=cfg$enabled_measures,processing_signature=cfg$processing_signature)
jsonlite::write_json(summary,file.path(cfg$output_dir,"intensity_summary.json"),auto_unbox=TRUE,pretty=TRUE,null="null",na="null")
im_emit("finished",status=status,message=summary$message)
if(status=="failed")quit(status=2L)
