# Installation/validation only. No waveform processing or scientific changes.
args <- commandArgs(trailingOnly=TRUE)
if(length(args)!=7L)stop("Internal setup arguments are incomplete")
action<-args[1]; manifest<-args[2]; common<-args[3]; im<-args[4]
app<-args[5]; python<-args[6]; mode<-args[7]
dir.create(common,recursive=TRUE,showWarnings=FALSE)
dir.create(im,recursive=TRUE,showWarnings=FALSE)
.libPaths(c(if(mode=="intensity")im,common,.Library),include.site=FALSE)
options(repos=c(CRAN="https://cloud.r-project.org"),timeout=300,
        install.packages.check.source="no",Ncpus=2)
packages<-read.delim(manifest,stringsAsFactors=FALSE,check.names=FALSE)
standard<-unique(packages$Package[packages$Package!="RCTC"])
if(action=="install"){
  cat("Installing R dependencies in a private user library.\n")
  bioc<-intersect(standard,c("rhdf5","rhdf5filters","Rhdf5lib"))
  cran<-setdiff(standard,bioc)
  # Binary packages avoid compiler installation and changes to the R program folder.
  install.packages(unique(c(cran,"BiocManager")),lib=common,type="win.binary",
                   dependencies=c("Depends","Imports","LinkingTo"))
  if(length(bioc)){
    if(!requireNamespace("BiocManager",quietly=TRUE))stop("BiocManager could not be installed. Check internet/IT restrictions.")
    BiocManager::install(bioc,lib=common,type="binary",ask=FALSE,update=FALSE,
                         dependencies=c("Depends","Imports","LinkingTo"))
  }
  install.packages(file.path(app,"runtime","setup_packages","RCTC_0.1.0.zip"),
                   repos=NULL,lib=common,type="win.binary")
  install.packages(file.path(app,"runtime","setup_packages","RCTC_0.2.4.zip"),
                   repos=NULL,lib=im,type="win.binary")
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
expected<-if(mode=="intensity")"0.2.4" else "0.1.0"
if(as.character(packageVersion("RCTC"))!=expected)stop("Wrong RCTC version: expected ",expected)
if(mode=="intensity" && !is.function(get0("RotD_cal_PSA",asNamespace("RCTC"),inherits=FALSE)))stop("RCTC RotD routines are missing")
for(pkg in c(standard,"RCTC")){
  location<-normalizePath(find.package(pkg),winslash="/",mustWork=TRUE)
  roots<-normalizePath(c(common,im),winslash="/",mustWork=TRUE)
  if(!any(startsWith(tolower(location),paste0(tolower(roots),"/"))))stop("Package was loaded outside the private libraries: ",pkg)
}
if(mode!="intensity"){
  reticulate::use_python(python,required=TRUE)
  reticulate::py_run_string("import numpy, scipy, pandas, h5py, obspy")
  actual<-normalizePath(reticulate::py_config()$python,winslash="/",mustWork=TRUE)
  if(tolower(actual)!=tolower(normalizePath(python,winslash="/",mustWork=TRUE)))stop("R selected a different Python")
  setwd(file.path(app,"engines","acceptreject"))
  reticulate::source_python("GMClassifierFx.py")
  reticulate::py_run_string("import GMClassifierFx; _signalqc_model=GMClassifierFx.neuralNet(); _signalqc_model.loadNN('GMClassifier_CantWell')")
}
versions<-data.frame(Package=c(standard,"RCTC"),Version=vapply(c(standard,"RCTC"),function(p)as.character(packageVersion(p)),character(1)))
write.table(versions,file.path(common,paste0("validated_",mode,"_versions.tsv")),sep="\t",row.names=FALSE,quote=FALSE)
cat("Verified ",length(unique(c(standard,"RCTC")))," R packages; RCTC ",expected,"; mode ",mode,".\n",sep="")
