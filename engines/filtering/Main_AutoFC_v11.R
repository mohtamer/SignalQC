rm(list=ls(all=TRUE))
setwd("D:/KU/PhD/PhD Project/NCM_Test_Data/autofilter_NCM")

library(reticulate)
library(zoo)
library(RCTC)
library("rhdf5")
library("openxlsx")

use_python('C:/Users/midot/AppData/Local/Programs/Python/Python312/python.exe')

source("autofc.R")
source("autoparr.R")
source("Baseline.r")
source("Butterworth.r")
source("Bworth.r")
source("DC.mean.r")
source("flexibleReader.R")
source("fbutter.r")
source("file.select.R")
source("FAS.r")
source("Filter.r")
source("IM20.R")
source("Integrate2VD.r")
source("Konno_Omachi.R")
source("Preferred_fc.R")
source("PSpicker.R")
source("Taper.r")
source("windows.r")
source("SMOOTH.r")
source("testDISP_2.R")
source("testDISP.R")
source("autofc_option7_matlab.R")
source("hk_nn_wrapper.R")

source_python("hk_nn.py")
hk_nn <- reticulate::import("hk_nn", convert = TRUE)

source_python('hdf5_reader_v8.py')
source_python('getATS.py')
source_python('hk_nn.py')

reticulate::py_run_string("
 import hk_nn as _hk
 def hk_predict(indict):
     out = {}
     _hk.score(indict, out)
     return out.get('Predicted fcHPmanual', None), out.get('Predicted fcLPmanual', None)
")

.log_interp_val <- function(freq, fas, f) {
  fas <- pmax(as.numeric(fas), .Machine$double.eps)
  freq <- as.numeric(freq)
  y <- approx(x = log(freq), y = log(fas), xout = log(f), rule = 2, ties = mean)$y
  as.numeric(exp(y))
}

.hk_anchor <- c(0.0018, 0.0056, 0.0178, 0.0562, 0.1778, 0.5623, 1.7783, 5.6234, 17.7828)

.build_hk_indata <- function(freq_all, FAS_all, freq_noise, FAS_noise) {
  F_all   <- sapply(.hk_anchor, function(ff) .log_interp_val(freq_all,  FAS_all,  ff))
  F_noise <- sapply(.hk_anchor, function(ff) .log_interp_val(freq_noise, FAS_noise, ff))
  rat <- log(F_all / F_noise)
  names(rat) <- c(
    "ln_ratio_FASave_over_FASnave_0.0018",
    "ln_ratio_FASave_over_FASnave_0.0056",
    "ln_ratio_FASave_over_FASnave_0.0178",
    "ln_ratio_FASave_over_FASnave_0.0562",
    "ln_ratio_FASave_over_FASnave_0.1778",
    "ln_ratio_FASave_over_FASnave_0.5623",
    "ln_ratio_FASave_over_FASnave_1.7783",
    "ln_ratio_FASave_over_FASnave_17.7828",
    "ln_ratio_FASave_over_FASnave_5.6234"
  )
  as.list(rat)
}

safe_h5_to_g <- function(h5file, eventid, station, component, trace.id) {
  out <- try(hdf5_acceleration(h5file, eventid, station, component, trace.id), silent = TRUE)
  if (inherits(out, "try-error")) {
    message(sprintf("Skipping component: HDF5 read failed for %s-%s trace '%s' (event '%s')",
                    station, component, trace.id, eventid))
    return(NULL)
  }
  out / 9.80665
}

.find_row_idx <- function(df, eventid, station, component, starttime.s) {
  which(df$event_id == eventid &
          df$station  == station &
          df$component == component &
          df$starttime.s == starttime.s)
}

.row_key <- function(event_id, station, component, starttime_s) {
  paste(event_id, station, component, starttime_s, sep = "||")
}

.is_bad_trace <- function(x, min_n = 64) {
  x <- as.numeric(x)
  if (length(x) < min_n) return(TRUE)
  if (all(!is.finite(x))) return(TRUE)
  xf <- x[is.finite(x)]
  if (length(xf) < min_n) return(TRUE)
  if (max(abs(xf), na.rm = TRUE) == 0) return(TRUE)
  if (sd(xf, na.rm = TRUE) == 0) return(TRUE)
  FALSE
}

.safe_plot_ts <- function(x, dt, main = NULL) {
  xx <- as.numeric(x)
  if (.is_bad_trace(xx, min_n = 10)) {
    message("Skipping plot: trace has non-finite/flat/too-short data.")
    return(FALSE)
  }
  if (sum(is.finite(xx)) < 2) {
    message("Skipping plot: not enough finite points.")
    return(FALSE)
  }
  plot(dt * (1:length(x)), x, type = "l", main = main)
  TRUE
}

.safe_call <- function(expr, default) {
  tryCatch(expr, error = function(e) default)
}

# ---------- main ----------
refpath  = "D:/KU/PhD/PhD Project/NCM Data/1"

all_meta_paths <- list.files(
  path = refpath,
  pattern = "h5meta(_updated)?\\.xlsx$",
  recursive = TRUE,
  full.names = FALSE,
  ignore.case = TRUE
)

all_meta_paths <- all_meta_paths[!grepl("h5filmeta", basename(all_meta_paths), ignore.case = TRUE)]

folds <- unique(dirname(all_meta_paths))
folds <- folds[1]

filetype = ".h5"

for (k in 1:length(folds)) {

  baipa    = paste(refpath, folds[k], sep = "/")
  h5files0 = list.files(path = paste(refpath, "/", folds[k], sep = ""), pattern = filetype, full.names = TRUE)

  metafiles = basename(list.files(
    path = paste(refpath, "/", folds[k], sep = ""),
    pattern = "h5meta(_updated)?\\.xlsx$",
    full.names = TRUE,
    ignore.case = TRUE
  ))

  metafiles = metafiles[!grepl("h5filmeta", metafiles, ignore.case = TRUE)]

  h5files  = list.files(
    path = paste(refpath, "/", folds[k], sep = ""),
    pattern = paste0("\\", filetype, "$"),
    recursive = FALSE,
    full.names = TRUE
  )

  nh5files = length(h5files)

  for (kk in 1:nh5files) {

    h5file = h5files[kk]

    .key_from_name <- function(x) {
      x <- basename(x)
      x <- sub("\\.h5meta_updated\\.xlsx$", ".h5", x, ignore.case = TRUE)
      x <- sub("\\.h5meta\\.xlsx$", ".h5", x, ignore.case = TRUE)
      x <- sub("_(0*)([0-9]+)\\.h5$", "_\\2.h5", x, perl = TRUE)
      x
    }

    h5_key    <- .key_from_name(h5file)
    meta_keys <- .key_from_name(metafiles)

    candidate_meta <- metafiles[meta_keys == h5_key]

    if (length(candidate_meta) == 0) {
      message(sprintf("No h5meta file found for %s. Skipping.", basename(h5file)))
      next
    }

    candidate_priority <- ifelse(
      grepl("\\.h5meta_updated\\.xlsx$", candidate_meta, ignore.case = TRUE),
      1,
      2
    )

    metafile <- candidate_meta[order(candidate_priority)][1]
    message(sprintf("Using metadata file for %s: %s", basename(h5file), metafile))

    ts.info = read.xlsx(paste(refpath, "/", folds[k], "/", metafile, sep = ""))

    ts.info$fcHP = -999; ts.info$fcLP = -999
    ts.info$windowStart = -999
    ts.info$windowEnd   = -999
    ts.info$PStart = -999; ts.info$SStart = -999; ts.info$SEnd = -999; ts.info$CStart = -999
    ts.info$pga = -999; ts.info$parr1 = -999
    ts.info$trace.id <- NA
    ts.info$rejectreason3filter = "accepted"
    ts.info = unique(ts.info)
    ts.info$use = TRUE
    ts.info = subset(ts.info, ts.info$meta.acrjfinal3.acfilename. == TRUE)
    names(ts.info) = gsub(".acfilename.", "", gsub("meta.", "", names(ts.info)))

    outfolder = paste(baipa, sep = "")
    outxlsx   = paste(outfolder, "/", basename(h5file), "filmeta.xlsx", sep = "")

    if (file.exists(outxlsx)) {
      prev <- tryCatch(read.xlsx(outxlsx), error = function(e) NULL)
      if (!is.null(prev) && nrow(prev) > 0) {
        ts.info$key__ <- .row_key(ts.info$event_id, ts.info$station, ts.info$component, ts.info$starttime.s)
        prev$key__    <- .row_key(prev$event_id,    prev$station,    prev$component,    prev$starttime.s)

        cols_to_take <- intersect(names(prev), c("fcHP","fcLP","windowStart","windowEnd","PStart","SStart","SEnd","CStart","pga","parr1","trace.id","rejectreason3filter","acrjfinal"))
        m <- match(ts.info$key__, prev$key__)
        for (cc in cols_to_take) {
          ts.info[[cc]][!is.na(m)] <- suppressWarnings(as.numeric(prev[[cc]][m[!is.na(m)]]))
          if (cc %in% c("trace.id","rejectreason3filter")) {
            ts.info[[cc]][!is.na(m)] <- as.character(prev[[cc]][m[!is.na(m)]])
          }
          if (cc %in% c("acrjfinal")) {
            ts.info[[cc]][!is.na(m)] <- prev[[cc]][m[!is.na(m)]]
          }
        }
        ts.info$key__ <- NULL
      }
    } else {
      ts.info.out0 <- apply(ts.info, 2, as.character)
      write.xlsx(ts.info.out0, outxlsx, row.names = TRUE, col.names = TRUE)
    }

    events      = unique(ts.info$event_id)
    nevents     = length(events)
    events.meta = hdf5_eventlist(h5file)

    for (iii in 1:nevents) {
      for (kkk in 1:length(events.meta)) {
        if (events.meta[[kkk]]$event_id == events[iii]) break
      }

      event.meta = as.character(events.meta[[kkk]])
      eqlat  = as.numeric(events.meta[[kkk]]$latitude)
      eqlon  = as.numeric(events.meta[[kkk]]$longitude)
      eqdep  = as.numeric(events.meta[[kkk]]$depth)
      eqtime = events.meta[[kkk]]$origin_time
      eqtime = strptime(eqtime, "%Y-%m-%d %H:%M:%OS")
      eventid = events.meta[[kkk]]$event_id

      outfilepath = paste(baipa, "/", basename(h5file), "meta.2.xlsx", sep = "")
      meta = list()
      baipa = paste(refpath, "/", folds[k], sep = "")
      ts.info.event = subset(ts.info, ts.info$event_id == eventid)
      nfiles = nrow(ts.info.event)

      if (nfiles >= 3) {
        dir.create(paste(refpath, "/", folds[k], "/Figure/AVD", sep = ""), showWarnings = FALSE, recursive = TRUE)
        dir.create(paste(refpath, "/", folds[k], "/Figure/FAS", sep = ""), showWarnings = FALSE, recursive = TRUE)
      }

      nrec = file.select(ts.info.event, io = 0)

      if (nrec > 0) {

        for (i in 1:nrec) {
          acfilename = file.select(ts.info.event, io = 1, K = i)
          print(paste(i, " over ", nrec, sep = ""))

          parr.e = vector(mode = 'numeric', length = (3 * 3))
          ts.info.es = subset(ts.info.event, ts.info.event$rec_id == acfilename)
          hypd = ts.info.es$hypdis[1]
          parr1 <- NA_real_

          if (abs(min(ts.info.es$count) - max(ts.info.es$count)) < 10) {
            par(mfrow = c(3, 1), pty = "m", mar = c(4, 4, 3, 2))

            for (j in 1:3) {
              ts.info.e = subset(ts.info.event, ts.info.event$rec_id == acfilename[j])
              ts.info.e = unique(ts.info.e)[1,]
              parr.e0 = ts.info.e$parr
              dt   = as.numeric(ts.info.e$timeStep)
              npts = as.numeric(ts.info.e$count)
              station   = ts.info.e$station
              component = ts.info.e$component
              network   = ts.info.e$network
              trace.id  = paste0(ts.info.e$station, "_", ts.info.e$component, "_",
                                  gsub("-", "", gsub(":", "", gsub(" ", "T", ts.info.e$starttime.s))))

              acc_g <- safe_h5_to_g(h5file, eventid, station, component, trace.id)
              if (is.null(acc_g)) next

              accts <- ts(data = acc_g, delta = dt)
              temp  <- Filter(accts, 1, 20, "C", TRUE)
              accts <- temp[[2]]

              .safe_plot_ts(accts, dt)
              abline(v = parr.e0, col = 1, lty = 2)

              parr.e[3 + (j - 1) * 3] = parr.e0
              nstart = max(1, round((parr.e0 - 50) / dt))
              nend   = min(npts, round((parr.e0 + 50) / dt))
              accts0 = ts(accts[nstart:nend], delta = dt)

              parr.out = autoparr(accts0, Option = 1) + (parr.e0 - 50)
              abline(v = parr.out, col = 2, lty = 2)
              parr.e[1 + (j - 1) * 3] = parr.out

              parr.out = autoparr(accts0, Option = 2) + (parr.e0 - 50)
              abline(v = parr.out, col = 6)
              parr.e[2 + (j - 1) * 3] = parr.out

              idx_store <- .find_row_idx(ts.info, eventid, station, component, ts.info.e$starttime.s)
              if (length(idx_store) == 1) {
                ts.info$trace.id[idx_store] <- trace.id
              }
            }

            par(mfrow = c(3, 1), pty = "m", mar = c(4, 4, 3, 2))

            for (j in 1:3) {
              ts.info.e = subset(ts.info.event, ts.info.event$rec_id == acfilename[j])
              ts.info.e = unique(ts.info.e)[1,]
              dt   = as.numeric(ts.info.e$timeStep)
              npts = as.numeric(ts.info.e$count)
              station   = ts.info.e$station
              component = ts.info.e$component
              network   = ts.info.e$network
              trace.id  = paste0(ts.info.e$station, "_", ts.info.e$component, "_",
                                  gsub("-", "", gsub(":", "", gsub(" ", "T", ts.info.e$starttime.s))))

              acc_g <- safe_h5_to_g(h5file, eventid, station, component, trace.id)
              if (is.null(acc_g)) next

              accts <- ts(data = acc_g, delta = dt)
              .safe_plot_ts(accts, dt, main = paste(eventid, "/", trace.id))
              parr1 = max(4.99, median((parr.e[parr.e > 0]))) - 3
              abline(v = parr1, col = 3)
            }
          }

          if (!is.finite(parr1)) {
            base_parr <- suppressWarnings(as.numeric(ts.info.es$parr[1]))
            parr1 <- if (is.finite(base_parr)) max(2, base_parr - 3) else 5
          }

          ts.info[ts.info$rec_id == acfilename & ts.info$event_id == eventid,]$parr1 = parr1
          ts.info[ts.info$rec_id == acfilename & ts.info$event_id == eventid,]$parr1 = parr1

          ts.info.out_i <- apply(ts.info, 2, as.character)
          write.xlsx(ts.info.out_i, outxlsx, row.names = TRUE, col.names = TRUE)
        }

        ts.info.event = subset(ts.info, ts.info$event_id == eventid)

        for (i in 1:nrec) {
          acfilename = file.select(ts.info.event, io = 1, K = i)

          for (j in 1:3) {
            ts.info.e = subset(ts.info.event, ts.info.event$rec_id == acfilename[j])
            ts.info.e = unique(ts.info.e)[1,]
            dt   = as.numeric(ts.info.e$timeStep)
            npts = as.numeric(ts.info.e$count)
            station   = ts.info.e$station
            component = ts.info.e$component
            network   = ts.info.e$network
            trace.id  = paste0(ts.info.e$station, "_", ts.info.e$component, "_",
                                gsub("-", "", gsub(":", "", gsub(" ", "T", ts.info.e$starttime.s))))

            par(mfrow = c(1, 1), pty = "m", mar = c(4, 4, 3, 2))

            idx_write0 <- .find_row_idx(ts.info, eventid, station, component, ts.info.e$starttime.s)
            if (length(idx_write0) == 1) {
              if (is.finite(as.numeric(ts.info$fcHP[idx_write0])) &&
                  is.finite(as.numeric(ts.info$fcLP[idx_write0])) &&
                  as.numeric(ts.info$fcHP[idx_write0]) != -999 &&
                  as.numeric(ts.info$fcLP[idx_write0]) != -999) {
                message(sprintf("Already processed -> skipping %s / %s", eventid, trace.id))
                next
              }
            }

            acc_g <- safe_h5_to_g(h5file, eventid, station, component, trace.id)
            if (is.null(acc_g)) next

            accts <- ts(data = acc_g, delta = dt)

            winout = windows(as.numeric(ts.info.e$parr1),
                             as.numeric(ts.info.e$hypdis),
                             as.numeric(ts.info.e$magnitude),
                             as.numeric(dt),
                             as.numeric(npts))

            accts <- ts(
              data  = acc_g[round(winout[[1]]/dt):(min(round(winout[[6]]/dt), (npts - 1)))],
              delta = dt
            )

            if (.is_bad_trace(accts)) {
              message(sprintf("Bad trace (NaN/Inf/flat/short) -> marking -999 and skipping %s / %s", eventid, trace.id))
              idx_write <- .find_row_idx(ts.info, eventid, station, component, ts.info.e$starttime.s)
              if (length(idx_write) == 1) {
                ts.info$trace.id[idx_write] = trace.id
                ts.info$fcHP[idx_write] = -999
                ts.info$fcLP[idx_write] = -999
                ts.info$rejectreason3filter[idx_write] = "bad trace (NaN/Inf/flat/short)"
                ts.info.out_bad <- apply(ts.info, 2, as.character)
                write.xlsx(ts.info.out_bad, outxlsx, row.names = TRUE, col.names = TRUE)
              }
              next
            }

            .safe_plot_ts(accts, dt)

            fc1 <- .safe_call(
              autofc(accts, dt, winout[[2]] - winout[[1]], dt, winout[[6]] - winout[[1]],
                     mag = ts.info.e$magnitude, Option = 1, paste(basename(datapath), fileout, sep = "/"), PlotFAS = FALSE),
              list(NA_real_, NA_real_, NA_real_)
            )

            fc2 <- .safe_call(
              autofc(accts, dt, winout[[2]] - winout[[1]], dt, winout[[6]] - winout[[1]],
                     mag = ts.info.e$magnitude, Option = 2, paste(basename(datapath), fileout, sep = "/"), PlotFAS = FALSE),
              list(NA_real_, NA_real_, NA_real_)
            )

            fc3 <- .safe_call(
              autofc(accts, dt, winout[[2]] - winout[[1]], dt, winout[[6]] - winout[[1]],
                     mag = ts.info.e$magnitude, Option = 3, paste(basename(datapath), fileout, sep = "/"), PlotFAS = FALSE),
              list(NA_real_, NA_real_, NA_real_)
            )

            fc7 <- .safe_call(
              autofc_option7_matlab(
                acc_ts     = ts(data = as.numeric(accts) * 980.665, delta = dt),
                dt         = dt,
                parr1      = as.numeric(ts.info.e$parr1 - winout[[1]]),
                SStart     = as.numeric(winout[[3]] - winout[[1]]),
                SEnd       = as.numeric(winout[[4]] - winout[[1]]),
                mag        = as.numeric(ts.info.e$magnitude),
                component  = ts.info.e$component,
                matlab_exe = "C:/Program Files/MATLAB/R2023a/bin/matlab.exe",
                opt7_dir   = "D:/KU/PhD/PhD Project/NCM_Test_Data/autofilter_NCM/Option 7"
              ),
              list(NA_real_, NA_real_, NA_real_)
            )

            e.T     <- 2^floor(logb(3276.80/dt, 2)) * dt
            e.order <- logb(e.T/dt, 2) - floor(logb(length(accts), 2))

            temp_all <- FAS.cal(accts, e.order)
            freq_all <- temp_all[[3]]
            fas_all  <- temp_all[[2]]
            temp_all <- SMOOTH(fas_all, length(fas_all), 0, freq_all, freq_all[2], max(freq_all), -0.05)
            FAS_all  <- temp_all[,1]; FREQ_all <- temp_all[,2]

            acctsn    <- ts(data = acc_g[1:(winout[[2]]/dt)], delta = dt)
            e.order_n <- max(1, logb(e.T/dt, 2) - floor(logb(length(acctsn), 2)))
            temp_n    <- FAS.cal(ts(data = acctsn, delta = dt), e.order_n)
            freq_n    <- temp_n[[3]]
            fas_n     <- temp_n[[2]]
            temp_n    <- SMOOTH(fas_n, length(fas_n), 0, freq_n, freq_n[2], max(freq_n), -0.05)
            FAS_noise <- temp_n[,1]; FREQ_noise <- temp_n[,2]

            hk <- .safe_call(
              hk_predict_HK(accts, dt, pstart_rel_secs = winout[[2]] - winout[[1]]),
              list(fchp = NA_real_, fclp = NA_real_)
            )

            fc8_hp <- hk$fchp
            fc8_lp <- hk$fclp

            message(sprintf("HK: fchp=%s, fclp=%s",
                            ifelse(is.na(fc8_hp), "NA", format(fc8_hp, digits = 4)),
                            ifelse(is.na(fc8_lp), "NA", format(fc8_lp, digits = 4))))

            hp_candidates <- na.omit(c(fc1[[1]], fc2[[1]], fc3[[1]], fc7[[1]], fc8_hp))
            lp_candidates <- na.omit(c(fc1[[2]], fc2[[2]], fc3[[2]], fc7[[2]], fc8_lp))

            if (length(hp_candidates) == 0 || length(lp_candidates) == 0) {
              fchp <- -999
              fclp <- -999
            } else {
              hp_median_all <- stats::median(hp_candidates)
              fchp <- if (is.finite(fc8_hp)) max(hp_median_all, fc8_hp) else hp_median_all
              fclp <- max(8, sqrt(stats::median(lp_candidates) * min(lp_candidates)))
              if (!is.finite(fchp)) fchp <- -999
              if (!is.finite(fclp)) fclp <- -999
            }

            SNRave = na.omit(c(fc1[[3]], fc2[[3]], fc3[[3]], fc7[[3]]))
            print("Average SNR"); print(SNRave)

            period.dat = read.table("period_NGA.dat")
            npr = period.dat[1,1]; pr = period.dat[2:nrow(period.dat),1]
            tmp <- PS_cal_cpp(data = accts, period_t = pr, damping = 0.05, time_dt = dt, type_return = 1)
            rsp <- data.frame(period = pr, t(matrix(tmp, ncol = npr, nrow = 2)))
            names(rsp) <- c("Period", "aa", "psa")
            rsp$psv = rsp$psa / (2*pi/pr); rsp$sd = rsp$psv / (2*pi/pr)

            plot(pr, rsp$psa, log = 'xy', typ = "l")
            if (is.finite(fchp) && fchp > 0) {
              Trev = 1/(1.25*fchp); abline(v = Trev, col = 3)
            }

            par(mfrow = c(1, 1), pty = "m", mar = c(4, 4, 1, 2))

            yv <- fas_all[-1]
            yv <- yv[is.finite(yv)]

            if (length(yv) < 2) {
              message("Skipping FAS plot: not enough finite points.")
            } else {
              plot(FREQ_all[-1], FAS_all[-1], type = "l", log = 'xy', ylim = range(yv))
              title(paste(eventid, "/", trace.id, ", Mean SNR=", round(mean(SNRave, na.rm = TRUE), 2), sep = ""))

              if (length(FREQ_noise) > 1 && length(FAS_noise) > 1) lines(FREQ_noise[-1], FAS_noise[-1], col = 2)
              if (is.finite(fc1[[1]])) abline(v = fc1[1], col = 3, lwd = 2, lty = 2)
              if (is.finite(fc1[[2]])) abline(v = fc1[2], col = 3, lwd = 2, lty = 2)
              if (is.finite(fc2[[1]])) abline(v = fc2[1], col = 4, lwd = 2, lty = 2)
              if (is.finite(fc2[[2]])) abline(v = fc2[2], col = 4, lwd = 2, lty = 2)
              if (is.finite(fc3[[1]])) abline(v = fc3[1], col = 5, lwd = 2, lty = 2)
              if (is.finite(fc3[[2]])) abline(v = fc3[2], col = 5, lwd = 2, lty = 2)
              if (is.finite(fc7[[1]])) abline(v = fc7[1], col = 6, lwd = 2, lty = 2)
              if (is.finite(fc7[[2]])) abline(v = fc7[2], col = 6, lwd = 2, lty = 2)

              if (is.finite(fc8_hp) && fc8_hp > 0) abline(v = fc8_hp, col = "#7B1FA2", lwd = 2, lty = 2)
              if (is.finite(fc8_lp) && fc8_lp > 0) abline(v = fc8_lp, col = "#7B1FA2", lwd = 2, lty = 2)
              if (is.finite(fchp) && is.finite(fclp) && fchp > 0 && fclp > 0) abline(v = c(fchp, fclp), col = 1, lwd = 2, lty = 3)

              legend("bottomleft",
                     c("SNR-Kishida", "Empirical", "Displacement-Ancheta", "Guo", "Hussein/Kishida", "Selected", "USGS"),
                     lty = c(2, 2, 2, 2, 2, 3, 2), col = c(3, 4, 5, 6, 8, 1, 7), lwd = 2)

              savePlot(paste(baipa, "/Figure/FAS/", eventid, "_", trace.id, ".png", sep = ""), typ = "png")
            }

            if (!is.finite(fchp) || !is.finite(fclp) || fchp == -999 || fclp == -999 || fchp <= 0 || fclp <= 0) {
              idx_write <- .find_row_idx(ts.info, eventid, station, component, ts.info.e$starttime.s)
              if (length(idx_write) == 1) {
                ts.info$trace.id[idx_write] = trace.id
                ts.info$fcHP[idx_write] = -999
                ts.info$fcLP[idx_write] = -999
                ts.info$rejectreason3filter[idx_write] = "failed corner selection (NA/NaN)"
                ts.info.out_fail <- apply(ts.info, 2, as.character)
                write.xlsx(ts.info.out_fail, outxlsx, row.names = TRUE, col.names = TRUE)
              }
              next
            }

            ts.filt = Filter(accts, fchp, fclp, "A", TRUE)

            par(mfrow = c(3, 1), pty = "m", mar = c(4, 4, 1, 2))

            plot(((1:length(accts)) - 1) * dt, ts.filt[[1]], type = "l", xlab = "Time (s)", ylab = "Acc (g)")
            title(paste(folds[k], eventid, trace.id, sep = "/"))
            abline(v = winout[[2]] - winout[[1]], col = 2, lwd = 2, lty = 2)

            plot(((1:length(accts)) - 1) * dt, ts.filt[[2]], type = "l", xlab = "Time (s)", ylab = "Vel (cm/s)")
            abline(v = winout[[2]] - winout[[1]], col = 2, lwd = 2, lty = 2)

            plot(((1:length(accts)) - 1) * dt, ts.filt[[3]], type = "l", xlab = "Time (s)", ylab = "Dis (cm)")
            abline(v = winout[[2]] - winout[[1]], col = 2, lwd = 2, lty = 2)

            idx_write <- .find_row_idx(ts.info, eventid, station, component, ts.info.e$starttime.s)

            if (length(idx_write) == 1) {
              ts.info$trace.id[idx_write] = trace.id
              ts.info$fcHP[idx_write] = fchp
              ts.info$fcLP[idx_write] = fclp

              ts.info$windowStart[idx_write] = max(0, winout[[1]])
              ts.info$windowEnd[idx_write]   = min(winout[[6]], dt * (npts - 1))

              ts.info$PStart[idx_write] = winout[[2]]
              ts.info$SStart[idx_write] = winout[[3]]
              ts.info$SEnd[idx_write]   = winout[[4]]
              ts.info$CStart[idx_write] = winout[[5]]

              if ((fclp / fchp) < 10) {
                ts.info$acrjfinal[idx_write] = FALSE
                text(dt * length(accts) / 2, 0, "Reject narrow bandwidth", col = 2, cex = 3)
                ts.info$rejectreason3filter[idx_write] = "too narrow bandwidth"
              }

              prepga = max(abs(ts.filt[[1]][1:round((winout[[2]] - winout[[1]]) / dt)]))
              pga    = max(abs(ts.filt[[1]]))
              ts.info$pga[idx_write] = pga

              if (pga / prepga < 6) {
                ts.info$acrjfinal[idx_write] = FALSE
                text(dt * length(accts) / 2, 0, "Reject Preevent PGA High", col = 2, cex = 3)
                ts.info$rejectreason3filter[idx_write] = "preevent pga is too high"
              }

              rawpga = max(abs(accts))
              if (pga / rawpga < 0.8) {
                ts.info$acrjfinal[idx_write] = FALSE
                text(dt * length(accts) / 2, 0, "Reject PGA/rawPGA is low", col = 2, cex = 3)
                ts.info$rejectreason3filter[idx_write] = "pga/rawpga is too low"
              }

              codastart <- round(((winout[[4]] + winout[[5]]) / 2 - winout[[1]]) / dt)
              codaend   <- round((winout[[6]] - winout[[1]]) / dt)

              idx <- seq.int(codastart, codaend)
              idx <- idx[idx >= 1 & idx <= length(ts.filt[[1]])]

              if (length(idx) > 0) {
                codapga <- max(abs(ts.filt[[1]][idx]), na.rm = TRUE)
              } else {
                codapga <- NA_real_
              }

              if (!is.na(codapga) && codapga == pga) {
                ts.info$acrjfinal[idx_write] = FALSE
                text(dt * length(accts) / 2, 0, "Reject PGA in coda", col = 2, cex = 3)
                ts.info$rejectreason3filter[idx_write] = "too high coda pga"
              }

              if (mean(na.omit(SNRave)) < 12) {
                ts.info$acrjfinal[idx_write] = FALSE
                text(dt * length(accts) / 2, 0, "Reject SNR is low", col = 2, cex = 3)
                ts.info$rejectreason3filter[idx_write] = "SNR is too low"
              }

              ts.info.out_step <- apply(ts.info, 2, as.character)
              write.xlsx(ts.info.out_step, outxlsx, row.names = TRUE, col.names = TRUE)
            } else {
              message(sprintf("No meta row for event_id '%s', station '%s', component '%s' (start '%s') — skipping meta writes",
                              eventid, station, component, as.character(ts.info.e$starttime.s)))
            }

            savePlot(paste(baipa, "/Figure/AVD/", eventid, "_", trace.id, ".png", sep = ""), typ = "png")
          }
        }
      }
    }

    ts.info.out = apply(ts.info, 2, as.character)
    write.xlsx(ts.info.out, outxlsx, row.names = TRUE, col.names = TRUE)
  }

  if (length(dev.list()) > 1) {
    for (i in dev.list()[2]:dev.list()[round(length(dev.list())/1)]) dev.off()
  }
}