ARF = function(accts, parr, Option, magnitude, hypdist)
{
# Update log:
#  - Option 4 was added. 2023/02/25
#  - Option 9 (NCM NN) added. 2025/01/09
#
# Option 1: STALTA (Cauzzi et al. 2016)
# Option 2: Pengfei
# Option 3: Bellagamba et al.
# Option 4: Multivariate regression (Kishida, 2023)
# Option 5: Generalized Regression (Kishida, 2023)
# Option 6: Bootstrap Forest (Kishida, 2023)
# Option 7: Boosted Tree (Kishida, 2023)
# Option 8: Support Vector (Kishida, 2023)
# Option 9: NCM NN (Neural network using IM1–IM30; Accept if NNscore > 100/360)

  print(paste("Option = ",Option,sep=""))
  if(Option == 1){
    Ts=1;Tau=0.2; Thr=5; #Tl=20;
    Tl = max(20,hypdist/8 + 7.5)
#Perform accept/reject argorithm until total energy reaches to 75%. 
    dt <- 1.0/tsp(accts)[3]
    winds = windows(parr,hypdist, magnitude,dt,length(accts))
    accts1 = accts
#    accts1 = ts(data = accts[round(winds[[1]]/dt):min(round(winds[[6]]/dt),length(accts))],delta = dt) The line does not work for late/early triggred records
    E = vector(mode='numeric',length=length(accts1))
    E = cumsum(accts1^2)

#    dt = 1.0/tsp(accts)[3]
#    E = vector(mode='numeric',length=length(accts))
#    E = cumsum(accts^2)
    maxE = 0.75
    iE = which(E>max(E)*maxE)
    if((iE[1]*dt) > Tl){
#      acctsin = ts(data=accts[1:iE[1]],delta=dt)
      acctsin = ts(data=accts1[1:iE[1]],delta=dt)
      out = acrjSTALTA(acctsin, Ts, Tl, Tau, Thr)
      accrejflg = out$flagout
      accrejind = out$peak
    }else{
      accrejflg = FALSE
      accrejind = -999
    }  
  }
  if(Option == 2){
    dt <- 1.0/tsp(accts)[3]
    x <- seq(0,length(accts)-1)*dt
    y <- as.numeric(accts)
    if(max(abs(y))==0 | dt > (1/40)){
      accrejflg = FALSE
      accrejind = 'NA'
    }else{
      acc_rej = label(x, y)
      accrejflg = as.logical(acc_rej)  # only return accrejflg, TRUE means accept, FALSE means reject
      accrejind = 'NA'  # 
    }
  }
  
  if(Option == 3){
    dt <- 1.0/tsp(accts)[3]
    winds = windows(parr,hypdist, magnitude,dt,length(accts))
    accts1 = ts(data = accts[round(winds[[1]]/dt):min(round(winds[[6]]/dt),length(accts))],delta = dt)
    parrx = parr - winds[[1]]
#	print(parrx)
    im20 = IM20(accts1,accts1,dt,parrx)
#    im20 = IM20(accts,accts,dt,parr)
    out1 = bella(im20)
#    if(out1[2]<=0.7 | is.nan(out1[2])==TRUE){#accelerometer 0.7, seismometer 0.4?
    if(out1[2]<=0.4 | is.nan(out1[2])==TRUE){	
      accrejflg = data.frame(out1[2],FALSE)
    }else{
      accrejflg = data.frame(out1[2],TRUE)
    }
    accrejind = 'NA'
  }
  
  if(Option == 4){
    dt <- 1.0/tsp(accts)[3]
    im20 = IM20(accts,accts,dt,parr)
    winds = windows(parr,hypdist, magnitude,dt,length(accts))
    if(round(winds[[6]]/dt) <= length(accts)){
      IM21s = IM21(accts,accts,dt,winds[[1]],winds[[2]],winds[[3]],winds[[4]],winds[[5]],winds[[6]])
	  IM30 = c(im20,IM21s)
#	  print("here")
#	  print(IM30)
      IMB = c(IM30[2],IM30[3],IM30[4],IM30[5],IM30[6],IM30[7],IM30[8],IM30[10],IM30[11],IM30[12],IM30[13],IM30[14],IM30[15],IM30[16],IM30[17],IM30[19],IM30[20],IM30[21],IM30[23],IM30[24],IM30[25],IM30[28],IM30[29],IM30[30],IM30[22])	
      accrejflg = MultScore(IMB,hypdist) 
      accrejind = 'NA'
    }else{
      accrejflg = data.frame(-1,FALSE) 
      accrejind = 'NA'
    }
  }

  if(Option == 5){
    dt <- 1.0/tsp(accts)[3]
    im20 = IM20(accts,accts,dt,parr)
    winds = windows(parr,hypdist, magnitude,dt,length(accts))
    if(round(winds[[6]]/dt) <= length(accts)){
      IM21s = IM21(accts,accts,dt,winds[[1]],winds[[2]],winds[[3]],winds[[4]],winds[[5]],winds[[6]])
      IM30 = c(im20,IM21s)
      invar = getInputMetadata(IM30)
      outGR = GRscore(invar)
      print(outGR)
      if (outGR > 0.2 & !is.nan(outGR)) {
        accrej = TRUE
      } else {
        accrej = FALSE
      }
      accrejflg = data.frame(outGR,accrej)
      accrejind = 'NA'
    }else{
      accrejflg = data.frame(-1,FALSE) 
      accrejind = 'NA'
    }
  }

  if(Option == 6){
    dt <- 1.0/tsp(accts)[3]
    im20 = IM20(accts,accts,dt,parr)
    winds = windows(parr,hypdist, magnitude,dt,length(accts))
    if(round(winds[[6]]/dt) <= length(accts)){
      IM21s = IM21(accts,accts,dt,winds[[1]],winds[[2]],winds[[3]],winds[[4]],winds[[5]],winds[[6]])
      IM30 = c(im20,IM21s)
      invar = getInputMetadata(IM30)
      outBF = BFscore(invar)
      if(outBF > 0.2){
        accrej = TRUE
      }else{
        accrej = FALSE
      }
      accrejflg = data.frame(outBF,accrej)
      accrejind = 'NA'
    }else{
      accrejflg = data.frame(-1,FALSE) 
      accrejind = 'NA'
    }
  }

  if(Option == 7){
    dt <- 1.0/tsp(accts)[3]
    im20 = IM20(accts,accts,dt,parr)
    winds = windows(parr,hypdist, magnitude,dt,length(accts))
    if(round(winds[[6]]/dt) <= length(accts)){
      IM21s = IM21(accts,accts,dt,winds[[1]],winds[[2]],winds[[3]],winds[[4]],winds[[5]],winds[[6]])
      IM30 = c(im20,IM21s)
      invar = getInputMetadata(IM30)
      outBT = BTscore(invar)
      if(outBT > 0.2){
        accrej = TRUE
      }else{
        accrej = FALSE
      }
      accrejflg = data.frame(outBT,accrej)
      accrejind = 'NA'
    }else{
      accrejflg = data.frame(-1,FALSE) 
      accrejind = 'NA'
    }
  }

  if(Option == 8){
    dt <- 1.0/tsp(accts)[3]
    im20 = IM20(accts,accts,dt,parr)
    winds = windows(parr,hypdist, magnitude,dt,length(accts))
    if(round(winds[[6]]/dt) <= length(accts)){
      IM21s = IM21(accts,accts,dt,winds[[1]],winds[[2]],winds[[3]],winds[[4]],winds[[5]],winds[[6]])
      IM30 = c(im20,IM21s)
      invar = getInputMetadata(IM30)
      outSV = SVscore(invar)
      if(outSV > 0.2){
        accrej = TRUE
      }else{
        accrej = FALSE
      }
      accrejflg = data.frame(outSV,accrej)
      accrejind = 'NA'
    }else{
      accrejflg = data.frame(-1,FALSE) 
      accrejind = 'NA'
    }
  }

if(Option == 9){
  dt <- 1.0/tsp(accts)[3]
  im20 = IM20(accts, accts, dt, parr)
  winds = windows(parr, hypdist, magnitude, dt, length(accts))

  if(round(winds[[6]]/dt) <= length(accts)){
    IM21s = IM21(accts, accts, dt,
                 winds[[1]], winds[[2]], winds[[3]],
                 winds[[4]], winds[[5]], winds[[6]])

    IM30 = c(im20, IM21s)

    # IMPORTANT: no getInputMetadata() here
    invar = as.list(setNames(as.numeric(IM30), paste0("IM", 1:30)))

    outNN = NNscore(invar)

    thrNN = 100/360
    accrej = outNN > thrNN
    accrejflg = data.frame(outNN, accrej)
    accrejind = 'NA'
  } else {
    accrejflg = data.frame(-1, FALSE)
    accrejind = 'NA'
  }
}


return(data.frame(accrejflg,accrejind))
}

