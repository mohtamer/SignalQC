# I/O, checkpoints and record grouping. Classifier equations remain in the main
# and the original ARF/IM20/IM21/windows/COHERENCE dependencies.
app_time <- function(x) as.POSIXct(gsub("T", " ", sub("Z$", "", as.character(x))),
                                  format="%Y-%m-%d %H:%M:%OS", tz="UTC")
app_filename <- function(x) gsub("[^A-Za-z0-9_.-]", "_", as.character(x))
app_cancelled <- function() !is.null(config$cancel_file) && file.exists(config$cancel_file)
app_status_table <- data.frame(source_file=character(), event_id=character(), trace_id=character(),
                              status=character(), reason=character(), figure_path=character(), stringsAsFactors=FALSE)
write.csv(app_status_table, file.path(config$output_dir,"stage1_status.csv"), row.names=FALSE, na="")
app_status <- function(event, traces, status, reason="", figure="", only_pending=FALSE) {
  changed <- 0L
  for (trace in traces) {
    index <- which(app_status_table$event_id == event & app_status_table$trace_id == trace)
    if (only_pending && length(index) && app_status_table$status[index[1]] != "pending") next
    row <- data.frame(source_file=config$input_hdf5, event_id=as.character(event), trace_id=as.character(trace),
                      status=status, reason=as.character(reason), figure_path=as.character(figure), stringsAsFactors=FALSE)
    if (length(index)) app_status_table[index[1],] <<- row else app_status_table <<- rbind(app_status_table, row)
    changed <- changed + 1L
  }
  if (!changed) return(invisible(NULL))
  write.csv(app_status_table, file.path(config$output_dir,"stage1_status.csv"), row.names=FALSE, na="")
  cat(sprintf("SIGNALQC %s event=%s traces=%d %s\n", status, event, changed, reason))
  flush.console()
}
app_export <- function() {
  records <- list.files(checkpoint_dir, pattern="[.]rds$", full.names=TRUE)
  tables <- lapply(records, function(path) tryCatch(readRDS(path)$output, error=function(e) NULL))
  tables <- Filter(Negate(is.null), tables)
  if (!length(tables)) {
    # Header-only exports prevent stale successful decisions from a different
    # source fingerprint being mistaken for current results.
    output <- data.frame(trace_id=character(),event_id=character(),source_file=character(),
                         station=character(),channel=character(),component=character(),
                         network=character(),location=character(),acrjfinal3=logical())
  } else output <- do.call(rbind, tables)
  write.csv(output, paste0(outfilepath_csv,".tmp"), row.names=FALSE, na="")
  if (!file.copy(paste0(outfilepath_csv,".tmp"), outfilepath_csv, overwrite=TRUE)) stop("Unable to publish Stage 1 CSV")
  unlink(paste0(outfilepath_csv,".tmp"))
  if (isTRUE(config$export_stage_workbooks)) {
    openxlsx::write.xlsx(output, paste0(outfilepath,".tmp.xlsx"), asTable=TRUE, overwrite=TRUE)
    if (!file.copy(paste0(outfilepath,".tmp.xlsx"), outfilepath, overwrite=TRUE)) stop("Unable to publish Stage 1 workbook; close it in Excel")
    unlink(paste0(outfilepath,".tmp.xlsx"))
  }
  invisible(output)
}

# A triplet must be one simultaneous NSL/channel-family recording, never three
# segments of one component. Repeated records are matched within one sample.
app_groups <- function(ts.info) {
  active <- ts.info[which(!is.na(ts.info$use) & ts.info$use), , drop=FALSE]
  groups <- list()
  if (!nrow(active)) return(groups)
  families <- interaction(active$network, active$station, active$location,
                          substring(active$component,1,2), drop=TRUE, lex.order=TRUE)
  # Complete acquisition labels retain the original grouping. Missing labels
  # require explicit association evidence from the v10 reader, never blank NSL.
  family_keys <- as.character(families)
  partial <- !nzchar(trimws(active$network)) | !nzchar(trimws(active$station))
  if (any(partial)) {
    for (i in which(partial)) {
      identity <- if ("identity_group" %in% names(active)) as.character(active$identity_group[i]) else ""
      if (!nzchar(identity)) identity <- paste0("isolated_",active$trace.id[i])
      family_keys[i] <- paste("partial",identity,substring(active$component[i],1,2),sep="||")
    }
  }
  split_key <- if (any(partial)) family_keys else families
  for (family in split(active, split_key)) {
    family <- family[order(as.numeric(app_time(family$recstime)), family$component),,drop=FALSE]
    remaining <- seq_len(nrow(family))
    while (length(remaining)) {
      first <- remaining[1]
      tolerance <- max(family$delta[remaining], na.rm=TRUE) + 1e-7
      aligned <- remaining[abs(as.numeric(difftime(app_time(family$recstime[remaining]),
                                        app_time(family$recstime[first]),units="secs"))) <= tolerance]
      if (!length(aligned)) aligned <- first
      component <- substring(family$component[aligned],3,3)
      valid <- length(aligned)==3 && length(unique(component))==3 &&
        (setequal(component,c("N","E","Z")) || setequal(component,c("1","2","Z")) || setequal(component,c("1","2","3"))) &&
        max(family$delta[aligned])-min(family$delta[aligned]) < 1e-9
      ids <- family$rec_id[aligned]
      attr(ids,"valid") <- valid
      groups[[length(groups)+1]] <- ids
      remaining <- setdiff(remaining, aligned)
    }
  }
  groups
}
file.select <- function(ts.info,io,K=1) {
  groups <- app_groups(ts.info)
  if (io==0) return(length(groups))
  if (io==1) return(groups[[K]])
  ts.info
}
