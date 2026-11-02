# Application version of Main_AutoFC_v11.R. Original files are retained alongside.
# v13: user-requested missing-corner defaults: HP 0.1 Hz, LP 8 Hz; log use.
# Core: same arrival refinement, windows, options 1/2/3/7/HK aggregation,
# Filter(), baseline/integration equations and five Stage-2 rejection thresholds.
# Changes: explicit inputs; exact identities; error isolation; headless figures;
# metadata repairs; durable per-trace checkpoints and exact-array HDF5 output.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1L) stop("Usage: Rscript Main_AutoFC_v13_app.R config.json")
entry <- sub("^--file=", "", commandArgs()[grepl("^--file=", commandArgs())][1])
engine_dir <- dirname(normalizePath(entry, winslash = "/", mustWork = TRUE))
app_dir <- normalizePath(file.path(engine_dir, "../.."), winslash = "/")
local_library <- file.path(app_dir, "runtime", "R-library")
if (dir.exists(local_library)) .libPaths(c(local_library, .libPaths()))
for (pkg in c("jsonlite", "reticulate", "zoo", "RCTC", "R.matlab")) {
  if (!requireNamespace(pkg, quietly = TRUE)) stop("Missing R package: ", pkg, ". Run the application dependency setup.")
}
cfg <- jsonlite::fromJSON(normalizePath(args[1], winslash = "/"))
`%or%` <- function(a, b) if (is.null(a) || length(a) == 0L || identical(a, "")) b else a
required <- c("input_hdf5", "stage1_metadata", "output_dir", "python_executable")
for (key in required) if (is.null(cfg[[key]])) stop("Missing configuration key: ", key)
h5file <- normalizePath(cfg$input_hdf5, winslash = "/", mustWork = TRUE)
metafile <- normalizePath(cfg$stage1_metadata, winslash = "/", mustWork = TRUE)
dir.create(cfg$output_dir, recursive = TRUE, showWarnings = FALSE)
outfolder <- normalizePath(cfg$output_dir, winslash = "/")
suffix <- cfg$processed_suffix %or% "processed_2"
if (!grepl("^[A-Za-z0-9_.-]+$", suffix)) stop("Processed suffix may contain only letters, numbers, underscores, dots and hyphens")
processed_h5 <- file.path(outfolder, paste0(tools::file_path_sans_ext(basename(h5file)), "_", suffix, ".h5"))
if (identical(normalizePath(processed_h5, winslash = "/", mustWork = FALSE), h5file)) stop("Output HDF5 must differ from input")
outcsv <- file.path(outfolder, paste0(basename(h5file), "filmeta.csv"))
outxlsx <- file.path(outfolder, paste0(basename(h5file), "filmeta.xlsx"))
save_figures <- isTRUE(cfg$save_figures %or% TRUE)
resume <- isTRUE(cfg$resume %or% TRUE)
matlab_exe <- cfg$matlab_executable %or% Sys.which("matlab")
if (!nzchar(matlab_exe) || !file.exists(matlab_exe)) {
  stop("MATLAB is required for the existing Guo (Option 7) method. Select its executable in Settings.")
}
setwd(engine_dir)
Sys.setenv(RETICULATE_PYTHON = cfg$python_executable, PYTHONIOENCODING = "utf-8")
library(reticulate)
library(zoo)
library(RCTC)
use_python(cfg$python_executable, required = TRUE)
io <- import_from_path("stage2_io", path = engine_dir, convert = TRUE)
hk_nn <- import_from_path("hk_nn", path = engine_dir, convert = TRUE)
for (file in c("autofc_v2_app.R", "autoparr.R", "Baseline.r", "DC.mean.r", "fbutter.r",
               "FAS.r", "Filter.r", "Integrate2VD.r", "Taper.r", "windows.r", "SMOOTH.r",
               "hk_nn_wrapper.R", "autofc_option7_matlab_v2_app.R")) source(file)