MultScore = function(IMB,hypdist){
#  IMB = c(IM2,IM3,IM4,IM5,IM6,IM7,IM8,IM10,IM11,IM12,IM13,IM14,IM15,IM16,IM17,IM19,IM20,IM21,IM23,IM24,IM25,IM28,IM29,IM30,IM22)  
#  (IMB[14]>2) was modified for rejection criteria because low frequency can be dominated with long distance
#  IMB[19]<0.25 is revised to (IMB[19]<0.25&IMB[25]!=1), P-wave spiky can make PGA-S/PGA < 0.25 (03/17/2023)
#  if(IMB[1]<0.005|IMB[3]<20|IMB[4]<2.5|IMB[7]<1.5|IMB[9]<1|IMB[10]<4|IMB[11]<4|IMB[12]<2|IMB[13]<1.5|(IMB[14]>2&hypdist<100)|(IMB[19]<0.25&IMB[25]!=1)|IMB[22]<0.35){
#    print(IMB[1]<0.005)
#    print(IMB[3]<20)
#    print(IMB[4]<2.5)
##    print(IMB[7]<1.5) #dropped, meaning is unclear
##    print(IMB[7])
#    print(IMB[9]<1)
#    print(IMB[10]<4)
#    print(IMB[11]<4)
#    print(IMB[12]<2)
#    print(IMB[13]<1.5)
#    print(IMB[14]>2&hypdist<100)
#    print(IMB[19]<0.25&IMB[25]!=1)
#    print(IMB[22]<0.29)#revised from 0.35 to 0.29
#    print(IMB[22])
#    print((IMB[1]<0.005|IMB[3]<20|IMB[4]<2.5|IMB[9]<1|IMB[10]<4|IMB[11]<4|IMB[12]<2|IMB[13]<1.5|(IMB[14]>2&hypdist<100)|(IMB[19]<0.25&IMB[25]!=1)|IMB[22]<0.35))
  if(IMB[1]<0.005|IMB[3]<20|IMB[4]<2.5|IMB[9]<1|IMB[10]<4|IMB[11]<4|IMB[12]<2|IMB[13]<1.5|(IMB[14]>2&hypdist<100)|(IMB[19]<0.25&IMB[25]!=1)|IMB[22]<0.29){
    Score = -1  #Rejection criteria 
  }else{
#print(IMB[2]<0.01   )
#print(IMB[5]<0.00005)
#print(IMB[6]<0.0001 )
#print(IMB[8]<0.00001)
#print(IMB[15]<0.01  )
#print(IMB[16]<5     )
#print(IMB[17]<14    )
#print(IMB[18]<0.001 )
#print(IMB[20]<0.008 )
#print(IMB[21]<0.005 )
#print(IMB[21])
#print(IMB[22]>14    )
#print(IMB[23]>8     )
#print(IMB[24]<0.08  )
    if(IMB[2]<0.01|IMB[5]<0.00005|IMB[6]<0.0001|IMB[8]<0.00001|IMB[15]<0.01|IMB[16]<5|IMB[17]<14|IMB[18]<0.001|IMB[20]<0.008|IMB[21]<0.005|IMB[22]>14|IMB[23]>8|IMB[24]<0.08){
      Score = 1 #Accept criteria
    }else{
        Score = -0.1794  - 0.1717*log(IMB[4]) - 0.56*log(IMB[18]) + 0.1876*log(IMB[22]) - 0.6992*log(IMB[23]) 
    }
  }
  if(Score > 0.2){
    accrej = TRUE
  }else{
    accrej = FALSE
  }
  return(data.frame(Score,accrej))
}

