autofc <- function(accts, stime1, etime1, stime2, etime2,mag,Option,filename,PlotFAS = FALSE){
#Revised 07/14/2021 LPFc revise based on SMSIM
#Revised 08/25/2021 fcorner for fpeak search
#Revised 04/23/2022 PSA fchp lowerboud algorithm

#The function returns the fc-hp and fc-lp automatically.
#stime1 and etime1 are start and end times for noise window.
#stime2 and etime2 are start and end times for entire window.
#Put plot option = FALSE since it does not work for R Studio.
# Obtain the windowed time series
  #print(stime1)
  #print(etime1)
  #print(stime2)
  #print(etime2)
  library(reticulate)
  source_python("USGSgmprocesscrnrfq.py")
  dt <- 1.0/tsp(accts)[3]
  pga = max(abs(accts))
  print(pga)
  # Entire window
i1 <- max(1L, floor(stime2/dt))
i2 <- min(length(accts), floor(etime2/dt))
if (!is.finite(i1)) i1 <- 1L
if (!is.finite(i2)) i2 <- i1
if (i2 < i1) i2 <- i1
e.accts <- accts[i1:i2]
e.accts <- ts(data = Taper(e.accts, (-0.5/dt), (-0.5/dt)), delta = dt)
  print("Taper line")
  e.T <- 2^floor(logb(3276.80/dt,2))*dt
  e.order <- logb(e.T/dt, 2)-floor(logb(length(e.accts), 2))

  temp = FAS.cal(e.accts, e.order)

  freq = temp[[3]]
  e.FAS = temp[[2]]

  temp = SMOOTH(e.FAS, length(e.FAS),0,freq,freq[2],max(freq),-0.05)
  print("f peak unsmooth")
  print(freq[which.max(e.FAS)])
  e.FAS.s = temp[,1]
  e.freq.s = temp[,2]
  # Noise window (only if used)
if (stime1 != etime1) {
  j1 <- max(1L, floor(stime1/dt))
  j2 <- min(length(accts), floor(etime1/dt))
  if (!is.finite(j1)) j1 <- 1L
  if (!is.finite(j2)) j2 <- j1
  if (j2 < j1) j2 <- j1

  n.accts <- accts[j1:j2]
  n.accts <- ts(data = Taper(n.accts, 5, 5), delta = dt)

  n.order <- max(1, logb(e.T/dt, 2) - floor(logb(length(n.accts), 2)))
  print("Line33")
  temp     <- FAS.cal(n.accts, n.order)
  n.FAS    <- temp[[2]]
  freq     <- temp[[3]]
  temp     <- SMOOTH(n.FAS, length(n.FAS), 0, freq, freq[2], max(freq), -0.05)
  n.FAS.s  <- temp[,1]
  n.freq.s <- temp[,2]
} else {
  n.accts  <- NULL
  n.FAS.s  <- NULL
  n.freq.s <- NULL
}

  # --- build SNR and NOI aligned to e.FAS.s / e.freq.s ---
if (stime1 != etime1 && !is.null(n.FAS.s)) {
  L <- min(length(n.FAS.s), length(e.FAS.s))
  SNR <- e.FAS.s[1:L] / n.FAS.s[1:L]
  NOI <- n.FAS.s[1:L]
  # pad to match full length of e.FAS.s if needed (rest won't be used if outside bands)
  if (L < length(e.FAS.s)) {
    SNR <- c(SNR, rep(NA_real_, length(e.FAS.s) - L))
    NOI <- c(NOI, rep(NA_real_, length(e.FAS.s) - L))
  }
} else {
  # no noise window -> large SNR, harmless NOI
  SNR <- rep(100, length(e.FAS.s))
  NOI <- rep(1,   length(e.FAS.s))
}

#  n.FAS.s = n.FAS.s[which(n.freq.s%in%freq.s)]
#  e.FAS.s = e.FAS.s[which(e.freq.s%in%freq.s)]
#  plot(freq.s,e.FAS.s,log='xy',type='l',ylim=range(e.FAS.s),xlab="Frequency (Hz)",ylab = "Smoothed FAS (g.s)") 
#  png(paste(filename,"_FAS.png",sep=""))
#par(mfrow = c(1, 1), pty = "m", mar=c(4,4,3,2))
SNRP = SNR*10^-11  
#plot(freq.s,e.FAS.s,log='xy',type='l',ylim=range(c(e.FAS.s,SNRP)),xlab="Frequency (Hz)",ylab = "Smoothed FAS (g.s)") 
#lines(freq.s,SNRP,type='l',col='red')

  #  #print(max(freq.s))
  if(Option == 1){
  fchl = SNRfc(e.FAS.s,SNR,NOI,e.freq.s,mag,e.accts)
#  print("here")
#  print(fchl[[1]])

  }
  if(Option == 2){
  print("here")
#  fchl = Fhc_Flc(mag)  
  fchl = fchplp(mag,pga,0)
  }
  if(Option == 3){
# The code was developed by Tim Ancheta.
  	fchl = SSHPfc(e.FAS.s,e.freq.s)
  }
  if(Option == 4){
#Pengfei will use avd
#  fchl = Fhc_Flc(mag)
  avd = Filter(accts, fc.auto[[1]], fc.auto[[2]])  
  }
  if(Option == 5){
#https://pypi.org/project/ucla-geotech-tools-auto-fchp/#description
#ucla-geotech-tools-auto-fchp 0.0.6 by Scott Brandenberg. Requires Microsoft C++ Build Tools
    fchl = data.frame(fchp=uclaauto_fchp(as.numeric(accts),dt))
    fchl[, 'fchl'] = NA
    fchl= as.list(fchl)
    
  }
if(Option == 6){
  #USGS gmprocess Waveform processing python function: corner_frequencies.py
  
  fchl = USGS(SNR,e.freq.s)
  fchl = c(fchl[1],fchl[2])
  fchl= as.list(fchl)
  }
#if (Option == 7) {
  # Guo etal 2025
  # will be called from autofc_option7_matlab.R
  

#print("here")
 if(PlotFAS==TRUE){
 plot(freq.s,e.FAS.s,log='xy',type='l',ylim=range(c(e.FAS.s,SNRP)),xlab="Frequency (Hz)",ylab = "Smoothed FAS (g.s)") 
 lines(freq.s,SNRP,type='l',col='red')
 abline(v=fchl[[1]],col='blue')
 abline(v=fchl[[2]],col='red')
 dev.off()
 }
#savePlot(filename=paste(filename,"_FAS",sep=""), type='png')
##print(fchl)

meanSNR = mean(SNR[e.freq.s>fchl[[1]]&e.freq.s<fchl[[2]]])
fchl =list(fchl[[1]],fchl[[2]],meanSNR)
return(fchl) 
}

