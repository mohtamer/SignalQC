function value = geometric_mean_app(X, dim)
% Geometric mean with the same definition as MATLAB geomean(X,dim).
% This avoids a Statistics toolbox dependency; no signal equation is changed.
% NaNs are included, as in the original geomean calls. Zero inputs return zero.
% https://www.mathworks.com/help/stats/geomean.html
if any(X(:) < 0)
    error('SignalQC:NegativeGeometricMean', 'Geometric mean requires nonnegative values.');
end
value = exp(mean(log(X), dim));
end
