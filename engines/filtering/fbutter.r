fbutter = function(Nnyq,df,fc,n,hp,AC){
          if(AC=="A"){
            f = df*((1:Nnyq)-1)         
            if(hp == "h"){
              tf = sqrt((f/fc)^(2*n)/(1.0+(f/fc)^(2*n)))
            }
            if(hp == "l"){
              tf = sqrt(1.0/(1.0+(f/fc)^(2*n)))
            }
          }
          if(AC=="C"){
            f = df*((1:Nnyq)-1) 
            tf = complex(real=1)   
            if(hp == "h"){
            fn = -complex(imaginary=abs(fc/f))             
              for(j in 1:n){
                tf = tf * (fn-exp(complex(imaginary=pi/(2.*n)*(2.*j-1+n))))
              }
              tf = 1/tf
            }
            if(hp == "l"){
              fn = complex(imaginary=abs(f/fc)) 
              for(j in 1:n){
                tf = tf * (fn-exp(complex(imaginary=pi/(2.*n)*(2.*j-1+n))))
              }
              tf = 1/tf
            }
          }
          return(tf)
          }  