source("corner_selection_v1_app.R")
signature <- io$run_signature(h5file, metafile, jsonlite::toJSON(cfg, auto_unbox = TRUE, null = "null", digits = NA))
normalized <- io$prepare_metadata(h5file, metafile, outfolder)
identity_columns <- c("event_id", "trace_id", "trace.id", "network", "location", "station", "component", "starttime.s", "rec_id", "group_id", "metadata_error", "stage2_error", "method_errors", "figure_avd", "figure_fas", "method_corners", "rejection_reasons", "corner_fallback")
text_classes <- setNames(rep("character", length(identity_columns)), identity_columns)
read_metadata <- function(path) {
  headers <- names(read.csv(path, nrows = 0, check.names = FALSE))
  read.csv(path, stringsAsFactors = FALSE, check.names = FALSE, colClasses = text_classes[intersect(names(text_classes), headers)])
}
ts.info <- read_metadata(normalized)
ts.info$metadata_error[is.na(ts.info$metadata_error)] <- ""
for (name in c("fcHP", "fcLP", "windowStart", "windowEnd", "PStart", "SStart", "SEnd", "CStart", "pga", "parr1", "SNRave")) ts.info[[name]] <- NA_real_
ts.info$rejectreason3filter <- "accepted"
ts.info$stage2_status <- "pending"
ts.info$stage2_error <- ""
ts.info$method_errors <- ""
ts.info$processing_signature <- signature
ts.info$processed_hdf5 <- processed_h5
ts.info$figure_avd <- ""
ts.info$figure_fas <- ""
ts.info$method_corners <- ""
ts.info$corner_fallback <- ""
ts.info$rejection_reasons <- ""
ts.info$acrjfinal <- TRUE
ts.info$trace.id <- as.character(ts.info$trace_id)

if (resume && file.exists(outcsv)) {
  prior <- tryCatch(read_metadata(outcsv), error = function(e) NULL)
  if (!is.null(prior) && all(c("processing_signature", "event_id", "trace_id", "stage2_status") %in% names(prior))) {
    keys <- paste(prior$event_id, prior$trace_id, sep = "||")
    for (i in seq_len(nrow(ts.info))) {
      old <- which(keys == paste(ts.info$event_id[i], ts.info$trace_id[i], sep = "||") & prior$processing_signature == signature)
      if (length(old) != 1L || !prior$stage2_status[old] %in% c("completed", "rejected")) next
      if (!io$is_complete(processed_h5, as.character(ts.info$event_id[i]), ts.info$trace_id[i], signature)) next
      for (name in intersect(names(ts.info), names(prior))) ts.info[i, name] <- prior[old, name]
    }
  }
}

checkpoint <- function() {
  temporary <- paste0(outcsv, ".partial")
  write.csv(ts.info, temporary, row.names = FALSE, na = "")
  invisible(io$replace_checkpoint(temporary, outcsv))
}
cancelled <- function() !is.null(cfg$cancel_file) && file.exists(cfg$cancel_file)
number <- function(x) suppressWarnings(as.numeric(x))
valid_trace <- function(x) length(x) >= 64L && all(is.finite(x)) && max(abs(x)) > 0 && stats::sd(x) > 0
emit <- function(kind, ...) cat("SIGNALQC_EVENT ", jsonlite::toJSON(c(list(stage = "filtering", type = kind), list(...)), auto_unbox = TRUE, null = "null"), "\n", sep = "")
figure_name <- function(event, trace, kind) {
  folder <- file.path(outfolder, "Figure", kind)
  dir.create(folder, recursive = TRUE, showWarnings = FALSE)
  safe <- gsub("[^A-Za-z0-9_.-]", "_", paste(event, trace, sep = "_"))
  file.path(folder, paste0(safe, ".png"))
}

