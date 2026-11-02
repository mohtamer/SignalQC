Filter <- function(accts, hpfc, lpfc,type,baseTF)
#type: Butterworth filter type, A: Acausal, C: Causal
#baseTF: Baseline correction, TRUE: Apply, FALSE:Do not apply
{
#Filtering
nPole.HP=5; nPole.LP=4
if (!is.ts(accts)) stop("Input array is not a regular time history!")
dt <- 1.0/tsp(accts)[3]
npts <- length(accts)

accts <- DC.mean(accts,-1)
accts = Taper(accts, -round(0.5/dt), -round(0.5/dt))

nfft <- 2^(floor(logb(npts, 2)) + 4) 
nNyq <- nfft/2+1 
df <- 1.0/(nfft * dt)
freq <- ((1:nNyq) - 1) * df
accts <- c(accts, rep(0, nfft - npts))
accfs <- fft(accts)
 
##Acausal or Causal Butterworth fiter
    if(!is.na(hpfc) & hpfc>0){
    butterworth = fbutter(nNyq,df,hpfc,nPole.HP,"h",type)
    accfs[1:nNyq] <- accfs[1:nNyq] * butterworth
#    accfs[1:nNyq] <- accfs[1:nNyq] * hpassButterworth(nfft, dt,hpfc, nPole.HP)
    } 
    if(!is.na(hpfc) & lpfc>0){
    butterworth = fbutter(nNyq,df,lpfc,nPole.LP,"l",type)
    accfs[1:nNyq] <- accfs[1:nNyq] * butterworth 
#    accfs[1:nNyq] <- accfs[1:nNyq] * lpassButterworth(nfft, dt,lpfc, nPole.LP) 
    }

accfs[1]    <- complex(real = Re(accfs[1]),    imaginary = 0)
accfs[nNyq] <- complex(real = Re(accfs[nNyq]), imaginary = 0)
accfs[nfft + 2 - (2:(nfft/2))] <- Conj(accfs[2:(nfft/2)])
accts <- ts(data = Re(fft(accfs, inverse = T))[1:npts]/nfft, deltat = dt, start = 0)

#Baseline Correction
  if(baseTF == TRUE){
  te.bsl=10; Skip.bsl=0; Add.bsl=10
  ne.bsl <- ifelse(te.bsl > 0, ceiling((npts * te.bsl) / 100), te.bsl)
  nSkip.bsl <- ceiling((Skip.bsl/100)*npts)
  nAdd.bsl  <- ceiling((Add.bsl/100)*npts)
  accts <- BslnAdj(accts, ne.bsl, nSkip.bsl, nAdd.bsl)
  }
  
#AVD
velts <- integrate2V(accts)
dists <- integrate2D(accts, velts)
GRAVITY <- 980.665
velts <- velts * GRAVITY
dists <- dists * GRAVITY
output <- list(accts,velts,dists)
return(output)
}
