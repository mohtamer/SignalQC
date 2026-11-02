# Selection changes which existing classifier calls vote. Their equations,
# inputs, score rounding, and origin-time STA/LTA reference remain unchanged.
app_voting_settings <- function(config) {
  methods <- if (is.null(config$acceptreject_methods)) as.character(1:8) else as.character(config$acceptreject_methods)
  required <- if (is.null(config$acceptreject_required_votes)) 5 else config$acceptreject_required_votes
  if (!length(methods) || anyDuplicated(methods) || any(!methods %in% as.character(1:8))) stop("Invalid accept/reject method selection")
  if (length(required)!=1 || !is.numeric(required) || !is.finite(required) || required != as.integer(required) || required<1 || required>length(methods)) stop("Invalid required accepting vote count")
  list(methods=methods,required=as.integer(required),legacy=setequal(methods,as.character(1:8)) && required==5)
}

app_custom_vote <- function(accts.trm,parr.trm,magnitude,hypdist,settings,call_method=ARF) {
  order <- c(1,2,3,4,8,7,6,5)
  options <- c(1,3,4,5,6,7,8,9)
  enabled <- order[as.character(order) %in% settings$methods]
  decisions <- scores <- rep(NA_real_,8)
  states <- ifelse(as.character(1:8) %in% settings$methods,"early_stopped","disabled")
  errors <- rep("",8)
  accepts <- unavailable <- 0L
  verdict <- NA
  for (index in seq_along(enabled)) {
    slot <- enabled[index]
    remaining <- length(enabled)-index+1L
    if (accepts >= settings$required) {verdict <- TRUE; break}
    if (accepts+unavailable+remaining < settings$required) {verdict <- FALSE; break}
    value <- tryCatch(call_method(accts.trm,if (slot==1) parr.trm else max(parr.trm,if (slot==2) 1 else 3),
                                 Option=options[slot],magnitude,hypdist),error=function(e) {
                                   errors[slot] <<- conditionMessage(e); NULL
                                 })
    if (!is.null(value)) {
      decisions[slot] <- as.numeric(value[[if (slot==1) 1 else 2]])
      scores[slot] <- if (slot==1) as.numeric(value[[2]]) else round(as.numeric(value[[1]]),2)
    }
    if (!is.finite(decisions[slot]) || !decisions[slot] %in% c(0,1)) {
      decisions[slot] <- NA_real_; states[slot] <- "unavailable"; unavailable <- unavailable+1L
      if (!nzchar(errors[slot])) errors[slot] <- "Classifier returned no valid accepting/rejecting vote"
    } else {
      states[slot] <- "executed"
      accepts <- accepts+as.integer(decisions[slot]==1)
    }
  }
  if (is.na(verdict)) {
    if (accepts>=settings$required) verdict <- TRUE
    else if (accepts+unavailable<settings$required) verdict <- FALSE
  }
  list(decision=verdict,acrj=decisions,score=scores,states=states,errors=errors,
       reason=if (is.na(verdict)) "Accept/reject vote is unresolved because enabled classifier results are unavailable" else "")
}

app_voting_audit_table <- data.frame()
app_voting_audit <- function(event,trace,states,decisions,scores,reason="") {
  row <- data.frame(event_id=as.character(event),trace_id=as.character(trace),
                    acceptreject_methods=paste(app_votes$methods,collapse=","),
                    acceptreject_required_votes=app_votes$required,voting_reason=reason,stringsAsFactors=FALSE)
  for (slot in 1:8) {
    row[[paste0("acrj",slot,"_status")]] <- states[slot]
    row[[paste0("acrj",slot)]] <- decisions[slot]
    row[[paste0("score",slot)]] <- scores[slot]
  }
  if (nrow(app_voting_audit_table)) {
    old <- which(app_voting_audit_table$event_id==event & app_voting_audit_table$trace_id==trace)
    if (length(old)) app_voting_audit_table <<- app_voting_audit_table[-old,,drop=FALSE]
  }
  app_voting_audit_table <<- rbind(app_voting_audit_table,row)
  write.csv(app_voting_audit_table,file.path(config$output_dir,"method_execution.csv"),row.names=FALSE,na="")
}