# Each group is one NSLC family with start times within one sample, as in Stage 1.
# Existing compatible triplets retain the v11 nine-pick median.
refine_arrival <- function(indices) {
  rows <- ts.info[indices, , drop = FALSE]
  picks <- numeric()
  compatible <- length(indices) == 3L && all(is.finite(number(rows$count))) && diff(range(number(rows$count))) < 10
  if (compatible) {
    for (index in indices) {
      row <- ts.info[index, , drop = FALSE]
      p0 <- number(row$parr); dt <- number(row$timeStep)
      if (!is.finite(p0) || p0 <= 0 || !is.finite(dt) || dt <= 0 || nzchar(row$metadata_error)) next
      tryCatch({
        raw <- number(io$read_acceleration(h5file, as.character(row$event_id), row$trace_id)) / 9.80665
        if (!valid_trace(raw)) stop("Invalid trace in arrival refinement")
        accts <- Filter(ts(raw, delta = dt), 1, 20, "C", TRUE)[[2]]
        picks <- c(picks, p0)
        nstart <- max(1, round((p0 - 50) / dt))
        nend <- min(length(raw), round((p0 + 50) / dt))
        if (nend - nstart < ceiling(22 / dt)) stop("Arrival window is too short for STA/LTA")
        accts0 <- ts(accts[nstart:nend], delta = dt)
        # Correct clipped-window origin: v11 used p0-50 even when this was negative.
        offset <- if (p0 >= 50) p0 - 50 else (nstart - 1) * dt
        for (option in c(1, 2)) {
          picked <- tryCatch(autoparr(accts0, Option = option) + offset, error = function(e) NA_real_)
          picks <- c(picks, picked)
        }
      }, error = function(e) message("Arrival refinement fallback: ", conditionMessage(e)))
    }
  }
  picks <- picks[is.finite(picks) & picks > 0]
  if (length(picks)) return(max(4.99, median(picks)) - 3)
  base <- number(rows$parr[1])
  if (is.finite(base)) return(max(2, base - 3))
  NA_real_
}

