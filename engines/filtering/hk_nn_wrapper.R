# Hussein/Kishida NN wrapper (matches hk_nn.py)
# Returns list(fchp, fclp) in Hz or NA if unsafe/invalid
hk_predict_HK <- function(accts, dt, pstart_rel_secs) {

  # --- FAS for the entire (trimmed) series ---
  e.T <- 2^floor(logb(3276.80/dt, 2)) * dt
  ord_all <- max(1, logb(e.T/dt, 2) - floor(logb(length(accts), 2)))
  tmp <- FAS.cal(accts, ord_all)
  freq_all  <- tmp[[3]]
  FAS_all   <- tmp[[2]]
  tmp <- SMOOTH(FAS_all, length(FAS_all), 0, freq_all, freq_all[2], max(freq_all), -0.05)
  FAS_all_sm   <- tmp[,1]
  freq_all_sm  <- tmp[,2]

  # --- FAS for the NOISE window: from start up to P (relative) ---
  nlen <- max(10, round(pstart_rel_secs / dt))  # enforce a minimum
  nlen <- min(nlen, length(accts))
  noise <- ts(data = as.numeric(accts)[seq_len(nlen)], delta = dt)

  ord_n <- max(1, logb(e.T/dt, 2) - floor(logb(length(noise), 2)))
  tmp <- FAS.cal(noise, ord_n)
  freq_n  <- tmp[[3]]
  FAS_n   <- tmp[[2]]
  tmp <- SMOOTH(FAS_n, length(FAS_n), 0, freq_n, freq_n[2], max(freq_n), -0.05)
  FAS_noise_sm  <- tmp[,1]
  freq_noise_sm <- tmp[,2]

  # --- Put noise spectrum on the SAME frequency grid as the full series (log-log) ---
  log_fn  <- log(freq_noise_sm)
  log_Sn  <- log(pmax(FAS_noise_sm, .Machine$double.eps))
  log_fa  <- log(freq_all_sm)
  log_Sn_on_all <- approx(log_fn, log_Sn, xout = log_fa, rule = 2)$y
  FASn_on_all <- exp(log_Sn_on_all)

  # --- Call Python model: hk_nn.predict_fc_from_arrays(freq, FAS_ave, FASn_ave) ---
  res <- tryCatch({
    hk_nn$predict_fc_from_arrays(freq_all_sm, FAS_all_sm, FASn_on_all)
  }, error = function(e) {
    message("HK NN call failed: ", conditionMessage(e))
    NULL
  })

  if (is.null(res)) return(list(fchp = NA_real_, fclp = NA_real_))

  hp <- suppressWarnings(as.numeric(res$fcHP))
  lp <- suppressWarnings(as.numeric(res$fcLP))

  # --- sanity checks vs Nyquist & ordering ---
  nyq <- 0.5 / dt
  if (!is.finite(hp) || hp <= 0 || hp >= nyq) hp <- NA_real_
  if (!is.finite(lp) || lp <= 0 || lp >= nyq) lp <- NA_real_
  if (is.finite(hp) && is.finite(lp) && lp <= hp) {
    # keep ordering sensible; if inverted, soften HP
    hp <- max(NA_real_, lp / 12)
  }

  list(fchp = hp, fclp = lp)
}
