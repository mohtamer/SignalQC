Taper <- function(accts, t.front, t.end)
{
# Original scriot exists in 'Process.TH.r', then modified based on the code in BASE4.FOR given by Bob Darragh (07/23/2013).
# If t.front/t.end is zero, no taper applied.
# If t.front/t.end is positive, the fraction of the total length of acc is tapered.
# If t.front/t.end is negative, the number of points of acc is tapered.
# floor for nb and ne are added since it casues the problem at the last point of npts.Z.
npts.Z = length(accts)
if(t.front != 0){
     nb  <- ifelse(t.front > 0, floor((npts.Z * t.front)/100), floor(abs(t.front)))
     # Apply front taper
     accts[1:nb] <- accts[1:nb] * sapply(1:nb, 
             function(i, nb) { (1.  + cos(pi * ((i - 1)/nb) + pi))/2.}, nb = nb)
}

if(t.end != 0){
     ne  <- ifelse(t.end > 0, floor((npts.Z * t.end)/100), floor(abs(t.end)))
     # Apply end taper
     accts[(npts.Z - ne + 1):npts.Z] <- accts[(npts.Z - ne + 1):npts.Z] * sapply(1:ne, 
             function(i, ne) { (1. + cos((pi * (i - 1))/ne))/2.} , ne=ne) 
}
#print((1. + cos((pi * (i - 1))/ne))/2.)
return(accts)
}