process_trace <- function(row) {
  eventid <- as.character(row$event_id); trace.id <- row$trace_id
  dt <- number(row$timeStep); npts <- number(row$count)
  if (nzchar(row$metadata_error)) stop(row$metadata_error)
  if (!is.finite(dt) || dt <= 0 || !is.finite(row$parr1)) stop("Invalid time step or refined P arrival")
  acc_g <- number(io$read_acceleration(h5file, eventid, trace.id)) / 9.80665
  npts <- length(acc_g)
  if (!valid_trace(acc_g)) stop("Bad trace: NaN/Inf, flat, or fewer than 64 samples")
  if (row$parr1 >= (npts - 1) * dt) stop("P arrival is outside the waveform")
  winout <- windows(number(row$parr1), number(row$hypdis), number(row$magnitude), dt, npts)
  if (!all(is.finite(unlist(winout)))) stop("Non-finite processing windows")
  # Preserve v11 one-based trim indices; record the exact sample offset separately.
  first <- max(1L, round(winout[[1]] / dt))
  last <- min(round(winout[[6]] / dt), npts - 1L)
  if (last - first < 63L) stop("Processing window contains fewer than 64 samples")
  accts <- ts(acc_g[first:last], delta = dt)
  errors <- character()
  safe_method <- function(label, expression, default = list(NA_real_, NA_real_, NA_real_)) {
    tryCatch(expression, error = function(e) {
      message(label, " failed: ", conditionMessage(e))
      errors <<- c(errors, paste(label, conditionMessage(e), sep = ": "))
      default
    })
  }
  # The active options, arguments and combination equations below are from v11.
  fc1 <- safe_method("SNR-Kishida", autofc(accts, dt, winout[[2]] - winout[[1]], dt,
      winout[[6]] - winout[[1]], mag = row$magnitude, Option = 1, trace.id, PlotFAS = FALSE))
  fc2 <- safe_method("Empirical", autofc(accts, dt, winout[[2]] - winout[[1]], dt,
      winout[[6]] - winout[[1]], mag = row$magnitude, Option = 2, trace.id, PlotFAS = FALSE))
  fc3 <- safe_method("Displacement-Ancheta", autofc(accts, dt, winout[[2]] - winout[[1]], dt,
      winout[[6]] - winout[[1]], mag = row$magnitude, Option = 3, trace.id, PlotFAS = FALSE))
  fc7 <- safe_method("Guo", autofc_option7_matlab(
      acc_ts = ts(number(accts) * 980.665, delta = dt), dt = dt,
      parr1 = number(row$parr1 - winout[[1]]), SStart = number(winout[[3]] - winout[[1]]),
      SEnd = number(winout[[4]] - winout[[1]]), mag = number(row$magnitude),
      component = row$component, matlab_exe = matlab_exe, opt7_dir = file.path(engine_dir, "Option 7")))
  hk <- safe_method("Hussein/Kishida", hk_predict_HK(accts, dt, winout[[2]] - winout[[1]]), list(fchp = NA_real_, fclp = NA_real_))
  fc8_hp <- hk$fchp; fc8_lp <- hk$fclp
  corners <- select_corners_app(
    c(fc1[[1]], fc2[[1]], fc3[[1]], fc7[[1]], fc8_hp),
    c(fc1[[2]], fc2[[2]], fc3[[2]], fc7[[2]], fc8_lp), fc8_hp)
  fchp <- corners$hp; fclp <- corners$lp
  row$corner_fallback <- corners$fallback
  if (!is.finite(fchp) || !is.finite(fclp) || fchp <= 0 || fclp <= fchp) stop("Failed or inverted corner selection")
  if (fchp >= 0.5 / dt || fclp >= 0.5 / dt) stop("Selected corner is outside Nyquist; sampling rate is incompatible with existing method bounds")
  SNRave <- na.omit(c(fc1[[3]], fc2[[3]], fc3[[3]], fc7[[3]]))
  snr_available <- length(SNRave) > 0L && is.finite(mean(SNRave))
  ts.filt <- Filter(accts, fchp, fclp, "A", TRUE)
  if (!all(vapply(ts.filt, function(a) length(a) == length(accts) && all(is.finite(a)), logical(1)))) stop("Filter returned invalid A/V/D arrays")
  row$fcHP <- fchp; row$fcLP <- fclp
  row$windowStart <- max(0, winout[[1]])
  row$windowEnd <- min(winout[[6]], dt * (npts - 1))
  row$PStart <- winout[[2]]; row$SStart <- winout[[3]]; row$SEnd <- winout[[4]]; row$CStart <- winout[[5]]
  row$SNRave <- if (snr_available) mean(SNRave) else NA_real_
  row$method_errors <- paste(errors, collapse = " | ")
  row$method_corners <- jsonlite::toJSON(list(SNR_Kishida = fc1, Empirical = fc2, Ancheta = fc3, Guo = fc7, HK = hk), auto_unbox = TRUE, digits = NA)
  # Same rejection thresholds and order as v11 (retain last reason in legacy field).
  reasons <- character()
  if (fclp / fchp < 10) reasons <- c(reasons, "too narrow bandwidth")
  noise_end <- max(1L, min(length(accts), round((winout[[2]] - winout[[1]]) / dt)))
  prepga <- max(abs(ts.filt[[1]][seq_len(noise_end)]))
  pga <- max(abs(ts.filt[[1]])); row$pga <- pga
  if (pga / prepga < 6) reasons <- c(reasons, "preevent pga is too high")
  if (pga / max(abs(accts)) < 0.8) reasons <- c(reasons, "pga/rawpga is too low")
  codastart <- round(((winout[[4]] + winout[[5]]) / 2 - winout[[1]]) / dt)
  codaend <- round((winout[[6]] - winout[[1]]) / dt)
  idx <- seq.int(codastart, codaend); idx <- idx[idx >= 1 & idx <= length(ts.filt[[1]])]
  if (length(idx) && max(abs(ts.filt[[1]][idx])) == pga) reasons <- c(reasons, "too high coda pga")
  if (!snr_available) reasons <- c(reasons, "SNR QC unavailable: no valid mean SNR")
  else if (mean(SNRave) < 12) reasons <- c(reasons, "SNR is too low")
  row$acrjfinal <- !length(reasons)
  row$rejectreason3filter <- if (length(reasons)) tail(reasons, 1) else "accepted"
  row$rejection_reasons <- paste(reasons, collapse = "; ")
  row$stage2_status <- if (length(reasons)) "rejected" else "completed"
  if (save_figures) {
    # Direct PNG devices work in Rscript on Windows and Linux without savePlot().
    avd <- figure_name(eventid, trace.id, "AVD")
    png(avd, width = 1500, height = 1000, res = 130)
    tryCatch({
      par(mfrow = c(3, 1), mar = c(3.5, 4.7, 2.3, 1.2), mgp = c(2.6, .7, 0))
      tt <- ((seq_along(accts)) - 1) * dt
      labels <- c("Acceleration (g)", "Velocity (cm/s)", "Displacement (cm)")
      for (panel in 1:3) {
        plot(tt, ts.filt[[panel]], type = "l", col = "#176c83", xlab = "Time from trimmed start (s)", ylab = labels[panel])
        grid(col = "#e8ecef"); abline(v = winout[[2]] - winout[[1]], col = "#c25830", lty = 2)
        if (panel == 1) title(main = paste(eventid, row$network, row$station, row$component,
            sprintf("| HP %.3g Hz / LP %.3g Hz | %s", fchp, fclp, row$stage2_status)), cex.main = .9)
      }
    }, finally = dev.off())
    row$figure_avd <- avd
    faspath <- figure_name(eventid, trace.id, "FAS")
    safe_method("FAS figure", {
      e.T <- 2^floor(logb(3276.80 / dt, 2)) * dt
      e.order <- logb(e.T / dt, 2) - floor(logb(length(accts), 2))
      all <- FAS.cal(accts, e.order)
      smooth <- SMOOTH(all[[2]], length(all[[2]]), 0, all[[3]], all[[3]][2], max(all[[3]]), -0.05)
      noise <- ts(acc_g[seq_len(max(1, min(npts, floor(winout[[2]] / dt))))], delta = dt)
      noise_ord <- max(1, logb(e.T / dt, 2) - floor(logb(length(noise), 2)))
      nf <- FAS.cal(noise, noise_ord)
      ns <- SMOOTH(nf[[2]], length(nf[[2]]), 0, nf[[3]], nf[[3]][2], max(nf[[3]]), -0.05)
      png(faspath, width = 1400, height = 850, res = 130)
      tryCatch({
        plot(smooth[-1, 2], smooth[-1, 1], type = "l", log = "xy", col = "#176c83",
             xlab = "Frequency (Hz)", ylab = "Smoothed FAS (g s)",
             main = paste(eventid, row$station, row$component, sprintf("| Mean SNR %.2f", mean(SNRave))))
        grid(col = "#e8ecef"); lines(ns[-1, 2], ns[-1, 1], col = "#a87744")
        colors <- c("#3a9156", "#416eb5", "#d85b48", "#ba5aa9", "#7047a8")
        candidates <- list(fc1, fc2, fc3, fc7, c(fc8_hp, fc8_lp))
        for (m in seq_along(candidates)) {
          values <- as.numeric(unlist(candidates[[m]][1:2]))
          values <- values[is.finite(values) & values > 0]
          if (length(values)) abline(v = values, col = colors[m], lty = 2, lwd = 1.6)
        }
        abline(v = c(fchp, fclp), col = "#172e41", lty = 3, lwd = 2.5)
        legend("bottomleft", c("Whole series", "Noise", "SNR-Kishida", "Empirical", "Ancheta", "Guo", "Hussein/Kishida", "Selected"),
               col = c("#176c83", "#a87744", colors, "#172e41"), lty = c(1, 1, rep(2, 5), 3), cex = .8, bty = "n")
      }, finally = dev.off())
      row$figure_fas <- faspath
    }, NULL)
  }
  io$write_processed(h5file, processed_h5, eventid, trace.id,
                     number(ts.filt[[1]]), number(ts.filt[[2]]), number(ts.filt[[3]]),
                     dt, as.integer(first - 1), jsonlite::toJSON(as.list(row[1, ]), auto_unbox = TRUE, na = "null", digits = NA), signature)
  row
}

