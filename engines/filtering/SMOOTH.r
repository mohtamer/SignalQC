SMOOTH <- function(Y, NN, NS, FS, FMIN,FMAX,KEY1)
{
#CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC
#C     SUBROUTINE TO SMOOTH A SPECTRUM
#C
#C     Y     - INPUT SPECTRUM
#C     NN    - NO OF POINTS IN THE SPECTRUM TO BE SMOOTHED
#C     NS    - NO OF NEIGHBORING POINTS INCLUDED THE SMOOTHING PROCESS (MUST BE ODD)
#C         POSITIVE FOR RECTANGULAR SMOOTHING (SIMPLE AVERAGE)
#C         NEGATIVE FOR TRIANGULAR SMOOTHING (WEIGHTED AVERAGE)
#C         FOR WEIGHTED AVERAGE, ABS(NS) < 501.
#C     FS    - INPUT FREQUENCY ARRAY
#C     FMIN  - MINIMUM FREQUNECY USED IN SMOOTHING
#C     FMAX  - MAXIMUM FREQUNECY USED IN SMOOTHING
#C     KEY1 IS THE KEY FOR SMOOTHING
#C          IF KEY1 .GE. 0 DATA ARE SMOOTHED USING CONSTANT DF
#C          IF KEY1 .LT. 0 DATA ARE SMOOTHED USING CONSTANT LOG10(DF)
#C                        AND FLOAT(IABS(KEY1)) = LOG10(DF)
#C
#C NOTE - ARRAY Y SHOULD CONTAIN MORE THAN (NN + (NS/2) DATA POINTS
#C      - NN .GT. NS
#CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC
#C     S     - SMOOTHED SPECTRUM
#C     F     - OUTPUT FREQUENCY ARRAY FOR SMOOTHED SPECTRUM
#C     RAWVAR = SUM OVER SMOOTHING I OF (SUM OVER PTS SMOOTHED J OF
#C     RNDF - NOT USED IN THE FUNCTION
#C NOTE - THIS PROGRAM WAS DEVELOPED BY WALT SILVA AND BOB DARRAGH IN FORTRAN,
#C        AND INPLIMENTED INTO R BY TADAHIRO KISHIDA.
if( KEY1>0 ) {
 W <- matrix(0, nrow=abs(NS), ncol=1)
 S <- matrix(0, nrow=NN, ncol=1)
 F <- matrix(0, nrow=NN, ncol=1)
} 
MAXNS=100
#print(KEY1)

          RAWVAR = 0.0
          RNDF = 0
    if ( KEY1<=0 ) {
        DF = FS[2] - FS[1]
        FINC = abs(KEY1)
        FSTART = log10(max(FMIN,0.001))
        temp <- SMUTH2(Y, NN, FMAX, FSTART, FINC, DF, FS)
        S <- temp[,1]
        NFS <- length(S) 
        PLACE <- temp[,2]
        for (I in 1:NFS){
            F[I] = FS[ PLACE[I] ]
        }
    } else {
        if ( NS > MAXNS) {
             print ('NS IS TOO LARGE, AUTO RESET TO ')
             print (MAXNS)
             NS = MAXNS
        }
        if (NS %% 2 == 0) {
        stop ("EXITING IN SMOOTH: NS MUST BE ODD!!")
        }
        NNS=(abs(NS)+1)/2
        NP=NN+abs(NS)/2
        NSAB = abs(NS)
#C     DETERMINE WEIGHTS FOR EITHER RECTANGULAR OR TRIANGULAR SMOOTHING
       if (NS > 0 ) {
#C        RECTANGULAR SMOOTHING
             for (I in 1:NSAB){
             W[I]=1./NSAB
             }
        }else{
#C
#C        TRIANGULAR SMOOTHING
#C
            ISUM=0
            for (I in 1:NNS-1) {
            ISUM=ISUM+I
            }
            MS=NNS+2*ISUM
            for (I in 1:NNS) {
            J=NSAB-I+1
            W[I]=I/MS
            W[J]=W[I]
            }
        }
#C
#C         FIND MAXIMUM WT FOR NORMALIZING SMOOTHING (NS IS ODD SO THIS WORKS)
#C
#        WMAX = 0.0
#        for (J in 1:NSAB) {
#            if (W[J]>WMAX) {
#            WMAX=W[J]
#            }
#        }
#C     SMOOTH SPECTRUM
        for (I in 1:NN) {
            S[I] = Y[I]*W[NNS]
            for (J in 1:(NNS-1)) {
                if ((I-J)<1) {
                A=0
                }else{
                A=Y[I-J]
                }
                if ((I+J)>NP) {
                B=0
                }else{
                B=Y[I+J]
                }
                S[I] = S[I] + W[NNS-J]*A + W[NNS+J]*B
            }
        }
     F = FS
     }
#C
#C     CHECK ARRAY BOUNDS FOR SMOOTHING
#C
out.SMOOTH <- data.frame(S,F)
return(out.SMOOTH)
} #close function
#------------------------------------------------------------------------ funtion SMUTH2 -------------------------------------
SMUTH2 <- function(FFT, NPTS, FMAX, FL1, DFL, DF, F){
#C     FFT     - INPUT SPECTRUM
#C     NPTS    - NO OF POINTS IN THE SPECTRUM TO BE SMOOTHED
#C     FMAX    - MAXIMUM FREQUNECY USED IN SMOOTHING
#C     FL1     - MINIMUM FREQUNECY USED IN SMOOTHING IN LOG10 SCALE
#C     DFL     - FREQUENCY WINDOW SIZE IN LOG10 SCALE
#C     DF      - NOT USED IN FUNCTION
#C     F       - INPUT FREQUENCY ARRAY
 AVG <- matrix(0, nrow=NPTS, ncol=1)
 PLACE <- matrix(0, nrow=NPTS, ncol=1) 
      IAV = 0
      FL  = FL1 - DFL
      FBU = 10.^FL1
#C
      while (FBU < FMAX) {
      IAV = IAV + 1
      FL  = FL + DFL
      FBL = 10.^(FL - 0.5*DFL)
      FBU = 10.^(FL + 0.5*DFL)

     
#C**   FIND INDICES CORRESPONDING TO LOWER AND UPPER BOUNDS.
      INL = floor(FBL/DF) + 2
      if ((INL-2)*DF == FBL) {INL = INL -1}
      if (INL < 2) {INL = 2}
      INU = floor(FBU/DF) + 1
      if (INU > NPTS) {INU = NPTS}
      if (INU < INL) {
        AVG[IAV] = 9.99
      }

      AVG[IAV] = 0
      PLACE[IAV] = (INU - INL) / 2 + INL
#      if(floor(PLACE[IAV]) == floor(PLACE[IAV-1])){   #added
#      IAV = IAV - 1                                  #added
#      }else{                                         #added
         for (J in INL:INU){
         AVG[IAV] = log10(FFT[J]) + AVG[IAV]
         }
         AVG[IAV] = AVG[IAV]/(INU-INL+1)
 #     }                                              #added
      } # end of while
  
K = 0                                                   #added

for (I in 1:IAV) {                                      #added
   if(is.finite(AVG[I]) == TRUE){                      #added
   K = K + 1                                            #added
   AVG[K] = 10^AVG[I]                                   #added
   PLACE[K] = PLACE[I]                                  #added
   }                                                    #added
}                                                       #added
IAV = K                                                 #added

    out.SMUTH2 <- data.frame(AVG[1:IAV],PLACE[1:IAV])
    names(out.SMUTH2) <- c("AVG","PLACE")
    return(out.SMUTH2)
} 


#------------------------------------------------------------------------ funtion LOCATE -------------------------------------
LOCATE <- function(XX,N,X)
{
#C ---
#C --- LOCATE AN X VALUE IN AN ARRAY XX BETWEEN XX(J) AND XX(J+1)
#C ---
      JL=0
      JU=N+1
  while (JU-JL>1) {
    JM <-(JU+JL)/2
      if(((XX[N]-XX[1])*(X-XX[JM]))>0) {
        JL <- JM
      } else {
        JU <- JM
      }
  }
return(JL)
}

