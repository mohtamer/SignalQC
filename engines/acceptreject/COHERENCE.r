library(seewave)
COHERENCE <- function(accts1,accts2,max.order)
# max.order determines the length of accts after padding 0 at the end. 
# If max.order = 3, the length of accts becomes at least 4 times, and maximum of 8 times.
{
if (!is.ts(accts1)) stop("Input array is not a regular time history!")
dt <- 1.0/tsp(accts1)[3]
npts <- length(accts1)
nfft <- 2^(floor(logb(npts, 2)) + max.order)
nNyq <- nfft/2 + 1;
df <- 1.0/(nfft * dt)
freq <- ((1:nNyq) - 1) * df; 

accts1 <- c(accts1, rep(0, nfft - npts))
accts2 <- c(accts2, rep(0, nfft - npts))
COH1 = coh(accts1,accts2,f=nfft,plot =FALSE)
coherence = c(1,COH1[,2])

output <- list(coherence,freq)
return(output)
}