SNRfc = function(e.FAS.s, SNR, NOI, freq.s, mag, accts) {

  # ---- 0) keep paired vectors same length so we don't index past ends ----
  L <- min(length(e.FAS.s), length(SNR), length(NOI), length(freq.s))
  e.FAS.s <- as.numeric(e.FAS.s[1:L])
  SNR     <- as.numeric(SNR[1:L])
  NOI     <- as.numeric(NOI[1:L])
  freq.s  <- as.numeric(freq.s[1:L])

  # ---- 1) define peak-search band & compute fpeak (dominant spectral peak) ----
  # (same physical logic as before; now with fallbacks so fpeak is always finite)
  themaxf <- 10
  Mo <- 10^(1.5*mag + 16.05)
  stress.drop <- 60
  beta <- 3.2
  fcorner <- 4.9e6 * beta * (stress.drop/Mo)^(1/3)
  theminf <- fcorner

  # band mask used to look for the main spectral bump
  if (mag > 4) {
    msk <- (freq.s > max(0.1, min(freq.s, na.rm = TRUE))) &
           (freq.s < themaxf) &
           (freq.s > theminf)
  } else {
    msk <- (freq.s > max(0.1, min(freq.s, na.rm = TRUE))) &
           (freq.s < max(freq.s, na.rm = TRUE)) &
           (freq.s > theminf)
  }

  # fallback: if mask is empty (all FALSE / NA), just use everything available
  if (!any(msk, na.rm = TRUE)) {
    msk <- rep(TRUE, length(freq.s))
  }

  freq.ssub  <- freq.s[msk]
  e.FAS.ssub <- e.FAS.s[msk]

  # remove any non-finite pairs before we call which.max / max
  good_sub <- is.finite(freq.ssub) & is.finite(e.FAS.ssub)
  freq.ssub  <- freq.ssub[good_sub]
  e.FAS.ssub <- e.FAS.ssub[good_sub]

  # absolute worst-case fallback: if still empty, just take first point
  if (length(freq.ssub) == 0) {
    freq.ssub  <- freq.s[1]
    e.FAS.ssub <- e.FAS.s[1]
  }

  # dominant spectral peak frequency
  fpeak_idx <- which.max(e.FAS.ssub)
  fpeak     <- freq.ssub[fpeak_idx]

  # guarantee fpeak is a single finite numeric value
  if (length(fpeak) == 0 || !is.finite(fpeak)) {
    fpeak <- max(freq.ssub, na.rm = TRUE)
  }

  FASpeak <- max(e.FAS.ssub, na.rm = TRUE)

  # ---- 2) minimum FAS before the peak (to get fmin and FASmin) ----
  pre_peak_mask <- freq.s < fpeak
  if (!any(pre_peak_mask, na.rm = TRUE)) {
    # if fpeak is at/near the first freq, just let pre-peak = everything
    pre_peak_mask <- rep(TRUE, length(freq.s))
  }

  FASpre   <- e.FAS.s[pre_peak_mask]
  freq.pre <- freq.s[pre_peak_mask]

  FASmin <- min(FASpre, na.rm = TRUE)
  fmin   <- freq.pre[which.min(FASpre)]

  # ---- 3) 30% & 70% "lines" in log space for the FAS (same math as original) ----
  e.FAS.lower <- exp(log(FASmin) + (log(FASpeak) - log(FASmin)) * 0.3)
  e.FAS.upper <- exp(log(FASmin) + (log(FASpeak) - log(FASmin)) * 0.7)

  # ---- 4) lower/upper bounds of fchp from FAS itself (fhplb, fhpubFAS) ----
  outd <- e.FAS.s - e.FAS.lower
  pick <- (outd < 0) & (freq.s >= fmin) & (freq.s <= fpeak)
  fhplbsub <- freq.s[pick]
  fhplb    <- if (length(fhplbsub)) max(fhplbsub) else NA_real_

  outd <- e.FAS.s - e.FAS.upper
  pick <- (outd < 0) & (freq.s >= fmin) & (freq.s <= fpeak)
  fhpubsub <- freq.s[pick]
  fhpubFAS <- if (length(fhpubsub)) max(fhpubsub) else NA_real_

  # ---- 5) SNR range between fmin and fpeak (SNRmin, SNRmax, etc.) ----
  snr_band_mask <- (freq.s <= fpeak) & (freq.s >= fmin)
  if (!any(snr_band_mask, na.rm = TRUE)) {
    # fallback if nothing in [fmin, fpeak]
    snr_band_mask <- rep(TRUE, length(freq.s))
  }

  SNRsub  <- SNR[snr_band_mask]
  fre_sub <- freq.s[snr_band_mask]

  # drop non-finite SNR in that band for stable min/max
  good_snr <- is.finite(SNRsub)
  SNRsub   <- SNRsub[good_snr]
  fre_sub  <- fre_sub[good_snr]

  # if STILL empty, just give it a dummy SNR ~3 at fpeak
  if (length(SNRsub) == 0) {
    SNRsub  <- 3
    fre_sub <- fpeak
  }

  SNRmin  <- min(SNRsub, na.rm = TRUE)
  SNRmax  <- max(SNRsub, na.rm = TRUE)
  fSNRmin <- fre_sub[which.min(SNRsub)]
  fSNRmax <- fre_sub[which.max(SNRsub)]

  # ---- 6) draw 30% / 70% SNR "lines" in log space (SNR.lower / SNR.upper) ----
  #     same definition you had, just NA-guarded
  SNR.upper <- exp(log(SNRmin) + (log(SNRmax) - log(SNRmin)) * 0.7)
  SNR.lower <- exp(log(SNRmin) + (log(SNRmax) - log(SNRmin)) * 0.3)

  # ---- 7) from SNR: alternate lower/upper bounds on fchp (fhplbSNR, fhpubSNR) ----
  outd <- SNR - SNR.lower
  pick <- (outd < 0) & (freq.s >= fSNRmin) & (freq.s <= fSNRmax)
  fhplbSNR <- if (any(pick, na.rm = TRUE)) max(freq.s[pick], na.rm = TRUE) else NA_real_

  outd <- SNR - SNR.upper
  pick <- (outd < 0) & (freq.s >= fSNRmin) & (freq.s <= fSNRmax)
  fhpubSNR <- if (any(pick, na.rm = TRUE)) max(freq.s[pick], na.rm = TRUE) else NA_real_

  # ---- 8) combine FAS bounds and SNR bounds for allowed f_hp search band ----
  if (!is.finite(fhpubFAS)) fhpubFAS <- Inf
  if (!is.finite(fhplb))    fhplb    <- -Inf

  fhpub <- min(fhpubFAS, fhpubSNR, na.rm = TRUE)
  fhplb <- max(fhplb,     fhplbSNR,  na.rm = TRUE)

  # ---- 9) Find SNR = 3 crossings for f_hp candidates (f_up / nfchp) ----
  DSNR <- SNR - 3
  f_up <- rep(NA_real_, length(DSNR))
  k    <- 1L

  for (ii in 1:(length(DSNR) - 1L)) {
    if (!is.finite(DSNR[ii])     || !is.finite(DSNR[ii+1L])) next
    f1 <- DSNR[ii] * DSNR[ii+1L]

    cond_ok <-
      !is.na(f1) &&
      f1 <= 0 &&
      DSNR[ii] <= 0 &&                           # crossing down through 3
      is.finite(freq.s[ii]) &&
      is.finite(fhplb) && is.finite(fhpub) &&
      freq.s[ii] >= fhplb && freq.s[ii] <= fhpub # must be in our allowed band

    if (cond_ok) {
      f_up[k] <- freq.s[ii+1L]
      k <- k + 1L
    }
  }

  f_up  <- f_up[is.finite(f_up)]
  nfchp <- length(f_up)

  # how strong / weak SNR-3 is in that band, same idea as original
  MASNR <- suppressWarnings(max(DSNR[(freq.s >= fhplb) & (freq.s <= fhpub)],
                                na.rm = TRUE))
  MISNR <- suppressWarnings(min(DSNR[(freq.s >= fhplb) & (freq.s <= fhpub)],
                                na.rm = TRUE))

  # ---- 10) Pick final f_hp using same decision logic, with NA guards ----
  if (nfchp > 0) {
    f_hp <- max(f_up, na.rm = TRUE)
  } else {
    if (is.finite(MASNR) && MASNR < 0) {
      f_hp <- fhpub
    } else if (is.finite(MISNR) && MISNR > 0) {
      f_hp <- fhplb
    } else {
      f_hp <- fhpub
    }
  }

  # ---- 11) Candidates for f_lp: where SNR crosses 3 AFTER the peak (f_down) ----
  f_down <- rep(NA_real_, length(DSNR))
  m      <- 1L

  for (ii in 1:(length(DSNR) - 1L)) {
    if (!is.finite(DSNR[ii])     || !is.finite(DSNR[ii+1L])) next
    f1 <- DSNR[ii] * DSNR[ii+1L]

    cond_ok <-
      !is.na(f1) &&
      f1 <= 0 &&
      DSNR[ii] > 0 &&                 # crossing downward from above 3
      is.finite(freq.s[ii]) &&
      is.finite(fpeak) &&
      freq.s[ii] >= fpeak             # only look AFTER the main peak

    if (cond_ok) {
      f_down[m] <- freq.s[ii]
      m <- m + 1L
    }
  }

  f_down <- f_down[is.finite(f_down)]
  nfclp  <- length(f_down)

  # ---- 12) Select f_lp using same original logic (with guards) ----
  if (nfclp == 0) {
    # fallback heuristic
    f_lp <- max(freq.s, na.rm = TRUE) * 0.8

    inFAS.s <- e.FAS.s[freq.s > 0.8 * f_lp]
    if (length(inFAS.s) > 1) {
      RinFAS.s <- inFAS.s[1] / inFAS.s[length(inFAS.s)]
    } else {
      RinFAS.s <- NA_real_
    }

    if (is.finite(RinFAS.s) && RinFAS.s < 2) {
      fl <- diff(sign(e.FAS.s - 1.1 * mean(inFAS.s)))
      cand <- freq.s[which(fl == -2)]
      cand <- cand[cand > fpeak]
      if (length(cand) > 0) f_lp <- cand[1]
    }
  } else {
    f_lp <- min(f_down[1:nfclp], na.rm = TRUE)

    if (!is.finite(f_lp)) {
      f_lp <- max(freq.s, na.rm = TRUE) * 0.8

      inFAS.s <- e.FAS.s[freq.s > 0.8 * f_lp]
      if (length(inFAS.s) > 1) {
        RinFAS.s <- inFAS.s[1] / inFAS.s[length(inFAS.s)]
      } else {
        RinFAS.s <- NA_real_
      }

      if (is.finite(RinFAS.s) && RinFAS.s < 2) {
        fl <- diff(sign(e.FAS.s - 1.1 * mean(inFAS.s)))
        cand <- freq.s[which(fl == -2)]
        cand <- cand[cand > fpeak]
        if (length(cand) > 0) f_lp <- cand[1]
      }
    }
  }

  # original clipping: f_lp cannot be crazy-high or tiny
  f_lp <- max(10,
              min(0.8 * max(freq.s, na.rm = TRUE), f_lp),
              na.rm = TRUE)

  # ---- 13) PSA-based refinement of f_hp (same method you had) ----
  dt <- 1.0 / tsp(accts)[3]
  period.dat <- read.table("period_NGA.dat")
  npr <- period.dat[1,1]
  pr  <- period.dat[2:nrow(period.dat),1]

  tmp <- PS_cal_cpp(data = accts,
                    period_t = pr,
                    damping = 0.05,
                    time_dt = dt,
                    type_return = 1)

  rsp <- data.frame(period = pr,
                    t(matrix(tmp, ncol = npr, nrow = 2)))
  names(rsp) <- c("period", "aa", "psa")
  rsp$psv <- rsp$psa / (2*pi/pr)
  rsp$sd  <- rsp$psv / (2*pi/pr)

  if (is.finite(f_hp) && f_hp > 1/20) {
    psagtT <- rsp$psa[rsp$period > 1/(1.25 * f_hp)]

    if (length(psagtT) > 0 && which.max(psagtT) != 1) {
      psagtTmax <- max(psagtT)
      idx <- which(sign(rsp$psa - psagtTmax) == 1)
      if (length(idx) > 0) {
        f_hp <- 1 / (max(pr[idx]))
      }
    }

    # Avoid f_hp = 0. Keep same fallback logic.
    if (!is.finite(f_hp) || f_hp == 0) {
      f_hp <- f_lp / 5
    }
  }

  # ---- 14) small-mag tweak (same intent: bias hp upward if mag<5) ----
  if (mag < 5.0) {
    m_smallmag <- (freq.s >= 1/3) & (freq.s <= 10) & is.finite(NOI)
    if (any(m_smallmag)) {
      minSNRInd <- which.min(NOI[m_smallmag])
      fc_hp2 <- freq.s[m_smallmag][minSNRInd]

      if (!is.finite(f_hp) || f_hp < 0) {
        f_hp <- fc_hp2
      } else if (is.finite(fc_hp2)) {
        f_hp <- min(fc_hp2,
                    exp((log(f_hp) + 3 * log(fc_hp2)) / 4))
      }
    }
  }

  # ---- 15) return hp/lp as numeric ----
  list(as.numeric(f_hp), as.numeric(f_lp))
}


