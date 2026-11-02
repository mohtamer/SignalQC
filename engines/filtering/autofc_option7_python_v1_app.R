# Guo Option 7: Python translation of the existing MATLAB v2 driver.
# The main R script owns reticulate initialization and imports guo_engine.
# Input units, component mapping, driver and wrapper clamps remain unchanged.
autofc_option7_python <- function(acc_ts, dt, parr1, SStart, SEnd, mag,
                                   component, type = "A", baseTF = 0) {
  result <- guo_engine$pick_corners(as.numeric(acc_ts), as.numeric(dt),
      as.numeric(parr1), as.numeric(SStart), as.numeric(SEnd), as.numeric(mag),
      as.character(component), as.character(type), as.numeric(baseTF))
  list(as.numeric(result$fHP), as.numeric(result$fLP), as.numeric(result$meanSNR))
}
