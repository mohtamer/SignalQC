"""Exact HDF5 key access. No timestamp reconstruction or station-only guessing."""
import h5py
import numpy as np

def _read(path,event,station,channel,trace,dataset):
    with h5py.File(path,'r') as f:
        group=f[str(event)][str(trace)]
        def txt(x): return x.decode() if isinstance(x,bytes) else str(x)
        for key,expected in [('station',station),('channel',channel)]:
            if key in group.attrs and txt(group.attrs[key])!=str(expected):
                raise ValueError(f'{key} mismatch for exact trace {trace}')
        ds=group[dataset]
        if dataset=='acceleration_waveform':
            unit=txt(ds.attrs.get('units',group.attrs.get('acceleration_units','m/s^2'))).lower().replace(' ','')
            factor={'m/s^2':1,'m/s²':1,'m/s/s':1,'m/s2':1,'cm/s^2':.01,'cm/s²':.01,'cm/s2':.01,'cm/s/s':.01,'gal':.01,'g':9.80665,'mg':.00980665}.get(unit)
            if factor is None: raise ValueError(f'Unsupported acceleration units: {unit}')
            return np.asarray(ds[:],dtype=float)*factor
        unit=txt(ds.attrs.get('units',group.attrs.get('raw_units','counts'))).lower()
        if unit not in ('count','counts','digital counts','adc counts'):
            raise ValueError(f'Clipping requires ADC counts, but raw waveform units are {unit}')
        # Convert before abs() to avoid overflow at a signed ADC's minimum value.
        return np.asarray(ds[:],dtype=float)

def hdf5_acceleration(path,event,station,channel,trace):
    return _read(path,event,station,channel,trace,'acceleration_waveform')

def hdf5_raw_counts(path,event,station,channel,trace):
    return _read(path,event,station,channel,trace,'raw_waveform')
