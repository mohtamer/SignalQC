# File-based probe avoids command-line quoting differences in Windows R launchers.
cat(paste(as.character(getRversion()),.Machine$sizeof.pointer,R.version$arch,sep="|"))
