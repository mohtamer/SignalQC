integrate2V <- function(a) {
    if (!is.ts(a)) stop("Input array is not a regular time history!")
        dt <- 1.0/tsp(a)[3]
        npts <- length(a)
a1 <- array(a[1:(npts-1)])
a2 <- array(a[2:npts])
        v <- cumsum((a1 + a2)*dt*0.5)
        v <- c(0, v)
        ts(data=v, deltat=dt, start=start(a))
    }

integrate2D <- function(a, v) {
    if (!is.ts(a)) stop("Input acc is not a regular time history!")
        if (!is.ts(v)) stop("Input vel is not a regular time history!")
            dt <- 1.0/tsp(a)[3]
            if (tsp(v)[3] != tsp(a)[3]) stop ("Input vel does not have the same sampling frequency as the input acc!")
                npts <- length(a)
                if (length(v) != npts) stop ("Input vel does not have the same length as the input acc!")
a1 <- array(a[1:(npts-1)])
a2 <- array(a[2:npts])
v1 <- array(v[1:(npts-1)])
                    d <- cumsum((v1 + dt * (a1/3.0 + a2/6.0))*dt)
                    d <- c(0, d)
                    ts(data=d, deltat=dt, start=start(a))
                }

