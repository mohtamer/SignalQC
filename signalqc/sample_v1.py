"""Portable example configuration; reference outputs are never used as inputs."""
from pathlib import Path
import json
from .project import APP_ROOT,defaults

def sample_config():
    folder=APP_ROOT/'test'
    settings=json.loads((folder/'sample_settings.json').read_text(encoding='utf-8'))
    cfg=defaults()
    cfg.update({k:v for k,v in settings.items() if k not in ('input_hdf5','event_catalog','station_catalog','event_id','metadata_example')})
    cfg.update(hdf5_paths=[str(folder/settings['input_hdf5'])],raw_paths=[],response_paths=[],
               event_catalog=str(folder/settings['event_catalog']),station_catalog=str(folder/settings['station_catalog']) if settings.get('station_catalog') else '',
               review_custom_hdf5_layout=False,interface_task='process',interface_saved_inputs={})
    for p in cfg['hdf5_paths']+[cfg['event_catalog'],cfg['station_catalog']]:
        if p and not Path(p).is_file():raise FileNotFoundError('The sample is incomplete: '+str(p))
    return cfg
