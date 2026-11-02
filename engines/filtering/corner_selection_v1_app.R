# Preserve valid-pick aggregation. Defaults apply independently to empty valid sets.
select_corners_app <- function(hp, lp, hk_hp = NA_real_) {
  hp <- hp[is.finite(hp) & hp > 0]
  lp <- lp[is.finite(lp) & lp > 0]
  fallback <- character()
  if (!length(hp)) {
    fchp <- 0.1
    fallback <- c(fallback, "HP=0.1 Hz: no valid high-pass candidates")
  } else {
    fchp <- stats::median(hp)
    if (is.finite(hk_hp) && hk_hp > 0) fchp <- max(fchp, hk_hp)
  }
  if (!length(lp)) {
    fclp <- 8
    fallback <- c(fallback, "LP=8 Hz: no valid low-pass candidates")
  } else {
    fclp <- max(8, sqrt(stats::median(lp) * min(lp)))
  }
  list(hp = fchp, lp = fclp, fallback = paste(fallback, collapse = "; "))
}
