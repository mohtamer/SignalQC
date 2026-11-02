windows = function(parr,hypd, magnitude,dt,npts){
        pwave= 6.0
        swave = 3.5
        psd = 1/(1/swave-1/pwave)
        sarr = parr + hypd/psd
        Mo <- 10^(1.5*magnitude+16.05)
        stress.drop <- 60
        beta <- 3.2
        fcorner <- 4.9*10^6*beta*(stress.drop/Mo)^(1/3)
        Td.s <- ifelse(magnitude < 4.5,10,max(1/fcorner*1.45,15))+0.1*hypd #Upper bound of 33 s was removed from Kishida et al.2016. Coefficient is changed from 1.4 to 1.45.
        send = sarr+Td.s
        carr = sarr+2*hypd/swave
        cend = carr + Td.s
		if(cend > npts*dt){
		  cend = npts*dt
		  if(send < cend){
  		    carr =  (send + cend)/2
          }else{
            if(sarr < cend){
		      send = sarr + (cend - sarr)/3
  		      carr =  (send + cend)/2	  
		    }else{
		      sarr = parr + (cend - parr)/4
		      send = sarr + (cend - parr)/4
  		      carr =  (send + cend)/2	  	
			}
		  }
		}
		if(carr<send){
		  carr = send+1
		  cend = min(carr + max(15,2*Td.s),npts*dt)
		}
        winstr = max(dt,parr - Td.s)
#		if(cend*dt < npts){
          out = list(winstr,parr,sarr,send,carr,cend)
#        }else{
#          out = list(winstr,parr,sarr,send,max(send+1,npts*dt-Td.s),npts*dt)		
#		}
        return(out)
}