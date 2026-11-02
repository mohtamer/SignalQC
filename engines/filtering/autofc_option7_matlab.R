
library(R.matlab)

# Map your component naming to direction (1=EW, 2=NS, 3=UD)
.map_component_to_dir <- function(component) {
  comp <- toupper(as.character(component))
  last <- substring(comp, nchar(comp))    # use last char (E/N/Z typical)
  if (last == "E" || grepl("EW$", comp)) return(1)
  if (last == "N" || grepl("NS$", comp)) return(2)
  if (last == "Z" || grepl("UP$|UD$", comp)) return(3)
  return(1) # default to EW if unknown
}

# Wrapper to call MATLAB Option 7
autofc_option7_matlab <- function(acc_ts, dt, parr1, SStart, SEnd, mag,
                                  component,
                                  matlab_exe = "C:/Program Files/MATLAB/R2023a/bin/matlab.exe",
                                  opt7_dir   = "D:/KU/PhD/PhD Project/NCM_Test_Data/autofilter_NCM/Option 7",
                                  type = "A",    # pass-through to Filter.m
                                  baseTF = 0     # pass-through to Filter.m
                                  ) {
  # acc_ts is a ts() with delta=dt, or a numeric vector
  acc_vec <- as.numeric(acc_ts)

  # Times expected by MATLAB driver (seconds)
  Pt <- as.numeric(parr1)          # refined P-arrival relative to the current series start
  St <- as.numeric(SStart)
  Se <- as.numeric(SEnd)

  direction <- .map_component_to_dir(component)

  # Prepare temp IO
  tmpdir <- tempfile("opt7_")
  dir.create(tmpdir, showWarnings = FALSE, recursive = TRUE)
  in_mat  <- file.path(tmpdir, "opt7_in.mat")
  out_mat <- file.path(tmpdir, "opt7_out.mat")

  # Write inputs for MATLAB
  writeMat(in_mat,
           acc = acc_vec, dt = as.numeric(dt), mag = as.numeric(mag),
           Pt = as.numeric(Pt), St = as.numeric(St), Se = as.numeric(Se),
           direction = as.numeric(direction),
           type = as.character(type),
           baseTF = as.numeric(baseTF))

  # Build MATLAB -batch command
  # Ensure opt7_dir is on MATLAB path so it can find Filter.m & helpers
  norm <- function(p) gsub("\\\\", "/", normalizePath(p, winslash = "/", mustWork = FALSE))
  mexe   <- sprintf('"%s"', norm(matlab_exe))
  addp   <- sprintf("addpath('%s');", norm(opt7_dir))
  call   <- sprintf("opt7_driver('%s','%s');", norm(in_mat), norm(out_mat))
  mcode  <- sprintf("%s %s", addp, call)
  cmd    <- sprintf('%s -batch "%s"', mexe, mcode)

  # Run MATLAB
  status <- system(cmd)

  if (status != 0 || !file.exists(out_mat)) {
    stop("Option 7 (MATLAB) failed: see MATLAB console for details.")
  }

  # Read outputs
  out <- readMat(out_mat)
  fchp    <- as.numeric(out$fHP)
  fclp    <- as.numeric(out$fLP)
  meanSNR <- if (!is.null(out$meanSNR)) as.numeric(out$meanSNR) else NA_real_

  # Final sanity clamps (mirror your main script style)
  if (!is.finite(fchp) || fchp <= 0) fchp <- 0.1
  if (!is.finite(fclp) || fclp <= 0) fclp <- max(8, 10)
  if (fclp <= fchp) fchp <- max(0.9*fclp/10, min(fchp, fclp/10))

  list(fchp, fclp, meanSNR)
}
