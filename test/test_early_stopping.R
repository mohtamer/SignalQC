# Run with Rscript test/test_early_stopping.R from the repository root.
# Exercise the actual default classifier block with controlled method outputs.
args <- commandArgs(trailingOnly=TRUE)
root <- if (length(args)) args[1] else "."
main <- file.path(root, "engines", "acceptreject", "Main AcceptReject_v16_ncm.R")
code <- parse(main)
find_default_blocks <- function(node) {
  if (missing(node)) return(list())
  out <- list()
  if (is.call(node) && identical(node[[1]], as.name("if")) &&
      identical(node[[2]], quote(app_votes$legacy))) out <- list(node[[3]])
  if (is.call(node) || is.expression(node) || is.pairlist(node)) {
    for (child in as.list(node)) out <- c(out, find_default_blocks(child))
  }
  out
}
blocks <- find_default_blocks(code)
stopifnot(length(blocks) == 2L)
classifier_block <- blocks[[1]]
final_decision_block <- blocks[[2]]

run_votes <- function(votes) {
  env <- new.env(parent=globalenv())
  env$acrj <- env$score <- rep(NA_real_, 8)
  env$method_states <- rep("not_evaluated", 8)
  env$accts.trm <- numeric(1)
  env$parr.trm <- env$magnitude <- env$hypdist <- 1
  env$calls <- integer()
  options <- c(1L,3L,4L,5L,9L,8L,7L,6L)
  env$ARF <- function(accts, parr, Option, magnitude, hypdist) {
    index <- match(Option, options)
    env$calls <- c(env$calls, index)
    value <- votes[index]
    if (Option == 1L) list(value, 0.75) else list(0.75, value)
  }
  eval(classifier_block, env)
  ratio <- sum(env$acrj, na.rm=TRUE) / sum(!is.na(env$acrj))
  verdict <- if (is.finite(ratio)) {
    eval(final_decision_block, env)
    env$acrjfinal
  } else NA
  list(calls=env$calls, states=env$method_states, votes=env$acrj, verdict=verdict)
}

cases <- expand.grid(rep(list(c(0,1,NA_real_)), 8))
for (row in seq_len(nrow(cases))) {
  votes <- as.numeric(cases[row,])
  actual <- run_votes(votes)
  accepts <- cumsum(ifelse(is.na(votes), 0, votes == 1))
  rejects <- cumsum(ifelse(is.na(votes), 0, votes == 0))
  decisive <- which(accepts >= 5 | rejects >= 4)
  expected_n <- if (length(decisive)) decisive[1] else 8L
  stopifnot(identical(actual$calls, seq_len(expected_n)))
  full_ratio <- sum(votes, na.rm=TRUE) / sum(!is.na(votes))
  expected <- if (is.finite(full_ratio)) full_ratio >= 0.625 else NA
  stopifnot(identical(actual$verdict, expected))
  order <- c(1,2,3,4,8,7,6,5)
  called <- order[seq_len(expected_n)]
  skipped <- setdiff(seq_len(8), called)
  stopifnot(all(actual$states[skipped] == "early_stopped"),
            all(is.na(actual$votes[skipped])))
  missing <- called[is.na(votes[seq_len(expected_n)])]
  stopifnot(all(actual$states[missing] == "unavailable"))
}
cat("PASS:", nrow(cases), "Accept/Reject/missing-output patterns; first decisive vote stops evaluation; final decisions match full voting; skipped and unavailable statuses remain distinct.\n")
