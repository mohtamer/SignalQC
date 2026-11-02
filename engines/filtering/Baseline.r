BslnAdj <- function(accts, nTaper, nSkip, nAdd) {

     npts <- length(accts)
     dt <- deltat(accts)

#       0. Apply DC Mean and Taper (This line is added by Tadahiro Kishida (07/24/2013) based on BASE4.FOR
#		1. Integrate uncorrected acc to velocity & displacement;
#		2. Pad zeros to improve stability of baseline estimation
#		3. Estimate baseline of displacement record


#... 0. Remove the mean value for the first 10 pts.
#...    Typically use 5 for the beginning taper (integer) in percent.
#...    Velocity taper was tested. But it won't work.
velocity.taper = FALSE
     accts <- Taper(accts,5, 0)
     accts <- DC.mean(accts, 10)
    if(velocity.taper){
     velts <- Differentiate2VA(accts,FALSE)
     velts <- DC.mean(velts, -1)
     velts <- Taper(velts,5, 0)
     accts <- Differentiate2VA(velts,TRUE)
     }

#... 1. Integrate (by time-domain integration) to Vel. and Dis.
     velts <- integrate2V(accts)
     dists <- integrate2D(accts, velts)

#... 2. Skip the first nSkip points, then append nAdd points for stable baseline.
     xx <- dists[(nSkip+1):npts]
     xx <- c(xx, rep(dists[npts], nAdd))


#... 3. Estimate polynomial baseline
     nParam <- 8
	 n1 <- nParam-1
	 n2 <- nParam-2
	 n3 <- nParam-3
     yy <- dt * ((1:length(xx))-1)
     for(i in 2:nParam) {
	     yyn <- yy^i
		 if(i==2){
         ypol <-  yyn
         }else{		 
         ypol <-  cbind(ypol, yyn)
		 }
	 }
     bl <- lm(xx ~ ypol - 1)

#... 4. Baseline
     b.dis <- ypol %*% coef(bl)
     b.vel <- (cbind(yy, ypol[,1:n2]) %*% (coef(bl)*(2:nParam)))
     b.acc <- (cbind(1,yy,ypol[,1:n3]) %*% (coef(bl)*(2:nParam)*(1:n1)))

     b.dis <- c(rep(0,nSkip), b.dis)[1:npts]
     b.vel <- c(rep(0,nSkip), b.vel)[1:npts]
     b.acc <- c(rep(0,nSkip), b.acc)[1:npts]

# End taper
            w <- numeric(npts)
            w <- w + 1.0
            w.vel <- numeric(npts)
            w.acc <- numeric(npts)
            if (nTaper > 0) {
        w[(npts-nTaper+1):npts]     <- sapply(1:nTaper, function(i, ne)     {
            ( 1.0 + cos(pi*(i-1)/ne))*0.5
        }, ne=nTaper)
        w.vel[(npts-nTaper+1):npts] <- sapply(1:nTaper, function(i, ne, dt) {
            (sin(pi*(i-1)/ne))*(pi/ne/dt)*(-0.5)
        }, ne=nTaper, dt=dt)
        w.acc[(npts-nTaper+1):npts] <- sapply(1:nTaper, function(i, ne, dt) {
            (cos(pi*(i-1)/ne))*(pi/ne/dt)^2*(-0.5)
        }, ne=nTaper, dt=dt)
    }
# Remove baseline and apply taper
    accts <- (accts - b.acc)*w + 2.0 * (velts - b.vel)*w.vel + (dists - b.dis)*w.acc
}

