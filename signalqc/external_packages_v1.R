# Installation/validation only. No waveform processing or scientific changes.
args <- commandArgs(trailingOnly=TRUE)
if(length(args)!=5L)stop("Internal setup arguments are incomplete")
action<-args[1]; manifest<-args[2]; common<-args[3]
app<-args[4]; python<-args[5]
dir.create(common,recursive=TRUE,showWarnings=FALSE)
.libPaths(c(common,.Library),include.site=FALSE)
options(repos=c(CRAN="https://cloud.r-project.org"),timeout=300,
        install.packages.check.source="no",Ncpus=2)
packages<-read.delim(manifest,stringsAsFactors=FALSE,check.names=FALSE)
standard<-unique(packages$Package[packages$Package!="RCTC"])
if(action=="install"){
  cat("Installing R dependencies in a private user library.\n")
  install.packages(standard,lib=common,type="win.binary",dependencies=c("Depends","Imports","LinkingTo"))
  install.packages(file.path(app,"runtime","setup_packages","RCTC_0.1.0.zip"),
                   repos=NULL,lib=common,type="win.binary")
  cat("Installation commands completed. Separate validation is required.\n")
  quit(status=0)
}
if(action!="check")stop("Unknown setup action")
failures<-character()
for(pkg in c(standard,"RCTC")){
  ok<-tryCatch(requireNamespace(pkg,quietly=TRUE),error=function(e)FALSE)
  if(!ok)failures<-c(failures,pkg)
}
if(length(failures))stop("R packages unavailable/incompatible: ",paste(failures,collapse=", "))
expected<-"0.1.0"
if(as.character(packageVersion("RCTC"))!=expected)stop("Wrong RCTC version: expected ",expected)
for(pkg in c(standard,"RCTC")){
  location<-normalizePath(find.package(pkg),winslash="/",mustWork=TRUE)
  roots<-normalizePath(common,winslash="/",mustWork=TRUE)
  if(!any(startsWith(tolower(location),paste0(tolower(roots),"/"))))stop("Package was loaded outside the private libraries: ",pkg)
}
reticulate::use_python(python,required=TRUE)
reticulate::py_run_string("import numpy, scipy, pandas, h5py, obspy")
actual<-normalizePath(reticulate::py_config()$python,winslash="/",mustWork=TRUE)
if(tolower(actual)!=tolower(normalizePath(python,winslash="/",mustWork=TRUE)))stop("R selected a different Python")
setwd(file.path(app,"engines","acceptreject"))
reticulate::source_python("GMClassifierFx.py")
reticulate::py_run_string("import GMClassifierFx; _signalqc_model=GMClassifierFx.neuralNet(); _signalqc_model.loadNN('GMClassifier_CantWell')")
versions<-data.frame(Package=c(standard,"RCTC"),Version=vapply(c(standard,"RCTC"),function(p)as.character(packageVersion(p)),character(1)))
write.table(versions,file.path(common,"validated_filtering_versions.tsv"),sep="\t",row.names=FALSE,quote=FALSE)
cat("Verified ",length(unique(c(standard,"RCTC")))," R packages; RCTC ",expected,".\n",sep="")