emit("start", total = nrow(ts.info), already_completed = sum(ts.info$stage2_status %in% c("completed", "rejected")), output_hdf5 = processed_h5)
write_summary <- function(status) {
  summary <- list(stage = "filtering", status = status, input_hdf5 = h5file,
                  metadata_csv = outcsv, metadata_xlsx = outxlsx, processed_hdf5 = processed_h5,
                  total = nrow(ts.info), completed = sum(ts.info$stage2_status == "completed"),
                  rejected = sum(ts.info$stage2_status == "rejected"), errors = sum(ts.info$stage2_status == "error"),
                  pending = sum(ts.info$stage2_status == "pending"),
                  method_warnings = sum(nzchar(ts.info$method_errors)),
                  processing_signature = signature)
  jsonlite::write_json(summary, file.path(outfolder, "filtering_summary.json"), auto_unbox = TRUE, pretty = TRUE)
}
if (!nrow(ts.info)) {
  checkpoint()
  io$atomic_export(outcsv, outxlsx)
  write_summary("no_accepted_traces")
  emit("complete", total = 0, completed = 0, rejected = 0, errors = 0, message = "No Stage 1 accepted traces; no filtering was performed")
  quit(status = 0)
}
for (group in unique(ts.info$group_id)) {
  if (cancelled()) break
  indices <- which(ts.info$group_id == group)
  pending <- indices[!ts.info$stage2_status[indices] %in% c("completed", "rejected")]
  if (!length(pending)) next
  arrival <- refine_arrival(indices)
  ts.info$parr1[pending] <- arrival
  for (index in pending) {
    if (cancelled()) break
    emit("trace_started", current = index, total = nrow(ts.info), event_id = ts.info$event_id[index], trace_id = ts.info$trace_id[index])
    row <- tryCatch(process_trace(ts.info[index, , drop = FALSE]), error = function(e) {
      ts.info$stage2_status[index] <<- "error"
      ts.info$stage2_error[index] <<- conditionMessage(e)
      ts.info$acrjfinal[index] <<- FALSE
      ts.info$rejectreason3filter[index] <<- "processing error"
      message("Trace failed: ", ts.info$trace_id[index], ": ", conditionMessage(e))
      NULL
    })
    if (!is.null(row)) for (name in names(row)) ts.info[index, name] <- row[[name]]
    checkpoint()
    emit("trace_finished", current = index, total = nrow(ts.info), trace_id = ts.info$trace_id[index],
         status = ts.info$stage2_status[index], error = ts.info$stage2_error[index])
  }
}
checkpoint()
io$atomic_export(outcsv, outxlsx)
counts <- table(factor(ts.info$stage2_status, levels = c("completed", "rejected", "error", "pending")))
has_issues <- counts[3] > 0 || any(nzchar(ts.info$method_errors))
write_summary(if (cancelled()) "cancelled" else if (has_issues) "completed_with_issues" else "completed")
emit(if (cancelled()) "cancelled" else "complete", total = nrow(ts.info), completed = unname(counts[1]),
     rejected = unname(counts[2]), errors = unname(counts[3]), pending = unname(counts[4]), metadata = outxlsx,
     output_hdf5 = processed_h5)
if (cancelled()) quit(status = 130)
if (sum(counts[c(1, 2)]) == 0 && counts[3] > 0) quit(status = 2)
if (has_issues) quit(status = 3)
