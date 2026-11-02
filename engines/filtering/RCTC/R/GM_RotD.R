#' A Function for Combination of Two Rotated Ground Motions
#'
#' This function computes RotD with 180 rotated angles and Geometric Mean of RotD with 90 angles
#' @param data1 The first horizontal component of ground motion
#' @param data2 The second horizontal component of ground motion
#' @param period_t A array of oscillator periods
#' @param damping Damping ratio
#' @param time_dt Time step of two time series. These two datasets have the same time step
#' @param fraction A fraction for subset selection to speed up calculation. 0 is using all dataset.
#' 0.7 is recommended for RotD50 if the input data are Sinc interpolated (default);
#' 0.5 is recommended if GMRotI50 is also interested; 0.0 should be used regarding for RotD00
#' @return A list is returned with RotD, GMRotD, number of selected subsets points, and threshold values
#' @keywords GM_RotD_cal
#' @export
#' @examples
#' GM_RotD_cal(c(0.01,0.03,...0.5), c(-0.01,0.02,...0.05), c(0.01,0.02,...10), 0.05, 0.005, 0.7)

########################################
######## inputs: time series, interpolation multiplier
######## outputs: Sinc interpolated time series


GM_RotD_cal <- function(data1, data2, period_t, damping, time_dt, fraction = 0.7){
  # row is period sequence for a given rotated angle, colomn is angle sequence for a given period
  GMRotD <- matrix(nrow = length(seq(1,90)), ncol = length(period_t))
  RotD180 <- matrix(nrow = length(seq(1,180)), ncol = length(period_t))
  # record the number of selected points
  Num_points <- seq(1,111)
  rd_alevel <- seq(1,111)
  RD1 <- PS_cal_cpp(data1, period_t, damping, time_dt, 2) # matrix [period by data points]
  RD2 <- PS_cal_cpp(data2, period_t, damping, time_dt, 2) # matrix [period by data points]
  omega = 2*pi/period_t
  for(per_index in seq(1,111)){
    length_min <- min(length(RD1[per_index,]), length(RD2[per_index,]))
    rd_subset <- subset_select(RD1[per_index,], RD2[per_index,], fraction, length_min, time_dt, 1)
    rd_alevel[per_index] <- subset_select(RD1[per_index,], RD2[per_index,], fraction, length_min, time_dt, 2)
    Num_points[per_index] <- length(rd_subset)/3
    Rot_rd1 <- rd_subset[1,]
    Rot_rd2 <- rd_subset[2,]
    rot_angle4rot <- seq(1,180)
    for(theta in seq(1,90)){
      RS1 = Rot_rd1*cos(theta/180*pi) + Rot_rd2*sin(theta/180*pi)
      RS2 = -Rot_rd1*sin(theta/180*pi) + Rot_rd2*cos(theta/180*pi)
      RotD180[theta, per_index] = max(abs(RS1))*omega[per_index]^2
      RotD180[theta+90, per_index] = max(abs(RS2))*omega[per_index]^2
      GMRotD[theta,per_index] = sqrt(max(abs(RS1))*max(abs(RS2)))*omega[per_index]^2
    }
  }
  return(list(RotD180, GMRotD, Num_points, rd_alevel))
}
