# v12 changes output reporting only; classifier equations are unchanged.
# New application version of Main AcceptReject_v10.R. Original methods retained.
# Usage: Rscript "Main AcceptReject_v12_app.R" config.json
start.time <- Sys.time()
args <- commandArgs(trailingOnly=TRUE)
if (length(args)!=1) stop("Usage: Rscript Main AcceptReject_v12_app.R config.json")
script_arg <- grep("^--file=",commandArgs(),value=TRUE)[1]
engine_dir <- dirname(normalizePath(sub("^--file=","",script_arg),winslash="/",mustWork=TRUE))
app_lib <- file.path(engine_dir,"..","..","runtime","R-library")
if (dir.exists(app_lib)) .libPaths(c(normalizePath(app_lib,winslash="/"),.libPaths()))
required <- c("jsonlite","reticulate","openxlsx","seewave")
missing <- required[!vapply(required,requireNamespace,logical(1),quietly=TRUE)]
if (length(missing)) stop(paste("Missing R packages:",paste(missing,collapse=", "),". Use the application dependency setup."))
config <- jsonlite::fromJSON(normalizePath(args[1],winslash="/",mustWork=TRUE))
config$input_hdf5 <- normalizePath(config$input_hdf5,winslash="/",mustWork=TRUE)
dir.create(config$output_dir,recursive=TRUE,showWarnings=FALSE)
config$output_dir <- normalizePath(config$output_dir,winslash="/",mustWork=TRUE)
setwd(engine_dir)
Sys.setenv(TZ="UTC",PYTHONIOENCODING="utf-8",RETICULATE_PYTHON=config$python_executable)
options(digits.secs=9,warn=1)
library(reticulate)
library(openxlsx)
use_python(config$python_executable,required=TRUE)
source_python("bella_v2_app.py")
source_python("getInputMetadata.py")
source_python("BFscore.py")
source_python("BTscore.py")
source_python("GRscore.py")
source_python("SVscore.py")
source_python("NNscore.py")
source_python("hdf5_reader_v9_app.py")
source("ARF.R")
source("IM20.R")
source("IM21.R")
source("windows.r")
source("Integrate2VD.r")
source("COHERENCE.r")
source("app_support_v2.R")
save_figures <- isTRUE(config$save_figures)
resume <- isTRUE(config$resume)
baipa <- config$output_dir
dir.create(file.path(baipa,"Figure"),showWarnings=FALSE)
h5files <- config$input_hdf5
ii <- 1
outfilepath <- file.path(baipa,paste0(basename(h5files),"meta.xlsx"))
outfilepath_csv <- file.path(baipa,paste0(basename(h5files),".meta.csv"))
checkpoint_dir <- file.path(baipa,"checkpoints",app_checkpoint_key(h5files,"__file__",list(),save_figures))
dir.create(checkpoint_dir,recursive=TRUE,showWarnings=FALSE)
if (!resume) {
  previous <- list.files(checkpoint_dir,pattern="[.]rds$",full.names=TRUE)
  if (length(previous)) unlink(previous)
}
app_export()
# --- helpers ---
ensure_len <- function(x, n, fill = NA) {
  if (is.null(x)) return(rep(fill, n))
  length(x) <- n
  x
}

safe_get <- function(lst, name, idx, fill = NA) {
  x <- lst[[name]]
  if (is.null(x)) return(rep(fill, length(idx)))
  v <- x[idx]
  if (is.null(v)) rep(fill, length(idx)) else v
}