#x = read.table("201710262037_BK_HULI_HHE.txt") 
#accts = ts(data=x[,2],delta=x[2,1])
#plot(x[,1],x[,2], type = 'l')
#stime1 = 20; etime1=80; stime2=20; etime2=300
#temp= autofc(accts, stime1, etime1, stime2, etime2)

##based on magnitude return high pass/ low pass frequencies 
fchplp = function(M, PGA, eps) {

  # ---- SAFE log(PGA) ----
  if (!is.finite(PGA) || PGA <= 0) {
    # if PGA invalid, return NA corners (so caller can na.omit safely)
    return(list(NA_real_, NA_real_))
  }

  lnpga = log(PGA)

  fchp = exp(0.6858933 -0.5551593*M + 0.9534*eps)
  fhc  = fchp

  if (lnpga < -8.3) {
    b0 = 3.702475 + 2.389173
    b1 = 0.047730 + 0.288075
    lpfc = b0 + b1*lnpga + 0.5843*eps
  } else {
    b0 = 3.702475
    b1 = 0.047730
    lpfc = b0 + b1*lnpga + 0.5843*eps
  }

  flc = exp(lpfc)
  return(list(fhc, flc))
}

##Based on shape of FAS and displacement time sereis (Coded by Tim)
fc_shp_dis= function(accts,dt,e.FAS,freq,accfs.amp.s,freq.c.s,stime1,etime1) {
  
  
  library(zoo)
  lseq <- function(from=1, to=100000, length.out=10) {
    # logarithmic spaced sequence
    # blatantly stolen from library("emdbook"), because need only this
    exp(seq(log(from), log(to), length.out = length.out))
  }
  
  ##try median smoothing
  hold.smooth <- data.frame(y = runmed(e.FAS[-1],length(e.FAS[-1])*0.1,endrule = 'median'))
  hold.smooth.2 <- data.frame(y = lowess(freq[-1], hold.smooth$y, f = freq[-1][1], iter = 2)$y)				
  
  
  xz <- as.zoo(hold.smooth.2)
  rxz <- rollapply(xz, 3, function(x) which.min(x)==2)
  local.minimums.index.smooth <- index(rxz)[coredata(rxz)]
  
  rxz <- rollapply(xz, 3, function(x) which.max(x)==2)
  local.maximums.index.smooth <- index(rxz)[coredata(rxz)]
  
  ############### Subroutine for selecting LP
  amp.min.max <- hold.smooth.2$y[c(local.minimums.index.smooth,local.maximums.index.smooth)]
  
  #create groups by 1 log cycle			
  groups.amp <- lseq(from = min(hold.smooth.2$y), to = max(hold.smooth.2$y))			
  
  
  #find groups of max/mins
  count.groups <- 0
  first.group.min.max.freq <- -1
  last.group.min.max.freq <- 0
  last.group.index <- 0
  for (iii in 1:(length(groups.amp)-1)){
    bin.min <- groups.amp[iii]
    bin.max <- groups.amp[iii+1]
    count.groups[iii] <- sum(amp.min.max > bin.min & amp.min.max < bin.max)
    if (count.groups[iii] > 0 && sum(first.group.min.max.freq == -1) == 1) {
      #      first.group.min.max.freq <- freq.a[-1][c(local.minimums.index.smooth,local.maximums.index.smooth)[amp.min.max > bin.min & amp.min.max < bin.max]]
      first.group.min.max.freq <- freq[-1][c(local.minimums.index.smooth,local.maximums.index.smooth)[amp.min.max > bin.min & amp.min.max < bin.max]]   #Check with Tim
    }
    if (count.groups[iii] > 0) {
      #    last.group.min.max.freq <- freq.a[-1][c(local.minimums.index.smooth,local.maximums.index.smooth)[amp.min.max > bin.min & amp.min.max < bin.max]]
      last.group.min.max.freq <- freq[-1][c(local.minimums.index.smooth,local.maximums.index.smooth)[amp.min.max > bin.min & amp.min.max < bin.max]]      #Check with Tim
      last.group.min.max.index <- c(local.minimums.index.smooth,local.maximums.index.smooth)[amp.min.max > bin.min & amp.min.max < bin.max]
      last.group.index <- iii
    }
  }
  #use lowest frequency in group as low pass 
  auto.fc.LP <- min(first.group.min.max.freq)
  
  
  ##print(auto.fc.LP)
  
  ###############	 subroutine for picking HP
  
  
  
  
  
  #find peak of spectrum
  peak.index.s <- which(accfs.amp.s[-1] == max(accfs.amp.s[-1]))
  
  #Test if peak.index uses a large amplitude in the noise part of the spectrum
  if (freq.c.s[-1][peak.index.s] < 0.1) {
    #try again with frequencies > 0.1 Hz
    add.index <- max(which(freq.c.s[-1] < 0.1))
    temp.accfs.amp.s <- accfs.amp.s[-1]
    temp.accfs.amp.s <- temp.accfs.amp.s[add.index:length(temp.accfs.amp.s)]
    peak.index.s <- which(temp.accfs.amp.s[-1] == max(temp.accfs.amp.s[-1]))
    peak.index.s <- peak.index.s+add.index
    
  }
  
  
  #find local minimums below peak of the spectram
  xz <- as.zoo(accfs.amp.s[-1][1:peak.index.s])
  rxz <- rollapply(xz, 3, function(x) which.min(x)==2)
  local.minimums.index <- index(rxz)[coredata(rxz)]
  
  
  #eliminate frequencies lower than the global minimum of the spectrum below the peak of the spectrum
  min.below.peak.index.s <- which(accfs.amp.s[-1][1:peak.index.s] == min(accfs.amp.s[-1][1:peak.index.s]))
  
  #generate frequency and amplitude array for testing the PGD_to_PGD_noise_ratio		
  fc.HP.set <- freq.c.s[-1][local.minimums.index]
  amp.HP.set <- accfs.amp.s[-1][local.minimums.index]
  amp.HP.set <- amp.HP.set[!is.na(fc.HP.set)]
  fc.HP.set <- fc.HP.set[!is.na(fc.HP.set)]
  amp.HP.set <- amp.HP.set[fc.HP.set >= freq.c.s[-1][min.below.peak.index.s]]
  fc.HP.set <- fc.HP.set[fc.HP.set >= freq.c.s[-1][min.below.peak.index.s]]
  
  
  
  
  #initialize to start test
  
  PGD_to_PGD_noise_end = 1 
  noise.PGD.threshold <- 0.07
  
  counter.while = 1
  while(PGD_to_PGD_noise_end > noise.PGD.threshold && counter.while <= 20) {
    fc.HP.set.2 <- fc.HP.set[(1+(10*(counter.while-1))):(10*counter.while)]
    
    
    
    npts.Z = length(accts) 
    order.a = 3
    nfft.a = 2^(floor(logb(npts.Z, 2)) + order.a)
    nNyq.a = nfft.a/2 + 1
    te.bsl=10
    ne.bsl = ifelse(te.bsl > 0, ceiling((npts.Z * te.bsl) / 100), te.bsl)
    Skip.bsl=0
    nSkip.bsl = ceiling((Skip.bsl/100)*npts.Z)
    Add.bsl=10
    nAdd.bsl = ceiling((Add.bsl/100)*npts.Z)
    nPole.HP = 5
    nPole.LP = 4
    
    
    testDISP_2_out <- testDISP_2(e.FAS,fc.HP.set,auto.fc.LP,nfft.a,nNyq.a,dt,npts.Z,ne.bsl,nSkip.bsl,nAdd.bsl,nPole.HP,nPole.LP,freq,nstart=stime1,nend=etime1)
    
    
    #print(str(testDISP_2_out))
    PGD_noise_to_PGD_ratio <- testDISP_2_out[[1]]
    sum.noise <- testDISP_2_out[[2]]
    
    #    #print(paste0('PGD_to_PGD_noise_ratio = ', as.character(PGD_to_PGD_noise_ratio)))
    
    auto.fc.HP <- fc.HP.set[min(which(PGD_noise_to_PGD_ratio < noise.PGD.threshold))]
    PGD_to_PGD_noise_end <- PGD_noise_to_PGD_ratio[min(which(PGD_noise_to_PGD_ratio < noise.PGD.threshold))]
    ##print(PGD_to_PGD_noise_end)
    if (is.na(PGD_to_PGD_noise_end)) {
      PGD_to_PGD_noise_end <- 1
    }
    
    
    counter.while <- counter.while+1
  }
  
  
  ###############
  
  
  
  #automation
  fc2 = vector(mode="numeric",length=2)
  fc2 = NULL
  fc2[1] <- auto.fc.HP
  fc2[2] <- auto.fc.LP 
  
  return(list(fc2[1],fc2[2]))
}


