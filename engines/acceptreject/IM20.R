source("FAS.r")
source("SMOOTH.r")

IM20 = function(acc1,acc2,dt,parr){
#acc: acceleration time series, unit: G
 npts = min(length(acc1),length(acc2))

#A: Peak noise to PGA ratio, Column T
 pn1 = max(abs(acc1[1:round(parr/dt)]))/max(abs(acc1))
 pn2 = max(abs(acc2[1:round(parr/dt)]))/max(abs(acc2))
 colT = sqrt(pn1*pn2)

#B: AVERAGE TAIL RATIO, TRave, Column I
 Dtail = min(5,0.1*npts*dt)
 tra1 = mean(abs(acc1[(npts-round(Dtail/dt)):npts]))/max(abs(acc1))
 tra2 = mean(abs(acc2[(npts-round(Dtail/dt)):npts]))/max(abs(acc2))
 colI = sqrt(tra1*tra2)

#C: MAXIMUM TAIL RATIO, TRmax, Column J
 Dtail = min(2,0.1*npts*dt)
 trm1 = max(abs(acc1[(npts-round(Dtail/dt)):npts]))/max(abs(acc1))
 trm2 = max(abs(acc2[(npts-round(Dtail/dt)):npts]))/max(abs(acc2))
 colJ = sqrt(trm1*trm2)

#D: AVERAGE TAIL NOISE RATIO, TNRave, Column K
 tnra1 = tra1/mean(abs(acc1[1:round(parr/dt)]))
 tnra2 = tra2/mean(abs(acc2[1:round(parr/dt)]))
 colK = sqrt(tnra1*tnra2)
#print(colK)
#E: MAXIMUM TAIL NOISE RATIO, TNRmax, Column L, Check finied by this.
 tnrm1 = trm1/max(abs(acc1[1:round(parr/dt)]))
 tnrm2 = trm2/max(abs(acc2[1:round(parr/dt)]))
 colL = sqrt(tnrm1*tnrm2)

#F: MAXIMUM HEAD RATIO, HRmax. Column M, head_ratio, checked.
 thead = 1.0
 hrmax1 = max(abs(acc1[1:round(thead/dt)]))/max(abs(acc1))
 hrmax2 = max(abs(acc2[1:round(thead/dt)]))/max(abs(acc2))
 colM = sqrt(hrmax1*hrmax2)

#G: 10%–20% BRACKETED DURATION RATIO, Db10_20, . Column U, bracketedPGA_10_20, 
#Small difference exits between table and this calculation.
 PGA10 = max(abs(acc1))*0.1;PGA20 = max(abs(acc1))*0.2
 dif = abs(diff(sign(abs(acc1)-PGA10)))
 tePGA10 = max(which(dif==2))*dt #Last zero crossing of abs(acc) - 0.1PGA
 tsPGA10 = min(which(dif==2))*dt #First zero crossing of abs(acc) - 0.1PGA
 Db101 = tePGA10 - tsPGA10
 dif = abs(diff(sign(abs(acc1)-PGA20)))
 tePGA20 = max(which(dif==2))*dt #Last zero crossing of abs(acc) - 0.2PGA
 tsPGA20 = min(which(dif==2))*dt #First zero crossing of abs(acc) - 0.2PGA
 Db201 = tePGA20 - tsPGA20
 if(Db201 == 0){
 Db10_201 = 0
 }else{
 Db10_201 = Db101/Db201
 } 

 PGA10 = max(abs(acc2))*0.1;PGA20 = max(abs(acc2))*0.2
 #print(PGA10)
 #print(PGA20)
 dif = abs(diff(sign(abs(acc2)-PGA10)))
 #print(max(dif))
 tePGA10 = max(which(dif==2))*dt #Last zero crossing of abs(acc) - 0.1PGA
 #print(tePGA10)
 tsPGA10 = min(which(dif==2))*dt #First zero crossing of abs(acc) - 0.1PGA
 Db102 = tePGA10 - tsPGA10
 #print(Db102)
 dif = abs(diff(sign(abs(acc2)-PGA20)))
 tePGA20 = max(which(dif==2))*dt #Last zero crossing of abs(acc) - 0.2PGA
 tsPGA20 = min(which(dif==2))*dt #First zero crossing of abs(acc) - 0.2PGA
 Db202 = tePGA20 - tsPGA20
 #print(Db202)
 if(Db202 == 0){
 Db10_202 = 0
 }else{
 Db10_202 = Db102/Db202
 }
 colU = sqrt(Db10_201*Db10_202)

#H: 5%–75% SIGNIFICANT DURATION, Ds575, Column V, Ds575, checked
 E1 <- rep(0,length(acc1))
 for (ii in 2:length(acc1)) {
 E1[ii] <- E1[ii-1]+(abs(acc1[ii])^2+abs(acc1[ii-1])^2)*(dt)/2
 }
 minE = 0.05;maxE = 0.75
 sE = which(E1>max(E1)*minE)[1]
 eE = which(E1>max(E1)*maxE)[1]
 Ds5751=(eE-sE)*dt;

 E2 <- rep(0,length(acc2))
 for (ii in 2:length(acc2)) {
 E2[ii] <- E2[ii-1]+(abs(acc2[ii])^2+abs(acc2[ii-1])^2)*(dt)/2
 }
 minE = 0.05;maxE = 0.75
 sE = which(E2>max(E2)*minE)[1]
 eE = which(E2>max(E2)*maxE)[1]
 Ds5752=(eE-sE)*dt;

 colV = sqrt(Ds5751*Ds5752)

#I: 5%–95% SIGNIFICANT DURATION, Ds595, Column W, Ds595, checked.
 minE = 0.05;maxE = 0.95
 sE = which(E1>max(E1)*minE)[1]
 eE = which(E1>max(E1)*maxE)[1]
 Ds5951=(eE-sE)*dt;

 minE = 0.05;maxE = 0.95
 sE = which(E2>max(E2)*minE)[1]
 eE = which(E2>max(E2)*maxE)[1]
 Ds5952=(eE-sE)*dt;

 colW = sqrt(Ds5951*Ds5952)

#J: LOW-FREQUENCY PRE-EVENT FAS TO MAXIMUM SIGNAL FAS RATIO, FASpeLF/s.
#Maybe column D, but it does not match.
 temp = FAS.cal(ts(data=acc1,delta=dt), 3)
 FMAX <- 1/(2*dt); FMIN <- 1/(npts*dt)
 NS <- 0; KEY1<- -0.06 # Use smaler KEY1 value (negative) if the larger window is prefered.
 temp <- SMOOTH(temp[[2]], length(temp[[2]]), NS, temp[[3]], FMIN, FMAX, KEY1)
 FAS1 = temp[,1]; Freq = temp[,2]
 FASs_max1 = max(FAS1)

 n = ceiling(log2(length(acc1))+3)-ceiling(log2(round(parr/dt)))
 temp = FAS.cal(ts(data=acc1[1:round(parr/dt)],delta=dt), n)
 temp <- SMOOTH(temp[[2]], length(temp[[2]]), NS, temp[[3]], FMIN, FMAX, KEY1)
 FASn1 = temp[,1]
 FASpe_max_low1 = max(FASn1[Freq<0.1])

 FASpe_LF_s1 = FASpe_max_low1/FASs_max1

 temp = FAS.cal(ts(data=acc2,delta=dt), 3)
 temp <- SMOOTH(temp[[2]], length(temp[[2]]), NS, temp[[3]], FMIN, FMAX, KEY1)
 FAS2 = temp[,1]
 FASs_max2 = max(FAS2)

 n = ceiling(log2(length(acc2))+3)-ceiling(log2(round(parr/dt)))
 temp = FAS.cal(ts(data=acc2[1:round(parr/dt)],delta=dt), n)
 temp <- SMOOTH(temp[[2]], length(temp[[2]]), NS, temp[[3]], FMIN, FMAX, KEY1)
 FASn2 = temp[,1]
 FASpe_max_low2 = max(FASn2[Freq<0.1])

 FASpe_LF_s2 = FASpe_max_low2/FASs_max2

 colD = sqrt(FASpe_LF_s1*FASpe_LF_s2)

#K: LOW-FREQUENCY SIGNAL FAS TO MAXIMUM SIGNAL FAS RATIO, FASsLF/s
#Maybe column E, but nor sure.
 FASs_max1 = max(FAS1)
 FAS_max_low1 = max(FAS1[Freq<0.1])
 FASs_LF_s1 = FAS_max_low1/FASs_max1

 FASs_max2 = max(FAS2)
 FAS_max_low2 = max(FAS2[Freq<0.1])
 FASs_LF_s2 = FAS_max_low2/FASs_max2
 colE = sqrt(FASs_LF_s1*FASs_LF_s2) #Column E


#L: FOURIER AMPLITUDE RATIO, FASratio, I do not know which column.
 dfreq = diff(Freq)
 dfreq = c(dfreq,dfreq[length(dfreq)])
 FAS01_05 = sum(FAS1[Freq>0.1 & Freq<0.5]*dfreq[Freq>0.1 & Freq<0.5])/sum(dfreq[Freq>0.1 & Freq<0.5])
 FAS05_10 = sum(FAS1[Freq>0.5 & Freq<1.0]*dfreq[Freq>0.5 & Freq<1.0])/sum(dfreq[Freq>0.5 & Freq<1.0])
 FASratio1 = FAS01_05/FAS05_10

 FAS01_05 = sum(FAS2[Freq>0.1 & Freq<0.5]*dfreq[Freq>0.1 & Freq<0.5])/sum(dfreq[Freq>0.1 & Freq<0.5])
 FAS05_10 = sum(FAS2[Freq>0.5 & Freq<1.0]*dfreq[Freq>0.5 & Freq<1.0])/sum(dfreq[Freq>0.5 & Freq<1.0])
 FASratio2 = FAS01_05/FAS05_10

 colS = sqrt(FASratio1*FASratio2)  #Not clear the column, colS???

#M: MINIMUM SNR, SNRmin, Column F, snr_min

 SNR1 = FAS1/FASn1
 SNRmin1 = min(SNR1[Freq>0.1 & Freq<20])
 SNRmax1 = max(SNR1[Freq>0.1 & Freq<20])
 SNRave1 = sum(SNR1[Freq>0.1 & Freq<20]*dfreq[Freq>0.1 & Freq<20])/sum(dfreq[Freq>0.1 & Freq<20])
 SNR11 = sum(SNR1[Freq>0.1 & Freq<0.5]*dfreq[Freq>0.1 & Freq<0.5])/sum(dfreq[Freq>0.1 & Freq<0.5])
 SNR21 = sum(SNR1[Freq>0.5 & Freq<1.0]*dfreq[Freq>0.5 & Freq<1.0])/sum(dfreq[Freq>0.5 & Freq<1.0])
 SNR31 = sum(SNR1[Freq>1.0 & Freq<2.0]*dfreq[Freq>1.0 & Freq<2.0])/sum(dfreq[Freq>1.0 & Freq<2.0])
 SNR41 = sum(SNR1[Freq>2.0 & Freq<5.0]*dfreq[Freq>2.0 & Freq<5.0])/sum(dfreq[Freq>2.0 & Freq<5.0])
 SNR51 = sum(SNR1[Freq>5.0 & Freq<10.0]*dfreq[Freq>5.0 & Freq<10.0])/sum(dfreq[Freq>5.0 & Freq<10.0])


 SNR2 = FAS2/FASn2
 SNRmin2 = min(SNR2[Freq>0.1 & Freq<20])
 SNRmax2 = max(SNR2[Freq>0.1 & Freq<20])
 SNRave2 = sum(SNR2[Freq>0.1 & Freq<20]*dfreq[Freq>0.1 & Freq<20])/sum(dfreq[Freq>0.1 & Freq<20])
 SNR12 = sum(SNR2[Freq>0.1 & Freq<0.5]*dfreq[Freq>0.1 & Freq<0.5])/sum(dfreq[Freq>0.1 & Freq<0.5])
 SNR22 = sum(SNR2[Freq>0.5 & Freq<1.0]*dfreq[Freq>0.5 & Freq<1.0])/sum(dfreq[Freq>0.5 & Freq<1.0])
 SNR32 = sum(SNR2[Freq>1.0 & Freq<2.0]*dfreq[Freq>1.0 & Freq<2.0])/sum(dfreq[Freq>1.0 & Freq<2.0])
 SNR42 = sum(SNR2[Freq>2.0 & Freq<5.0]*dfreq[Freq>2.0 & Freq<5.0])/sum(dfreq[Freq>2.0 & Freq<5.0])
 SNR52 = sum(SNR2[Freq>5.0 & Freq<10.0]*dfreq[Freq>5.0 & Freq<10.0])/sum(dfreq[Freq>5.0 & Freq<10.0])

 colF = sqrt(SNRmin1*SNRmin2) #Column F
 colG = sqrt(SNRmax1*SNRmax2) #Column G
 colH = sqrt(SNRave1*SNRave2) #Column H
 colN = sqrt(SNR11*SNR12) #Column  N
 colO = sqrt(SNR21*SNR22) #Column O
 colP = sqrt(SNR31*SNR32) #Column P
 colQ = sqrt(SNR41*SNR42) #Column Q
 colR = sqrt(SNR51*SNR52) #Column R


#Save Column D-W in xls file
output = c(colD,colE,colF,colG,colH,colI,colJ,colK,colL,colM,colN,
           colO,colP,colQ,colR,colS,colT,colU,colV,colW)

return(output)
}
