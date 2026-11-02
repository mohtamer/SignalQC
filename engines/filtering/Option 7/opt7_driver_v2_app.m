function opt7_driver_v2_app(in_path, out_path)
% OPT7_DRIVER  Bridge to run Option-7 (MATLAB) from R.
% Usage (MATLAB): opt7_driver_v2_app('C:/tmp/opt7_in.mat','C:/tmp/opt7_out.mat')
%
% Expects in_path MAT-file to contain:
%   acc        : double column vector, acceleration (same units as Filter.m expects)
%   dt         : scalar, sampling interval (s)
%   mag        : scalar, event magnitude
%   Pt         : scalar, P arrival time (s) relative to series start used here
%   St, Se     : scalars, S-window start/end (s)
%   direction  : 1 (EW), 2 (NS), 3 (UD) — used by autochoice_fHP
%   type       : char or numeric flag passed to Filter (e.g., 'A' or 'C')
%   baseTF     : numeric flag for baseline/transfer choice (per your Filter.m)
%
% Produces out_path MAT-file with:
%   fHP, fLP   : chosen high/low-pass corner frequencies (Hz)
%   meanSNR    : mean SNR within (fHP, fLP) [NaN if not computable]

  % Ensure helpers are on path
  try
    thisDir = fileparts(mfilename('fullpath'));
    addpath(thisDir);
  catch
    % best-effort; user should put this file in the same folder as Filter.m
  end

  % Load inputs
  S = load(in_path);
  acc = S.acc(:);
  dt  = S.dt;
  mag = S.mag;
  Pt  = S.Pt;  St = S.St;  Se = S.Se;
  direction = S.direction;
  type = S.type;     % e.g., 'A' or 'C'
  baseTF = S.baseTF; % numeric per your Filter.m

  % --- Auto-pick LP then HP (these functions are in your folder)
  fLP = autochoice_fLP_v2_app(acc, dt, numel(acc), Pt, St, Se);
  fHP = autochoice_fHP_v2_app(acc, dt, numel(acc), mag, Pt, direction, fLP);

  % --- Safety clamps (optional; mirror your R logic if you like)
  nyq = 0.5/dt;
  if ~isfinite(fLP) || fLP<=0,    fLP = 0.8*nyq; end
  if ~isfinite(fHP) || fHP<=0,    fHP = min(0.1, 0.05*nyq); end
  fLP = min(fLP, 0.8*nyq);
  if fLP <= fHP, fHP = max(0.9*fLP/10, min(fHP, fLP/10)); end % try to keep a decade

  % --- Run the filter engine (returns filtered A/V/D; we mostly need fHP/fLP)
  try
    [~, ~, ~, ~, ~, ~, ~] = Filter(dt, acc, fHP, fLP, type, baseTF);
  catch ME
    warning('Filter() failed: %s', ME.message);
  end

  % --- Compute mean SNR within (fHP, fLP) using your helper (if available)
  meanSNR = NaN;
  try
    % calculate_SNR is expected to return Nx2 [freq, SNR] OR two arrays; handle both
    snrOut = calculate_SNR_v2_app(acc, dt, numel(acc), [0, max(0, 0.95*Pt)], [St, Se]);
    if ismatrix(snrOut) && size(snrOut,2) >= 2
      freq = snrOut(:,1); SNR = snrOut(:,2);
    else
      % If the helper returns separate arrays, adapt here:
      freq = snrOut.freq(:); SNR = snrOut.SNR(:);
    end
    idx = (freq > fHP) & (freq < fLP) & isfinite(SNR);
    if any(idx), meanSNR = mean(SNR(idx)); end
  catch
    % leave meanSNR = NaN
  end

  % Save outputs
  save(out_path, 'fHP', 'fLP', 'meanSNR', '-mat');
end