SSHPfc = function(accfs.amp.s,freq.c.s) { 		
  
  require(zoo)
  
  #input
  #accfs.amp.s = smoothed Fourier amplitude spectra, from SMOOTH.r 
  #freq.c.s = smoothed Fourier amplitude spectra frequencies, from SMOOTH.r
  #e.g. 
  # temp <- SMOOTH(accfs.amp, nNyq.c, NS, freq.c, FMIN, FMAX, KEY1)
  # accfs.amp.s <- temp[,1]
  # freq.c.s <- temp[,2]
  
  
  
  ############### Subroutine for selecting LP
  
  
  
  # automated LP based on spectra shape was not reliable, keep simple holder not to be used
  auto.fc.LP <- round(0.8*max(freq.c.s),1) 
  
  ###############	 subroutine for picking HP
  
  
  
  
  
  #find peak of spectrum
  peak.index.s <- which(accfs.amp.s[-1] == max(accfs.amp.s[-1]))
  
  #Test if peak.index uses a large amplitude in the noise part of the spectrum
  if (freq.c.s[-1][peak.index.s] < 0.1) {
    #try again with frequencies > 0.1 Hz
    add.index <- max(which(freq.c.s[-1] < 0.1))
    temp.accfs.amp.s <- accfs.amp.s[-1]
    temp.accfs.amp.s <- temp.accfs.amp.s[add.index:length(temp.accfs.amp.s)]
    peak.index.s <- which(temp.accfs.amp.s[-1] == max(temp.accfs.amp.s[-1]))
    peak.index.s <- peak.index.s+add.index
    
  }
  
  
  #find local minimums below peak of the spectram
  xz <- as.zoo(accfs.amp.s[-1][1:peak.index.s])
  rxz <- rollapply(xz, 3, function(x) which.min(x)==2)
  local.minimums.index <- index(rxz)[coredata(rxz)]
  
  
  #eliminate frequencies lower than the global minimum of the spectrum below the peak of the spectrum
  min.below.peak.index.s <- which(accfs.amp.s[-1][1:peak.index.s] == min(accfs.amp.s[-1][1:peak.index.s]))
  
  #generate frequency and amplitude array to calculate slope of the spectrum			
  fc.HP.set <- freq.c.s[-1][local.minimums.index]
  amp.HP.set <- accfs.amp.s[-1][local.minimums.index]
  amp.HP.set <- amp.HP.set[!is.na(fc.HP.set)]
  fc.HP.set <- fc.HP.set[!is.na(fc.HP.set)]
  amp.HP.set <- amp.HP.set[fc.HP.set >= freq.c.s[-1][min.below.peak.index.s]]
  fc.HP.set <- fc.HP.set[fc.HP.set >= freq.c.s[-1][min.below.peak.index.s]]
  
  
  #add one frequency between last frequency and peak (for the case of short or smooth increase to peak where there are no local minimums in the spectrum)
  add.HP.set.index <- which(freq.c.s[-1] > ((fc.HP.set[length(fc.HP.set)]+freq.c.s[-1][peak.index.s])/2))[1]
  add.fc.HP.set <- freq.c.s[-1][add.HP.set.index]
  add.amp.HP.set <- accfs.amp.s[-1][add.HP.set.index]
  fc.HP.set <- c(fc.HP.set, add.fc.HP.set)
  amp.HP.set <- c(amp.HP.set, add.amp.HP.set)
  
  #add the peak frequency, if not already in the list
  if (length(which(fc.HP.set == freq.c.s[-1][peak.index.s])) > 0 ) {
    add.fc.HP.set <- freq.c.s[-1][peak.index.s]
    add.amp.HP.set <- accfs.amp.s[-1][peak.index.s]
    fc.HP.set <- c(fc.HP.set, add.fc.HP.set)
    amp.HP.set <- c(amp.HP.set, add.amp.HP.set)
    
  } 
  
  #if minimum of the spectrum is near the initial frequency allow a test of the first frequency (20% of the initial frequenices for now)
  if (freq.c.s[-1][min.below.peak.index.s] < freq.c.s[-1][round(length(freq.c.s[-1])*0.20)]) {
    add.fc.HP.set <- freq.c.s[-1][min.below.peak.index.s+1]
    add.amp.HP.set <- accfs.amp.s[-1][min.below.peak.index.s+1]
    fc.HP.set <- c(fc.HP.set[1],add.fc.HP.set, fc.HP.set[-1])
    amp.HP.set <- c(amp.HP.set[1],add.amp.HP.set,amp.HP.set[-1])
  }
  
  
  
  
  if (length(fc.HP.set) == 1) {
    print("LIMTATION: frequency set has only one value")
    mtext("LIMITATION: frequency set has only one values",line = 2, col = 'red')
  } else if (length(fc.HP.set) == 2) {
    print("LIMITATION: frequency set has only two values")
    mtext("LIMITATION: frequency set has only two values",line = 2, col = 'red')
    #add a test frequency in between two values
    
    add.HP.set.index <- which(freq.c.s[-1] > ((fc.HP.set[1]+fc.HP.set[2])/2))[1]
    
    add.fc.HP.set <- freq.c.s[-1][add.HP.set.index]
    add.amp.HP.set <- accfs.amp.s[-1][add.HP.set.index]
    
    fc.HP.set <- c(fc.HP.set[1], add.fc.HP.set,fc.HP.set[2])
    amp.HP.set <- c(amp.HP.set[1], add.amp.HP.set, amp.HP.set[2])
  }
  
  #check if frequencies are below or on the 2 slope ramp
  
  #initialize			
  slope.amp <- fc.HP.set*0
  slope.target <- slope.amp
  slope.target[1] <- 0
  slope.target[length(fc.HP.set)] <- 0
  
  slope.amp.2 <- fc.HP.set*0
  slope.target.2 <- slope.amp
  slope.target.2[1] <- 0
  slope.target.2[length(fc.HP.set)] <- 0
  
  
  if (length(fc.HP.set) > 5) {
    
    for (ifreq in 3:(length(fc.HP.set)-2)) {
      temp.x = fc.HP.set[(ifreq-2):(ifreq+2)]
      temp.y = amp.HP.set[(ifreq-2):(ifreq+2)]
      slope.amp.2[ifreq] <- lm(temp.y~temp.x)$coefficients[2]
      
      slope <- 2.
      x0 <- fc.HP.set[(ifreq-2)]
      y0 <- amp.HP.set[(ifreq-2)]
      y1 <- amp.HP.set[(ifreq+2)]
      x1 <-10 ^ (log10(x0) + (log10(y1) - log10(y0)) / slope)
      
      slope.target.2[ifreq] <- (y1-y0)/(x1-x0)
    }
    
    remove.ind.2 <- which((slope.amp.2/slope.target.2)>0.3)
    remove.ind.2 <- remove.ind.2[(remove.ind.2-c(remove.ind.2[-1],remove.ind.2[length(remove.ind.2)])) >= -1]
    
  } 
  
  
  for (ifreq in 2:(length(fc.HP.set)-1)) {
    temp.x = fc.HP.set[(ifreq-1):(ifreq+1)]
    temp.y = amp.HP.set[(ifreq-1):(ifreq+1)]
    slope.amp[ifreq] <- lm(temp.y~temp.x)$coefficients[2]
    
    slope <- 2.
    x0 <- fc.HP.set[(ifreq-1)]
    y0 <- amp.HP.set[(ifreq-1)]
    y1 <- amp.HP.set[(ifreq+1)]
    x1 <-10 ^ (log10(x0) + (log10(y1) - log10(y0)) / slope)
    
    slope.target[ifreq] <- (y1-y0)/(x1-x0)
  }
  
  #find frequency indices if it is on the 2 slope
  remove.ind <- which((slope.amp/slope.target)>0.3)
  #do not use frequency indices which have an isolated large slope some noisey parts of the spectrum may have high +slopes then reverse to low slope
  #print(remove.ind-c(remove.ind[-1],remove.ind[length(remove.ind)]))
  #remove.ind <- remove.ind[(remove.ind-c(remove.ind[-1],remove.ind[length(remove.ind)])) >= -1]
  
  
  if (length(fc.HP.set) > 5) {
    possible.ind <- (min(remove.ind.2)-1)
  } else {
    possible.ind <- (min(remove.ind)-1)
  }
  
  
  keep.ind <- seq(1,length(fc.HP.set))
  keep.ind <- keep.ind[keep.ind <= possible.ind]
  
  #save the first guess of the HP filter corner based on spectra shape
  auto.fc.HP <- fc.HP.set[possible.ind]
  
  
  
  return(list(auto.fc.HP,auto.fc.LP))
  
}