events <- hdf5_eventlist(h5files)
for (iii in seq_along(events)) {
      eventid = as.character(events[[iii]]$event_id)
      app_event_traces <- character()
      tryCatch({
      eqlat = as.numeric(events[[iii]]$latitude)
      eqlon = as.numeric(events[[iii]]$longitude)
      eqdep = as.numeric(events[[iii]]$depth)
      eqtime = events[[iii]]$origin_time
      eqtime = app_time(eqtime)
      eventid = events[[iii]]$event_id


      meta=list()
      stnchn = hdf5_station_channels(h5files[ii], eventid)
      nfiles = length(stnchn)
      app_event_traces <- vapply(stnchn, function(row) as.character(row[[4]]), character(1))
      app_status(eventid, app_event_traces, "pending")
      if (!is.finite(as.numeric(events[[iii]]$magnitude)) || is.na(eqtime)) {
        app_status(eventid, app_event_traces, "skipped", "missing_event_magnitude_or_origin_time")
        next
      }
      if (nfiles <= 9) {
        app_status(eventid, app_event_traces, "skipped", "requires_at_least_10_traces_for_original_9_record_origin_correction")
        next
      }

      #Only run the code if the event includes time series > 9.
      if(nfiles > 9){
        ortcor = 0 #Origin time correction flag
        parr.off = 0   

        network = vector();station = vector();location = vector();trace.id = vector();
        component = vector();use = vector();rec_id = vector();hyp = vector(); mag = vector(); 
        stlat = vector();stlon = vector();stelv = vector();delta = vector();recstime = vector();recetime = vector(); epi = vector()
        for(j in seq_len(nfiles)){
          stnchn0 = stnchn[[j]]
          station[j] = stnchn0[[1]]
          component[j] = stnchn0[[2]]
          network[j] = as.character(stnchn0[[3]])
          location[j] = ""
          trace.id[j] = as.character(stnchn0[[4]])

          acc_error <- ""
          acc <- tryCatch(hdf5_acceleration(h5files[ii], eventid, station[j], component[j], trace.id[j]),
                          error=function(e) {acc_error <<- conditionMessage(e); NULL})
          if (is.null(acc) || length(acc) == 0) {
            warning(paste("Skipping empty trace in metadata phase:", station[j], component[j], trace.id[j]))

            # Fill everything else with NA to keep vector lengths consistent
            recstime[j] = NA
            recetime[j] = NA
            delta[j] = NA
            rec_id[j] = j
            use[j] = FALSE
            mag[j] = NA
            stlat[j] = NA
            stlon[j] = NA
            stelv[j] = NA
            hyp[j] = NA
            epi[j] = NA
            app_status(eventid, trace.id[j], "skipped", paste("invalid_waveform", acc_error))

            next
          }

          acc.meta <- tryCatch(hdf5_acceleration_meta(h5files[ii], eventid, station[j], component[j], trace.id[j]),
                               error=function(e) {acc_error <<- conditionMessage(e); NULL})
          if (is.null(acc.meta)) {
            recstime[j] <- NA; recetime[j] <- NA; delta[j] <- NA; rec_id[j] <- j; use[j] <- FALSE
            mag[j] <- NA; stlat[j] <- NA; stlon[j] <- NA; stelv[j] <- NA; hyp[j] <- NA; epi[j] <- NA
            app_status(eventid, trace.id[j], "skipped", paste("invalid_metadata", acc_error)); next
          }
          location[j] <- as.character(acc.meta$location)
          network[j] <- as.character(acc.meta$network)
          recstime[j] = acc.meta$record_start_time
          recetime[j] = acc.meta$record_end_time
          delta[j] = as.numeric(1 / acc.meta$sampling_rate)
          rec_id[j] = j
          use[j] = TRUE
          event_mag <- suppressWarnings(as.numeric(events[[iii]]$magnitude))

# Missing event magnitude is reported above; no invented magnitude is used.

mag[j] = event_mag
          stlat[j] = acc.meta$station_lat
          stlon[j] = acc.meta$station_lon
          stelv[j] = acc.meta$station_elevation_m
          hyp[j] = acc.meta$hypocentral_distance_km
          epi[j] = acc.meta$epicentral_distance_km
        }

        mseedfiles = rep("NA", length(rec_id))
        lengths_list <- lengths(list(
          rec_id = rec_id,
          eventid = rep(eventid, length(rec_id)),
          mseedfiles = mseedfiles,
          trace.id = trace.id,
          network = network,
          station = station,
          location = location,
          component = component,
          use = use,
          hyp = hyp,
          mag = mag,
          eqtime = rep(eqtime, length(rec_id)),
          recstime = recstime,
          recetime = recetime,
          eqlat = rep(eqlat, length(rec_id)),
          eqlon = rep(eqlon, length(rec_id)),
          eqdep = rep(eqdep, length(rec_id)),
          stlat = stlat,
          stlon = stlon,
          stelv = stelv,
          delta = delta
        ))
        print(lengths_list)

        # Make sure every vector has the same length as rec_id, and correct types
        n <- length(rec_id)
        network   <- ensure_len(network,   n, "")
        station   <- ensure_len(station,   n, "")
        location  <- ensure_len(location,  n, "")
        trace.id  <- ensure_len(trace.id,  n, "")
        component <- ensure_len(component, n, "")
        use       <- ensure_len(use,       n, FALSE)
        hyp       <- as.numeric(ensure_len(hyp, n, NA_real_))
        mag       <- as.numeric(ensure_len(mag, n, NA_real_))
        recstime  <- ensure_len(recstime,  n, NA)
        recetime  <- ensure_len(recetime,  n, NA)
        stlat     <- as.numeric(ensure_len(stlat,  n, NA_real_))
        stlon     <- as.numeric(ensure_len(stlon,  n, NA_real_))
        stelv     <- as.numeric(ensure_len(stelv,  n, NA_real_))
        delta     <- as.numeric(ensure_len(delta,  n, NA_real_))
        mseedfiles <- rep("NA", n)

        ts.info = data.frame(
          rec_id,rep(eventid,length(rec_id)),mseedfiles,trace.id,network,station,location,component,
          use,hyp,mag,rep(eqtime,length(rec_id)),recstime,recetime,rep(eqlat,length(rec_id)),
          rep(eqlon,length(rec_id)),rep(eqdep,length(rec_id)),stlat,stlon,stelv,delta,
          stringsAsFactors = FALSE
        )
        names(ts.info)[c(2,12,15:17)] = c("eventid","eqtime","eqlat","eqlon","eqdep")
        ts.info$epidis <- as.numeric(ensure_len(epi,n,NA_real_))
        valid_meta <- is.finite(ts.info$hyp) & is.finite(ts.info$delta) & ts.info$delta > 0 &
                      !is.na(app_time(ts.info$recstime)) & !is.na(app_time(ts.info$recetime))
        app_status(eventid, ts.info$trace.id[!valid_meta], "skipped", "missing_distance_coordinates_depth_elevation_or_record_times", only_pending=TRUE)
        ts.info <- ts.info[valid_meta,,drop=FALSE]
        if (nrow(ts.info) <= 9) {
          app_status(eventid, app_event_traces, "skipped", "fewer_than_10_valid_traces_for_original_origin_correction", only_pending=TRUE)
          next
        }
        # Keep exact normalized timestamp strings in exports. R parses them only for arithmetic.

        # Minor guard: if hyp column still has bad types, coerce safely
        ts.info$hyp <- suppressWarnings(as.numeric(ts.info$hyp))

        #Remove unrelated time series using S-wave arrival
        Sarr_est = ts.info$eqtime + ts.info$hyp/6 + ts.info$hyp/8
        rst2sarr = as.numeric(difftime(Sarr_est,app_time(ts.info$recstime),units="secs"))
        rst2ret = as.numeric(difftime(app_time(ts.info$recetime),app_time(ts.info$recstime),units="secs"))
        ts.info$use = rst2sarr > 0 & rst2ret - rst2sarr > 0

        #Start here.
        nrec = file.select(ts.info,io=0)

        ############ Added Lines: Origin Time Correction
        refdis = c(2.3, 17.4, 100, 200, 300, 700); refM = c(2,3,4,5,6,8)
        refdist = min(120, approx(refM, refdis, ts.info$mag[1], method = "linear", n = 50, rule = 1)$y)
        if (!is.finite(refdist)) {
          app_status(eventid, app_event_traces, "skipped", "magnitude_outside_original_origin_correction_interpolation_range_2_to_8", only_pending=TRUE)
          next
        }
        meddist = median(ts.info$hyp)/2
        df = data.frame(ts.info$rec_id,ts.info$hyp,abs(ts.info$hyp-refdist),abs(ts.info$hyp-meddist))    
        rsn1 = df[order(df[,3]),][1:9,1:2]
        rsn2 = df[order(df[,4]),][1:9,1:2]
        rsn12 = data.frame(rsn1,rsn2)
        travelt = vector(mode="numeric",length=9)
        parr.offs = vector(mode="numeric",length=9)

        #jj loops for the selected records based on ref distance and median distance
        for(jj in 1:2){#jj=1 
          for(j in 1:9){#j=1
            subts.info = subset(ts.info,ts.info$rec_id == rsn12[j,jj*2-1])
            hypdist=ts.info$hyp[ts.info$rec_id == rsn12[j,jj*2-1]]
            magnitude = ts.info$mag[ts.info$rec_id == rsn12[j,jj*2-1]]
            str_eqtime = strptime(ts.info$eqtime[ts.info$rec_id == rsn12[j,jj*2-1]], "%Y-%m-%d %H:%M:%OS")
            str_recstime = strptime(ts.info$recstime[ts.info$rec_id == rsn12[j,jj*2-1]], "%Y-%m-%d %H:%M:%OS")
            pvel = 6.5
            parr = as.numeric(difftime(str_eqtime,str_recstime,units="secs"))+(hypdist/pvel)

            acc = hdf5_acceleration(h5files[ii], subts.info$eventid, subts.info$station, subts.info$component,subts.info$trace.id)
            dt = subts.info$delta; npts = length(acc)
            accts = ts(data=acc,delta=dt)

            AR <- tryCatch(ARF(accts, parr, Option=1,magnitude,hypdist),error=function(e) {
              message("Origin reference unavailable: ",conditionMessage(e)); data.frame(FALSE,-999)
            })                    
            ARthr = ifelse(magnitude <=4.5, 2.0, 2.5)
            if(isTRUE(AR[[2]]>ARthr & parr < (npts*dt-10) & parr > 0)){
              npserc = max(1,round((parr-20)/dt))
              peakt = (npserc-1 + which.max(abs(acc[npserc:length(acc)])))*dt
              subacc = accts[npserc:round(min(parr+50,peakt)/dt)]/max(abs(accts[npserc:(min(npts,round((parr+50)/dt)))]))
              subt = (npserc:round(min(parr+50,peakt)/dt))*dt
              sub1 = cumsum(subacc^2)/max(cumsum(subacc^2))
              sub2 = cumsum(abs(subacc)^0.5)/max(cumsum(abs(subacc)^0.5))
              sub3 = cumsum(abs(subacc)^1)/max(cumsum(abs(subacc)^1))
              t1 = subt[which.min(abs(sub1-0.04))]
              t2 = subt[which.min(abs(sub2-0.04))]
              t3 = subt[which.min(abs(sub3-0.04))]
              parr1 = (t1+t2+t3)/3
              travelt[j] = as.numeric(difftime(subts.info$eqtime,subts.info$recstime,units="secs"))+(t1+t2+t3)/3
              parr.offs[j] = parr1 - parr
            }else{
              parr.offs[j] = -999
            }
          }
          if(jj == 1){
            t_parr = data.frame(rsn12[,jj*2],travelt,parr.offs)
          }else{
            t_parr = rbind(t_parr,data.frame(rsn12[,jj*2],travelt,parr.offs))
          }
        }

        parr.off = ifelse(max(t_parr$parr.offs)==-999,0,median(t_parr$parr.offs[t_parr$parr.offs != -999]))

        ########################################## Processing per 3-component record #####################
        for(j in seq_len(nrec)){#1:nrec
          if (app_cancelled()) break
          selected_ids <- file.select(ts.info,io=1,K=j)
          acfilename <- as.numeric(selected_ids)
          group_traces <- ts.info$trace.id[match(acfilename, ts.info$rec_id)]
          if (!isTRUE(attr(selected_ids,"valid"))) {
            app_status(eventid, group_traces, "skipped", "requires_unambiguous_aligned_three_component_group_with_equal_sample_rates")
            next
          }
          group_key <- app_checkpoint_key(h5files[ii], eventid, as.list(group_traces), save_figures)
          checkpoint_path <- file.path(checkpoint_dir,paste0(group_key,".rds"))
          figure_path <- if (save_figures) file.path(config$output_dir,"Figure",paste0(app_filename(eventid),"_",group_key,".png")) else ""
          if (resume && file.exists(checkpoint_path) && (!save_figures || file.exists(figure_path))) {
            previous <- readRDS(checkpoint_path)
            for (row in seq_len(nrow(previous$output))) {
              app_status(eventid, previous$output$trace_id[row], if (isTRUE(as.logical(previous$output$acrjfinal3[row]))) "accepted" else "rejected", "resumed", previous$figure_path)
            }
            next
          }
          tryCatch({
          #only process when time series are 3 components
          if(length(acfilename) == 3){
            if (save_figures) png(figure_path,width=1800,height=1400,res=150) else pdf(file=NULL)
            par(mfrow = c(3, 1), pty = "m", mar=c(3,4,4,3),las=1)
            for(jj in 1:3){#jj=1
              print(jj)
              rec_id = acfilename[jj]
              subts.info = ts.info[ts.info$rec_id == rec_id,]

              acc = hdf5_acceleration(h5files[ii], subts.info$eventid, subts.info$station, subts.info$component,subts.info$trace.id)
              print(paste(j," over ",nrec,sep=""))

              rawpga = max(abs(acc))
              accuniq = length(unique(acc))
              count = length(acc)
              rejectreason = NA
              hypdist=subts.info$hyp
              magnitude = subts.info$mag
              str_eqtime = strptime(subts.info$eqtime, "%Y-%m-%d %H:%M:%OS")
              str_recstime = strptime(subts.info$recstime, "%Y-%m-%d %H:%M:%OS")

              #When the record information is missing (2023/09/14)
              if(is.na(hypdist)) stop("Missing hypocentral distance")
              if(is.na(magnitude)) stop("Missing magnitude")
              if(is.na(str_eqtime)) stop("Missing event origin time")

              dt = as.numeric(subts.info$delta)
              network = subts.info$network
              station = subts.info$station
              location = subts.info$location
              component = subts.info$component

              accts = ts(data=acc,delta=dt)
              #Store accts for coherence calculation later
              if(jj == 1){accts1 = accts}
              if(jj == 2){accts2 = accts}
              if(jj == 3){accts3 = accts}  

              recid = paste(network,".",station,".",location,".",component,sep="")
              plot((1:length(accts))*dt,accts,typ="l",xlab = "Time (s)",ylab = "Acc (m/s2)")
              title(paste(subts.info$eventid,subts.info$network,subts.info$station,subts.info$component,sep="."))
              acrj = vector(mode='numeric',length=8)
              score = vector(mode='numeric',length=8)
              acrj[] = NA;score[] = NA

              #save meta for output (placeholders before full run)
              pvel = 6.5
              parr = as.numeric(difftime(str_eqtime,str_recstime,units="secs"))+(hypdist/pvel) + min(0,max(parr.off,-7))
              for (k in 1:8) {
                meta[[paste0("acrj",k)]][rec_id]  <- as.numeric(NA)
                meta[[paste0("score",k)]][rec_id] <- as.numeric(NA)
              }
              meta$RSN[rec_id] = rec_id 
              meta$filename[rec_id] = basename(h5files[ii])
              meta$rec_id[rec_id] = recid
              meta$event_id[rec_id] = subts.info$eventid
              meta$timeStep[rec_id] = as.numeric(dt)
              meta$rawpga[rec_id] <- as.numeric(rawpga)
              meta$count[rec_id] <- as.numeric(count)
              meta$accuniq[rec_id] <- as.numeric(accuniq)
              meta$parr[rec_id] <- as.numeric(parr)
              meta$network[rec_id] <- subts.info$network
              meta$station[rec_id] <- subts.info$station
              meta$location[rec_id] <- subts.info$location
              meta$component[rec_id] <- subts.info$component
              meta$latitude.s[rec_id] <- subts.info$stlat
              meta$longitude.s[rec_id] <- subts.info$stlon
              meta$elevation.s[rec_id] <- subts.info$stelv
              meta$depth.s[rec_id] <- 0
              meta$azimuth.s[rec_id] <- ifelse(substr(subts.info$component,3,3)=="N",0,ifelse(substr(subts.info$component,3,3)=="E",90,-999))
              meta$dip.s[rec_id] <- ifelse(substr(subts.info$component,3,3)=="Z",90,0)
              meta$starttime.s[rec_id] <- subts.info$recstime
              meta$calib[rec_id] <- 1  
              meta$origintime[rec_id] = as.character(events[[iii]]$origin_time)
              meta$magnitude[rec_id] <- subts.info$mag
              meta$hypdis[rec_id] <- subts.info$hyp
              meta$epidis[rec_id] <- subts.info$epidis
              meta$trace_id[rec_id] <- subts.info$trace.id
              meta$coh[rec_id] <- as.numeric(-999)
              meta$cohL[rec_id] <- as.numeric(-999)
              meta$cohH[rec_id] <- as.numeric(-999)
              meta$acrjfinal3[rec_id] <- NA

              #Reject when resolution is low or short duration
              if(accuniq<=9 | (count*dt)<=40){
                acrjfinal = FALSE
                if((count*dt)<=40){
                  rejectreason = "short_record"
                }else{
                  rejectreason = "low_resolution"
                }
                meta$rejectreason[rec_id] = rejectreason
                meta$acrjfinal[rec_id] <- as.numeric(acrjfinal)
              }

              if(accuniq>9 & (count*dt)>40){
                abline(v=parr,col=2)

                # Accept/Reject Run
                acrj = vector(mode='numeric',length=8)
                score = vector(mode='numeric',length=8)
                acrj[] = NA;score[] = NA

                if(parr < 0 |parr > (count*dt-20)){
                  acrj[] = -1
                  legend("topright","Reject - Late/Early trigger")
                  acrjfinal = FALSE
                  if(parr < -5){
                    rejectreason = "Late_trigger"
                  }else{
                    rejectreason = "Early_truncation"
                  }
                }else{
                  #Trim acceleration time series
                  acctrim = accts[max(1,round((parr-20)/dt)):min(length(accts),round((parr+360)/dt))]
                  accts.trm = ts(acctrim,delta = dt)
                  parr.trm = min(parr,20)

                  #Option=1 STALTA
                  AR = ARF(accts.trm, parr.trm, Option=1,magnitude,hypdist)
                  acrj[1] = AR[[1]]
                  score[1]= AR[[2]]

                  #Option=3 Bellagamba et al.
                  AR = ARF(accts.trm, max(parr.trm,1), Option=3,magnitude,hypdist)
                  acrj[2] = AR[[2]]
                  score[2] = round(AR[[1]],2)

                  #Option=4 Kishida et al. 
                  AR = ARF(accts.trm, max(parr.trm,3), Option=4,magnitude,hypdist)
                  acrj[3] = AR[[2]]
                  score[3] = round(AR[[1]],2)

                  #Option=5 Generalized Regression, NGA-W3, Kishida et al.
                  AR = ARF(accts.trm, max(parr.trm,3), Option=5,magnitude,hypdist)
                  acrj[4] = AR[[2]]
                  score[4] = round(AR[[1]],2)

                  # Option=9 NCM NN
                  AR = ARF(accts.trm, max(parr.trm,3), Option=9,magnitude,hypdist)
                  acrj[8] = AR[[2]]
                  score[8] = round(AR[[1]],2)

                  #Option=8 Support Vector, NGA-W3, Kishida et al.
              	if(sum(na.omit(acrj)==1)<5 & sum(na.omit(acrj)==0)<5){
              	AR = ARF(accts.trm, max(parr.trm,3), Option=8,magnitude,hypdist)
              	acrj[7] = AR[[2]]
              	score[7] = round(AR[[1]],2)
              	}

              	#Option=7 Booted Tree, NGA-W3, Kishida et al.
              	if(sum(na.omit(acrj)==1)<5 & sum(na.omit(acrj)==0)<5){
              	AR = ARF(accts.trm, max(parr.trm,3), Option=7,magnitude,hypdist)
              	acrj[6] = AR[[2]]
              	score[6] = round(AR[[1]],2)
              	}

              	#Option=6 Bootstrap Forrest, NGA-W3, Kishida et al.
              	if(sum(na.omit(acrj)==1)<5 & sum(na.omit(acrj)==0)<5){
              	AR = ARF(accts.trm, max(parr.trm,3), Option=6,magnitude,hypdist)
              	acrj[5] = AR[[2]]
              	score[5] = round(AR[[1]],2)
              	}
			
			im20 = IM20(accts,accts,dt,parr)#parr can be small negative value.
              	winds = windows(parr,hypdist, magnitude,dt,length(accts))
              
              	if(round(winds[[6]]/dt) <= length(accts)){
                	IM21s = IM21(accts,accts,dt,winds[[1]],winds[[2]],winds[[3]],winds[[4]],winds[[5]],winds[[6]])
	          	IM30 = c(im20,IM21s)
              	}

                  # 5 of 8 majority
                  if(sum(acrj,na.rm=TRUE)/sum(is.na(acrj)==FALSE) >= 0.625){
                    acrjfinal = TRUE
                    rejectreason = "NA"
                  }else{
                    acrjfinal = FALSE
                    rejectreason = "1/3_algorithms_reject"
                  }

legend("topright", title = acrjfinal,
       c(paste("STALTA:",        acrj[1], round(score[1],1)),
         paste("Bellagamba NN:", acrj[2], score[2]),
         paste("Kishida MV:",    acrj[3], score[3]),
         paste("Kishida GR:",    acrj[4], score[4]),
         paste("Kishida BF:",    acrj[5], score[5]),
         paste("Kishida BT:",    acrj[6], score[6]),
         paste("Kishida SV:",    acrj[7], score[7]),
         paste("NCM NN:",        acrj[8], round(score[8],3))),
       cex = 0.8, bg = "white")

                }# end parr bounds

                # ---- Save results back to meta ----
                meta$parr[rec_id]   <- as.numeric(parr)
                for (k in 1:8) {
                  meta[[paste0("acrj",k)]][rec_id]  <- as.numeric(acrj[k])
                  meta[[paste0("score",k)]][rec_id] <- as.numeric(score[k])
                }
                meta$acrjfinal[rec_id] <- as.numeric(acrjfinal)
                meta$rejectreason[rec_id] = as.character(rejectreason)
                meta$coh[rec_id]  <- as.numeric(-999)
                meta$cohL[rec_id] <- as.numeric(-999)
                meta$cohH[rec_id] <- as.numeric(-999)
                meta$acrjfinal3[rec_id] <- NA
              } # end if accuniq>9 & duration>40
            }#Close 3-component loop

            print("here8")
            #Based on three-component results, decide accept or reject
            meta$acrjfinal3[acfilename] = TRUE
            meta$rejectreason3[acfilename] = as.character("accepted")
            meta$coh[acfilename] = -999
            meta$cohL[acfilename] = -999
            meta$cohH[acfilename] = -999

            #Short-record rejection
            if(sum(meta$rejectreason[acfilename] == "short_record",na.rm=TRUE) > 0){
              meta$acrjfinal3[acfilename] = FALSE
              meta$rejectreason3[acfilename] = as.character("short_record")
            }else{
              #Low resolution rejection
              if(sum(meta$rejectreason[acfilename] == "low_resolution",na.rm=TRUE) > 0){
                meta$acrjfinal3[acfilename] = FALSE
                meta$rejectreason3[acfilename] = as.character("low_resolution")
              }else{
                #Late trigger rejection
                if(sum(meta$rejectreason[acfilename] == "Late_trigger",na.rm=TRUE) > 0){
                  meta$acrjfinal3[acfilename] = FALSE
                  meta$rejectreason3[acfilename] = as.character("Late_trigger")
                }else{
                  #Early truncation rejection
                  if(sum(meta$rejectreason[acfilename] == "Early_truncation",na.rm=TRUE) > 0){
                    meta$acrjfinal3[acfilename] = FALSE
                    meta$rejectreason3[acfilename] = as.character("Early_truncation")
                  }else{
                    #2 component rejection
                    if(sum(meta$rejectreason[acfilename] == "1/3_algorithms_reject",na.rm=TRUE) > 1){
                      meta$acrjfinal3[acfilename] = FALSE
                      meta$rejectreason3[acfilename] = as.character("2_component_rejected")
                    }else{
                      #1 component substantially low quality (disabled threshold)
                      finalscore4 = 1
                      if(min(finalscore4)< -999.){
                        meta$acrjfinal3[acfilename] = FALSE
                        meta$rejectreason3[acfilename] = as.character("1_component_very_low_quality")
                      }else{
                        #maxPGA/minPGA > 50
                        maxminPGA = max(meta$rawpga[acfilename])/min(meta$rawpga[acfilename])
                        if(maxminPGA > 50){
                          meta$acrjfinal3[acfilename] = FALSE
                          meta$rejectreason3[acfilename] = "maxPGA_over_minPGA"
                        }else{
                          #low coherence (very low guard)
                          npts = min(length(accts1),length(accts2),length(accts3))
                          out1 = COHERENCE(ts(accts1[1:npts],delta=dt),ts(accts2[1:npts],delta=dt),max.order=2)
                          out2 = COHERENCE(ts(accts2[1:npts],delta=dt),ts(accts3[1:npts],delta=dt),max.order=2)
                          out3 = COHERENCE(ts(accts3[1:npts],delta=dt),ts(accts1[1:npts],delta=dt),max.order=2)
                          cohmean = median(c(out1[[1]],out2[[1]],out3[[1]]))
                          cohlmean = median(c(out1[[1]][out1[[2]]<0.1],out2[[1]][out2[[2]]<0.1],out3[[1]][out3[[2]]<0.1]))
                          cohhmean = median(c(out1[[1]][out1[[2]]>1],out2[[1]][out2[[2]]>1],out3[[1]][out3[[2]]>1]))
                          meta$coh[acfilename]  = as.numeric(round(cohmean,3))
                          meta$cohL[acfilename] = as.numeric(round(cohlmean,3))
                          meta$cohH[acfilename] = as.numeric(round(cohhmean,3))
                          if(cohmean < 0.010 | cohhmean < 0.010 | cohlmean < 0.010 | (cohlmean < 0.01 & cohhmean < 0.01)){
                            meta$acrjfinal3[acfilename] = FALSE
                            meta$rejectreason3[acfilename] = as.character("low_coherance")
                          }
                        }
                      }
                    }
                  }
                }
              }
            }
            if(meta$acrjfinal3[acfilename[1]] == FALSE){
              text(dt*length(acc)/2, 0,"Reject",col=2,cex = 3)
            }else{
              text(dt*length(acc)/2, 0,"Accept",col=2,cex = 3)
            }

            #Save png file for each 3 component run      
            dev.off()
            #dev.off()

            #Save metadata for each 3 component run
            print(names(meta))
            print(meta$rejectreason3[acfilename])

            # ----- EXPORT: permissive, do not drop rows with missing fields -----
            valid_rows <- acfilename[!is.na(safe_get(meta, "rec_id", acfilename))]
            if (length(valid_rows) == 0) {
              warning(paste("Skipping export for event", eventid,
                            "no valid rec_id among", paste(acfilename, collapse=",")))
              next
            }

            # ----- EXPORT: permissive, do not drop rows with missing fields -----

valid_rows <- acfilename[!is.na(safe_get(meta, "rec_id", acfilename))]
if (length(valid_rows) == 0) {
  warning(paste("Skipping export for event", eventid,
                "no valid rec_id among", paste(acfilename, collapse=",")))
  next
}

output <- data.frame(
  safe_get(meta, "RSN",           valid_rows),
  safe_get(meta, "filename",      valid_rows),
  safe_get(meta, "rec_id",        valid_rows),
  safe_get(meta, "event_id",      valid_rows),
  safe_get(meta, "timeStep",      valid_rows),
  safe_get(meta, "rawpga",        valid_rows),
  safe_get(meta, "count",         valid_rows),
  safe_get(meta, "accuniq",       valid_rows),
  safe_get(meta, "parr",          valid_rows),
  safe_get(meta, "acrj1",         valid_rows),
  safe_get(meta, "acrj2",         valid_rows),
  safe_get(meta, "acrj3",         valid_rows),
  safe_get(meta, "acrj4",         valid_rows),
  safe_get(meta, "acrj5",         valid_rows),
  safe_get(meta, "acrj6",         valid_rows),
  safe_get(meta, "acrj7",         valid_rows),
  safe_get(meta, "acrj8",         valid_rows),
  safe_get(meta, "score1",        valid_rows),
  safe_get(meta, "score2",        valid_rows),
  safe_get(meta, "score3",        valid_rows),
  safe_get(meta, "score4",        valid_rows),
  safe_get(meta, "score5",        valid_rows),
  safe_get(meta, "score6",        valid_rows),
  safe_get(meta, "score7",        valid_rows),
  safe_get(meta, "score8",        valid_rows),
  safe_get(meta, "acrjfinal",     valid_rows),
  safe_get(meta, "rejectreason",  valid_rows),
  safe_get(meta, "network",       valid_rows),
  safe_get(meta, "station",       valid_rows),
  safe_get(meta, "location",      valid_rows),
  safe_get(meta, "component",     valid_rows),
  safe_get(meta, "latitude.s",    valid_rows),
  safe_get(meta, "longitude.s",   valid_rows),
  safe_get(meta, "elevation.s",   valid_rows),
  safe_get(meta, "depth.s",       valid_rows),
  safe_get(meta, "azimuth.s",     valid_rows),
  safe_get(meta, "dip.s",         valid_rows),
  safe_get(meta, "starttime.s",   valid_rows),
  safe_get(meta, "calib",         valid_rows),
  safe_get(meta, "origintime",    valid_rows),
  safe_get(meta, "magnitude",     valid_rows),
  safe_get(meta, "hypdis",        valid_rows),
  safe_get(meta, "coh",           valid_rows),
  safe_get(meta, "cohL",          valid_rows),
  safe_get(meta, "cohH",          valid_rows),
  safe_get(meta, "acrjfinal3",    valid_rows),
  safe_get(meta, "rejectreason3", valid_rows),
  stringsAsFactors = FALSE,
  check.names = FALSE   # << prevent R from altering dots
)

names(output) <- c(
  "meta.RSN.acfilename.",
  "meta.filename.acfilename.",
  "meta.rec_id.acfilename.",
  "meta.event_id.acfilename.",
  "meta.timeStep.acfilename.",
  "meta.rawpga.acfilename.",
  "meta.count.acfilename.",
  "meta.accuniq.acfilename.",
  "meta.parr.acfilename.",
  "meta.acrj1.acfilename.",
  "meta.acrj2.acfilename.",
  "meta.acrj3.acfilename.",
  "meta.acrj4.acfilename.",
  "meta.acrj5.acfilename.",
  "meta.acrj6.acfilename.",
  "meta.acrj7.acfilename.",
  "meta.acrj8.acfilename.",
  "meta.score1.acfilename.",
  "meta.score2.acfilename.",
  "meta.score3.acfilename.",
  "meta.score4.acfilename.",
  "meta.score5.acfilename.",
  "meta.score6.acfilename.",
  "meta.score7.acfilename.",
  "meta.score8.acfilename.",
  "meta.acrjfinal.acfilename.",
  "meta.rejectreason.acfilename.",
  "meta.network.acfilename.",
  "meta.station.acfilename.",
  "meta.location.acfilename.",
  "meta.component.acfilename.",
  "meta.latitude.s.acfilename.",
  "meta.longitude.s.acfilename.",
  "meta.elevation.s.acfilename.",
  "meta.depth.s.acfilename.",
  "meta.azimuth.s.acfilename.",
  "meta.dip.s.acfilename.",
  "meta.starttime.s.acfilename.",
  "meta.calib.acfilename.",
  "meta.origintime.acfilename.",
  "meta.magnitude.acfilename.",
  "meta.hypdis.acfilename.",
  "meta.coh.acfilename.",
  "meta.cohL.acfilename.",
  "meta.cohH.acfilename.",
  "meta.acrjfinal3.acfilename.",
  "meta.rejectreason3.acfilename."
)

            clean_names <- sub("^meta[.]", "", sub("[.]acfilename[.]$", "", names(output)))
            for (column in seq_along(clean_names)) output[[clean_names[column]]] <- output[[column]]
            output <- output[,!grepl("^meta[.].*[.]acfilename[.]$",names(output)),drop=FALSE]
            output$trace_id <- safe_get(meta,"trace_id",valid_rows)
            output$source_file <- rep(config$input_hdf5,nrow(output))
            output$channel <- output$component
            output$epidis <- safe_get(meta,"epidis",valid_rows)
            output$epicentral_distance_km <- output$epidis
            output$hypocentral_distance_km <- output$hypdis
            output$figure_path <- rep(figure_path,nrow(output))
            output$method_version <- "Main AcceptReject_v12_app (v10 methods)"
            saveRDS(list(output=output,figure_path=figure_path),paste0(checkpoint_path,".tmp"))
            if (!file.rename(paste0(checkpoint_path,".tmp"),checkpoint_path)) {
              if (!file.copy(paste0(checkpoint_path,".tmp"),checkpoint_path,overwrite=TRUE)) stop("Unable to write checkpoint")
              unlink(paste0(checkpoint_path,".tmp"))
            }
            app_export()
            for (row in seq_len(nrow(output))) app_status(eventid,output$trace_id[row],
              if (isTRUE(as.logical(output$acrjfinal3[row]))) "accepted" else "rejected",
              output$rejectreason3[row],figure_path)
          }#Close 3-component condition loop
          },error=function(e) {
            if (dev.cur()>1) dev.off()
            if (nzchar(figure_path) && file.exists(figure_path)) unlink(figure_path)
            app_status(eventid,group_traces,"error",conditionMessage(e))
          })
        }#Close nrec loop
      }#Close nfiles condition   
      app_status(eventid, app_event_traces, "skipped", "no_complete_group_or_estimated_arrival_outside_record", only_pending=TRUE)
      },error=function(e) {
        if (dev.cur()>1) dev.off()
        app_status(eventid,app_event_traces,"error",conditionMessage(e),only_pending=TRUE)
      })
      if (app_cancelled()) break
}
app_export()
end.time <- Sys.time()
summary <- list(input_hdf5=config$input_hdf5,output_csv=outfilepath_csv,output_xlsx=if (isTRUE(config$export_stage_workbooks)) outfilepath else NULL,
                status_counts=as.list(table(app_status_table$status)),
                cancelled=app_cancelled(),elapsed_seconds=as.numeric(difftime(end.time,start.time,units="secs")))
jsonlite::write_json(summary,file.path(baipa,"stage1_summary.json"),auto_unbox=TRUE,pretty=TRUE)
summary$status <- if (app_cancelled()) "cancelled" else if (!any(app_status_table$status %in% c("accepted","rejected"))) "failed" else if (any(app_status_table$status %in% c("error","skipped"))) "completed_with_issues" else "completed"
summary$counts <- summary$status_counts
jsonlite::write_json(summary,file.path(baipa,"acceptreject_summary.json"),auto_unbox=TRUE,pretty=TRUE)
cat("Stage 1 finished. See stage1_status.csv for every trace, including skips and errors.\n")
if (app_cancelled()) quit(status=130)
if (any(app_status_table$status=="error")) quit(status=2)
if (!any(app_status_table$status %in% c("accepted","rejected"))) quit(status=3)
