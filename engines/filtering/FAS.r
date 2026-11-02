FAS.cal <- function(accts, max.order)
# max.order determines the length of accts after padding 0 at the end. 
# If max.order = 3, the length of accts becomes at least 4 times, and maximum of 8 times.
# Phase / pi is added (11072013)
{
if (!is.ts(accts)) stop("Input array is not a regular time history!")
dt <- 1.0/tsp(accts)[3]
npts <- length(accts)
nfft <- 2^(floor(logb(npts, 2)) + max.order)
nNyq <- nfft/2 + 1;
df <- 1.0/(nfft * dt)

accts <- c(accts, rep(0, nfft - npts))
accfs <- fft(accts)
accfs.amp <- abs(accfs[1:nNyq])*dt
accfs.phase <- Arg(accfs)[1:nNyq]
freq <- ((1:nNyq) - 1) * df; 

min.k <- ceiling(nfft/npts)+1
output <- list(accfs,accfs.amp,freq,min.k,accfs.phase)
return(output)
}
