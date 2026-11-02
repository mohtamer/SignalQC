testDISP_2 <- function(accfs,fc.HP.set,fc.LP,nfft,nNyq,dt,npts.Z,ne.bsl,nSkip.bsl,nAdd.bsl,nPole.HP,nPole.LP,freq,nstart,nend) {
	
	
	
sum.noise <- rep(0,length(fc.HP.set))
PGD_to_PGD_noise_ratio <- 0
counter <- 0

#filter and calculate difference between filtered and filtered+baseline displacements
for (i in fc.HP.set){ 
	counter <- counter+1
	
	holder.accfs <- accfs	
	
	
	
	
	## high pass causal filter
	holder.accfs[1:nNyq] <- holder.accfs[1:nNyq] * BWorth(freq[1:nNyq], i,  -nPole.HP) 
	## low pass causal filter
	holder.accfs[1:nNyq] <- holder.accfs[1:nNyq] * BWorth(freq[1:nNyq], fc.LP, nPole.LP)
	
	#... Prepare Fourier spectrum for inverse FFT
            #

    #causal	
    holder.accfs[1]    <- complex(real = Re(holder.accfs[1]),    imaginary = 0)
    holder.accfs[nNyq] <- complex(real = Re(holder.accfs[nNyq]), imaginary = 0)
    holder.accfs[nfft + 2 - (2:(nfft/2))] <- Conj(holder.accfs[2:(nfft/2)])
	
	#... Inverse FF; keep only the original time length
            
    #causal
    holder.accts.flt <- ts(data = Re(fft(holder.accfs, inverse = T))[1:npts.Z]/nfft, deltat = dt, start = 0)
	
	#baseline correct
	holder.accts.flt.base <- BslnAdj(holder.accts.flt, ne.bsl, nSkip.bsl, nAdd.bsl)
	
	#calculate displacements for filter+baseline
	holder.velts.flt.base <- integrate2V(holder.accts.flt.base)
    holder.dists.flt.base <- integrate2D(holder.accts.flt.base, holder.velts.flt.base)
        
            
	#sum of amplitude of displacement in noise 
	sum.noise[counter] <- sum(abs((holder.dists.flt.base[nstart:nend])))

	#pull the PGD_to_PGD_noise_ratio	
	PGD <- max(abs((holder.dists.flt.base)))
	PGD_noise <- max(abs((holder.dists.flt.base[nstart:nend])))
	PGD_to_PGD_noise_ratio[counter] <- PGD_noise/PGD
	

	
}
	
	out.data <- list(PGD_to_PGD_noise_ratio,sum.noise)
	
return(out.data)	
	
}