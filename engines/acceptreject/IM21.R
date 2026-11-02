source("FAS.r")
source("SMOOTH.r")

IM21 = function(acc1,acc2,dt,nstr,parr,sarr,send,carr,cend){
#acc: acceleration time series, unit: G
 npts = min(length(acc1),length(acc2))

#A: Peak noise to PGA ratio, Column T
 pn1 = max(abs(acc1[nstr:round((parr-2)/dt)]))/max(abs(acc1))
 pn2 = max(abs(acc2[nstr:round((parr-2)/dt)]))/max(abs(acc2))
 colA = sqrt(pn1*pn2)

#B: P peak to PGA ratio
 pp1 = max(abs(acc1[round(parr/dt):round(sarr/dt)]))/max(abs(acc1))
 colB = pp1

#C: S peak to PGA ratio
 ps1 = max(abs(acc1[round(sarr/dt):round(send/dt)]))/max(abs(acc1))
 colC = ps1

#D: Coda peak to PGA ratio
 pc1 = max(abs(acc1[round(carr/dt):round(cend/dt)]))/max(abs(acc1))
 colD = pc1
 
#E: Peak off-coda to PGA ratio
 rcend = round(min(npts,(cend+10)/dt)); rcst = round((rcend*dt - 20)/dt)
 tra1 = max(abs(acc1[rcst:rcend]))/max(abs(acc1))
 tra2 = max(abs(acc2[rcst:rcend]))/max(abs(acc2))
 colE = sqrt(tra1*tra2)
# print(rcst)
# print(rcend)
# print(npts)
# print(colE)

#F: Peak window, 
 peakt = which.max(abs(acc1))*dt
 if(peakt<parr){
   peakw = 0+peakt/parr
 }else{
   if(peakt<sarr){
     peakw = 1+(peakt-parr)/(sarr-parr)
   }else{
     if(peakt<carr){
       peakw = 2+(peakt-sarr)/(carr-sarr)
     }else{
       peakw = 3+(peakt-carr)/(cend-carr)
     }
   }
 }
 colF = peakw

#G: 10%–20% BRACKETED DURATION RATIO, Db10_20. Column U, bracketedPGA_10_20, 
#Small difference exits between table and this calculation.
 vel1 = integrate2V(ts(acc1[round(nstr/dt):round(cend/dt)],delta=dt))
# vel2 = integrate2V(ts(acc2,delta=dt))
 PGV10 = max(abs(vel1))*0.1;PGV20 = max(abs(vel1))*0.2
 dif = abs(diff(sign(abs(vel1)-PGV10)))
 tePGV10 = max(which(dif==2))*dt #Last zero crossing of abs(acc) - 0.1PGV
 tsPGV10 = min(which(dif==2))*dt #First zero crossing of abs(acc) - 0.1PGV
 Db101 = tePGV10 - tsPGV10
 dif = abs(diff(sign(abs(vel1)-PGV20)))
 tePGV20 = max(which(dif==2))*dt #Last zero crossing of abs(vel) - 0.2PGV
 tsPGV20 = min(which(dif==2))*dt #First zero crossing of abs(vel) - 0.2PGV
 Db201 = tePGV20 - tsPGV20
 if(Db201 == 0){
 Db10_201 = 0
 }else{
 Db10_201 = Db101/Db201
 } 

 colG = Db10_201

#H: 5%–75% SIGNIFICANT VELOCITY DURATION, Ds575 by the end of CODA
 E1 <- rep(0,length(vel1))
 for (ii in 2:length(vel1)) {
 E1[ii] <- E1[ii-1]+(abs(vel1[ii])^2+abs(vel1[ii-1])^2)*(dt)/2
 }
 minE = 0.05;maxE = 0.75
 sE = which(E1>max(E1)*minE)[1]
 eE = which(E1>max(E1)*maxE)[1]
 Ds5751=(eE-sE)*dt;

 colH = Ds5751

#I: 5%–95% SIGNIFICANT VELOCITY DURATION, Ds595 by the end of CODA
 minE = 0.05;maxE = 0.95
 sE = which(E1>max(E1)*minE)[1]
 eE = which(E1>max(E1)*maxE)[1]
 Ds5951=(eE-sE)*dt;

 colI = Ds5951

#J: 5%–75% SIGNIFICANT ACCELERATION DURATION bt the end of CODA, Ds575
 acc1w = acc1[round(nstr/dt):round(cend/dt)]

 E1 <- rep(0,length(acc1w))
 for (ii in 2:length(acc1w)) {
 E1[ii] <- E1[ii-1]+(abs(acc1w[ii])^2+abs(acc1w[ii-1])^2)*(dt)/2
 }
 minE = 0.05;maxE = 0.75
 sE = which(E1>max(E1)*minE)[1]
 eE = which(E1>max(E1)*maxE)[1]
 Ds5751=(eE-sE)*dt;

 colJ = Ds5751

#K: 5%–95% SIGNIFICANT ACCELERATION DURATION, Ds595 using the end of CODA
 minE = 0.05;maxE = 0.95
 sE = which(E1>max(E1)*minE)[1]
 eE = which(E1>max(E1)*maxE)[1]
 Ds5951=(eE-sE)*dt;

 colK = Ds5951

#L: 5%–95% SIGNIFICANT ACCELERATION DURATION ENTIRE TIMESERIES, Ds595
 E1 <- rep(0,length(acc1))
 for (ii in 2:length(acc1)) {
 E1[ii] <- E1[ii-1]+(abs(acc1[ii])^2+abs(acc1[ii-1])^2)*(dt)/2
 }

 minE = 0.05;maxE = 0.95
 sE = which(E1>max(E1)*minE)[1]
 eE = which(E1>max(E1)*maxE)[1]
 Ds5951=(eE-sE)*dt;

 colL = Ds5951

#Save Column D-W in xls file
output = c(colA,colB,colC,colD,colE,colF,colG,colH/colJ,colI/colK,colK/colL)
#print(colH/colJ)
return(output)
}
