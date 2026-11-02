DC.mean <- function(accts, nDC)
#    negative nDC : DC is based on the whole windowed record; 
#    positive nDC : DC is taken as the average of the first nDC points of the windowed record
#
{
if (nDC < 0) {
accts <- accts - mean(accts)
}else{
accts <- accts - mean(accts[1:nDC])
}
return(accts)
}
