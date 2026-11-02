autoparr = function(accts,Option,evdp,dist,relative.event.time){
#Option 1: P arrival is detected by STA LTA ratio
#Option 2: P arrival is detected by AIC
#Option 3: PSpicker
#Option 4: P arrival is detected by the algorithm from Pengfei, check-off
#evdp: Hypocenter depth
#dist: Hypocenter distance
#relative.event.time - Origin time - record starting time in sec.

 dt <- 1.0/tsp(accts)[3]
 if(Option == 1){
   Ts = 2.0; Tl = 20; Tau=2; thre = 0.6 
   parr = STALTA_P_arrival(accts,dt,Ts,Tl,Tau,thre)
 }
 if(Option == 2){
   parr = AIC_Picker(accts,dt)
 }
 if(Option == 4){
   x <- seq(0,length(accts)-1)*dt
   y <- as.numeric(accts)
   res = ps_arrival(x, y) 
   parr = res[[1]] # res[[1]] is P-arrival, res[[2]] is S-arrival
 } 
 
 if(Option == 3) {
   UE = accts
   UN = accts
   UZ = accts
   parr = PSpicker(evdp,dist,dt,UE,UN,UZ,relative.event.time)
#parr = PSIRpicker(MOD = -1,dvv = 0.15,snr_win = c(1,1),evlo,evla,evdp,stlo,stla,stel,dist,dt,t,t0 = 0,UE = UE,UN = UN,UZ = UZ,start.time.E,start.time.N,start.time.Z,event.time) 

 }
 
 
return(parr)
}





STALTA_P_arrival = function(x,dt,Ts,Tl,Tau,thre){
#t1 = 2.0, t2 = 20, thre = 0.6 is recommended. Tau=2;
  #dt = 1.0/tsp(x)[3]
  npts = length(x)
  
  kmax = floor((npts-Tl/dt)/(Tau/dt))

  sta <- vector(mode='numeric',length=kmax)
  lta <- vector(mode='numeric',length=kmax)
  sta.len <- floor(Ts/dt)
  lta.len <- floor(Tl/dt)
  L = 0
  p = 0
  for (k in 1:kmax){
    j = Tl/dt + (k-1)*Tau/dt
    sta[k] <- sqrt(sum(x[(j-(Ts/dt)):j]^2))
    lta[k] <- sqrt(sum(x[(j-(Tl/dt)):j]^2))
    if ((sta[k]/lta[k])> thre & L ==0){ 
    p <- ((j-(Ts/dt)))*dt
    L = L + 1
    }
  }
  return(p)
} 

AIC_Picker <- function(x,dt){
#dt <- 1.0/tsp(x)[3]
nr=length(x)
acc.dat=x

#Compute tst and ten for AIC computation
#tst is set at AI = 0.00, ten is set at AI=0.99
ai1 <- x[1:(nr-1)]^2
ai2 <- x[2:nr]^2
ai12 = (ai1+ai2)/2.*dt
ai = vector(mode='numeric',length=nr)
ai[1]=0
  for(i in 2:nr){
  ai[i] = ai[i-1]+ai12[i-1]
  }
nrmai = ai/ai[nr]
tst = which.min(abs(nrmai-0.00))*dt
ten = which.min(abs(nrmai-0.99))*dt

AIC_value<- vector(mode='numeric',length=nr)
var1<- vector(mode='numeric',length=nr)
var2<- vector(mode='numeric',length=nr)
AIC_value[]=0
  for(k in (tst/dt):(ten/dt)) {
  var1[k]= k*log(var(acc.dat[1:k]))
  var2[k]= (nr-k-1)*(log(var(acc.dat[(k+1):nr])))
  AIC_value[k]= var1[k]+var2[k]
  }
# min_idx= which.min(AIC_value)
# p=time.dat[min_idx]
AICts = ts(data=AIC_value[(tst/dt):(ten/dt)],delta=dt)
dAIC = Central.dif(AICts,dt, 5)
dAIC = c(rep(0,(tst/dt)),dAIC,rep(0,(nr-ten/dt)))
parr = which.max(dAIC)*dt-5
#print(parr)
#print(nr*dt)
#return(list(AIC_value,dAIC))
return(parr)
}

Central.dif <- function(velts,dt, order)
{
#dt <- 1.0/tsp(velts)[3]
npts <- length(velts)
accts = vector(mode="numeric",length=npts)
  if(order == 3){
    for(i in 2:(npts-1)){
    accts[i] = (velts[i+1]-velts[i-1])/(2*dt)
    }
  }
  if(order == 5){
    for(i in 3:(npts-2)){
    accts[i] = (velts[i-2]-8*velts[i-1]+8*velts[i+1]-velts[i+2])/(12*dt)
    }
  }
return(ts(data=accts,delta=dt))
}