acrjSTALTA = function(acc, Ts, Tl, Tau, Thr)
{
#Note: Based on Cauzzi et al. (2016) "Introducing the European Rapid Raw Strong-Motion Database"
#      Recommended parameters: Tl=20;Ts=1;dt=0.01;Tau=0.2; Thr=5 for seismometer, Thr=3 for accelerometer
#      Ts = 1.0s, Tl = 20s are used. If Peak is less than 5.0, the record is rejected. We keep this value as default. 
#      Thr is the threshold value, and 5.0 is used by Cauzzi et al. 2016.
#      For faster computation, Tau = 0.2s is recommended in this routine.
#      The reduction of "peak" is checked using 51 records, which results in the following output.
#      Tau  Min.Peak Mean.Peak Max.Peak
#      0.01      1.0       1.0      1.0
#       0.1     0.94      0.99      1.0
#       0.2     0.93      0.98      1.0  <- we can state that the same criteria by Cauzzi et al. 2016 is used...
#       0.5     0.88      0.95      1.0
#       1.0     0.79      0.93      1.0
      dt = 1.0/tsp(acc)[3]
      npts = length(acc)
      kmax = floor((npts-Tl/dt)/(Tau/dt))
      amp = abs(acc)
      out = vector(mode='numeric', length=kmax)
      if(kmax>0){
        for (k in 1:kmax){
          j = Tl/dt + (k-1)*Tau/dt
          out[k] = mean(amp[(j-(Ts/dt)):j])/mean(amp[(j-(Tl/dt)):j])
        }
        out[is.na(out)] <- 0
        peak=max(out)
        if(peak > Thr){
          flagout = TRUE   #TRUE is "Accept"
        }else{
          flagout = FALSE  #FALSE is "Reject"
        }
      }else{
        flagout = FALSE
        peak=-999
      }
      return(data.frame(flagout,peak))
}
