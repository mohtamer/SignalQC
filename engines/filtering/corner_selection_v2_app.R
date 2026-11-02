# User-selectable bounds; default valid-pick aggregation is unchanged from v1.
filtering_method_ids_app <- c("snr_kishida", "empirical", "ancheta", "guo", "hk")

validate_corner_bounds_app <- function(hp_fallback_hz, lp_min_hz) {
  valid <- function(value) is.numeric(value) && length(value) == 1L && !is.na(value) && is.finite(value) && value > 0
  if (!valid(hp_fallback_hz) || !valid(lp_min_hz) || hp_fallback_hz >= lp_min_hz)
    stop("Corner defaults must be finite positive frequencies with high-pass fallback below low-pass minimum/fallback")
  invisible(TRUE)
}

filtering_settings_app <- function(cfg) {
  methods <- if ("filtering_methods" %in% names(cfg)) cfg$filtering_methods else filtering_method_ids_app
  if (!is.character(methods) || !length(methods) || anyNA(methods) || anyDuplicated(methods) ||
      !all(methods %in% filtering_method_ids_app)) stop("Select at least one known corner-frequency method, without duplicates")
  hp <- if ("filtering_hp_fallback_hz" %in% names(cfg)) cfg$filtering_hp_fallback_hz else 0.1
  lp <- if ("filtering_lp_min_hz" %in% names(cfg)) cfg$filtering_lp_min_hz else 8.0
  validate_corner_bounds_app(hp, lp)
  list(filtering_methods=filtering_method_ids_app[filtering_method_ids_app %in% methods],
       filtering_hp_fallback_hz=as.numeric(hp), filtering_lp_min_hz=as.numeric(lp))
}

select_corners_app <- function(hp, lp, hk_hp = NA_real_, hp_fallback_hz = 0.1, lp_min_hz = 8.0) {
  validate_corner_bounds_app(hp_fallback_hz, lp_min_hz)
  hp <- hp[is.finite(hp) & hp > 0]
  lp <- lp[is.finite(lp) & lp > 0]
  fallback <- character()
  if (!length(hp)) {
    fchp <- hp_fallback_hz
    fallback <- c(fallback, paste0("HP=", hp_fallback_hz, " Hz: no valid high-pass candidates"))
  } else {
    fchp <- stats::median(hp)
    if (is.finite(hk_hp) && hk_hp > 0) fchp <- max(fchp, hk_hp)
  }
  if (!length(lp)) {
    fclp <- lp_min_hz
    fallback <- c(fallback, paste0("LP=", lp_min_hz, " Hz: no valid low-pass candidates"))
  } else {
    fclp <- max(lp_min_hz, sqrt(stats::median(lp) * min(lp)))
  }
  list(hp = fchp, lp = fclp, fallback = paste(fallback, collapse = "; "))
}
