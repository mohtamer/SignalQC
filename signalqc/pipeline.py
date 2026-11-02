from __future__ import annotations
import importlib.util
import json
import copy
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone

from .project import APP_ROOT, Project, ProjectLock, signature, atomic_json
from .data import walk_files, read_table
from .results_v2 import publish_results
from .method_settings_v1 import method_setting_errors, stage_method_settings
from .hdf5_profiles_v1 import mapping_for_source, adapter_for_mapping, mapping_files

INGESTION_KEYS = {
    'raw_paths', 'response_paths', 'event_catalog', 'station_catalog', 'column_mappings',
    'grouping', 'remove_response', 'input_units', 'workers', 'resume', 'event_depth_unit',
    'station_elevation_unit', 'events_sheet', 'stations_sheet', 'events_delimiter',
    'stations_delimiter', 'events_encoding', 'stations_encoding', 'scan_all_files',
    'trace_selectors', 'event_time_tolerance_s', 'max_files', 'max_traces', 'waveform_format',
}


def ingestion_config(config):
    selected = {k: v for k, v in config.items() if k in INGESTION_KEYS}
    if 'event_match_tolerance_seconds' in config and 'event_time_tolerance_s' not in config:
        selected['event_time_tolerance_s'] = config['event_match_tolerance_seconds']
    return selected


def engine_inputs(engine):
    # Include classifier weights and model assets, and exclude build/cache trees.
    extensions = {'.r', '.py', '.m', '.rds', '.h5', '.pkl', '.json', '.mat', '.csv', '.txt', '.rdata', '.dll', '.so'}
    excluded={'qa','tests','verification','checkpoints','outputs','guo_python_experimental_v1'}
    return [str(p) for p in walk_files([engine]) if p.suffix.lower() in extensions and not any(part.lower() in excluded for part in p.relative_to(engine).parts)]

def load_engine(folder,name):
    path=APP_ROOT/'engines'/folder/name
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('signalqc_'+folder,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module

class Pipeline:
    def __init__(self,project,progress=None):
        self.project=project
        from .ncm_data_v1 import bind_config
        from .workflow_v2 import relevant_input_config
        self.config=relevant_input_config(bind_config(copy.deepcopy(project.config)))
        self.progress=progress or (lambda event:None)
        self.cancelled=threading.Event()
        self.cancel_file=project.folder/'pause.request'
        self.run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:8]

    def pause(self):
        self.cancelled.set()
        self.cancel_file.write_text('Pause requested. Save the current checkpoint.',encoding='utf-8')
        self.progress({'type':'pause_requested','message':'Pause requested. Finishing the current safe checkpoint…'})

    def emit(self,message,**kwargs): self.progress(dict(message=message,**kwargs))

    def run(self):
        with ProjectLock(self.project.folder):
            self.cancel_file.unlink(missing_ok=True)
            self.project.save(self.config)
            with self.project.connect() as db:
                db.execute("UPDATE jobs SET status='interrupted' WHERE status='running'")
            try:
                return self._run()
            except Exception as exc:
                atomic_json(self.project.folder/'last_run.json', {'status':'failed','message':str(exc),'run_id':self.run_id})
                raise

    def _target(self, stage, stem, digest, old):
        changed_outputs=bool(old and old.get('status') in ('completed','cached') and self.config.get('resume',True) and not self._cached(old))
        if self.config.get('resume', True) and not changed_outputs and old and old.get('output_dir'):
            prior=Path(old['output_dir']).resolve()
            if prior.is_relative_to(self.project.folder/'outputs'/stage):
                return prior
        name=(stem+'_' if stem else '')+digest[:12]
        if not self.config.get('resume',True) or changed_outputs:
            name+='_'+self.run_id
        return self.project.folder/'outputs'/stage/name

    def _run(self):
        from .ncm_data_v1 import snapshot_tables
        self.config=snapshot_tables(self.config,self.project.folder)
        stages=self.config.get('stages',[])
        if not stages: raise ValueError('Select at least one task to run.')
        self.emit('Preparing selected workflow.',type='run_started',stages=stages)
        setting_errors=method_setting_errors(self.config)
        from .intensity_v1 import settings_errors
        setting_errors+=settings_errors(self.config)
        if setting_errors: raise ValueError(' '.join(setting_errors))
        h5=mapping_files(self.config.get('hdf5_paths',[]),self.project.folder)
        reports=[]
        if h5 and set(stages)!={"intensity"}:
            normalized=[]
            for source in h5:
                if self.cancelled.is_set(): break
                try:
                    mapping=mapping_for_source(self.config,source)
                    if not mapping.get('enabled'):
                        normalized.append(source)
                        continue
                    result=adapter_for_mapping(mapping).normalize_hdf5(source,self.project.folder,mapping,
                                          progress=self.progress,cancelled=self.cancelled.is_set)
                    files=[result['output_file']] if result.get('output_file') else []
                    reports.append(dict(result,stage='hdf5_import',input=source,output_files=files))
                    if result.get('status') not in ('failed','cancelled'): normalized.extend(files)
                except Exception as exc:
                    self.emit(f'HDF5 import failed for {source}: {exc}',level='error')
                    reports.append(dict(stage='hdf5_import',input=source,status='failed',message=str(exc),output_files=[]))
            h5=normalized
        if 'ingestion' in stages:
            self.emit('Preparing HDF5 files from raw recordings.',type='stage_started',stage='ingestion',input='raw waveforms')
            module=load_engine('ingestion','hdf5_writer_v10_ncm.py')
            cfg=ingestion_config(self.config)
            inventory=module.discover(cfg)
            paths=inventory['raw_files']+inventory['response_files']
            paths += [p for p in (cfg.get('event_catalog'),cfg.get('station_catalog')) if p]
            paths += engine_inputs(APP_ROOT/'engines'/'ingestion')
            paths.append(str(APP_ROOT/'signalqc'/'ncm_data_v1.py'))
            settings={k:v for k,v in cfg.items() if k not in ('resume','workers','raw_paths','response_paths')}
            digest=signature(paths,settings)
            key,old=self.project.job('ingestion','raw waveforms',digest)
            target=self._target('ingestion','',digest,old)
            if self._cached(old):
                h5.extend(json.loads(old['outputs'])); self.emit('HDF5 generation already complete; verified saved outputs.')
                reports.append({'stage':'ingestion','status':'cached','output_files':json.loads(old['outputs'])})
            else:
                cfg.update(output_dir=str(target),resume=self.config.get('resume',True))
                self.project.update_job(key,'ingestion','raw waveforms',digest,'running',output_dir=target)
                try:
                    result=module.run(cfg,progress=self.progress,cancelled=self.cancelled.is_set)
                    files=result.get('output_files',[])
                    status=result.get('status','failed')
                    self.project.update_job(key,'ingestion','raw waveforms',digest,status,files,json.dumps(result,default=str),output_dir=target)
                    reports.append(result); h5.extend(files)
                except Exception as e:
                    self.project.update_job(key,'ingestion','raw waveforms',digest,'failed',message=str(e),output_dir=target); raise
            self.emit('HDF5 preparation finished.',type='stage_finished',stage='ingestion',input='raw waveforms',status=reports[-1].get('status','completed'))
        h5=sorted(set(h5))
        if self.cancelled.is_set():
            summary={'status':'cancelled','reports':reports,'hdf5_inputs':h5,'run_id':self.run_id}
            atomic_json(self.project.folder/'last_run.json',summary)
            return summary
        if any(x in stages for x in ('acceptreject','filtering','sanity','intensity')) and not h5:
            raise ValueError('No HDF5 inputs were selected or successfully generated.')
        if 'filtering' in stages and 'acceptreject' not in stages and not self.config.get('stage1_metadata'):
            raise ValueError('Select the existing accept/reject metadata when starting at filtering.')
        if 'sanity' in stages and 'filtering' not in stages and not self.config.get('filtering_metadata'):
            raise ValueError('Select the existing filtering metadata when starting at sanity checks.')
        if 'sanity' in stages and 'filtering' not in stages and len(h5)>1:
            raise ValueError('For sanity-only runs, select one source HDF5 and its matching filtering table at a time.')
        # The worker budget belongs to waveforms/groups inside ONE HDF5. Never
        # multiply it by a second pool of concurrent input files.
        self.emit('HDF5 file queue ready.',type='files_planned',files=h5)
        for path in h5:
            if self.cancelled.is_set(): break
            self.emit('Processing '+Path(path).name,type='file_started',input=path)
            file_reports=[]
            try:
                file_reports=self._process_h5(path,stages); reports.extend(file_reports)
            except Exception as e:
                self.emit(str(e),level='error'); reports.append({'status':'failed','input':path,'message':str(e)})
                file_reports=[{'status':'failed'}]
            states=[r.get('status') for r in file_reports]
            scientific_states=[r.get('status') for r in file_reports if r.get('stage')!='results']
            file_status=('cancelled' if self.cancelled.is_set() else 'failed' if 'failed' in states else
                         'completed_with_issues' if any(s not in ('completed','cached','skipped','no_accepted_traces') for s in states) else 'completed')
            self.emit(Path(path).name+': '+file_status.replace('_',' '),type='file_finished',input=path,status=file_status,
                      reused_only=bool(scientific_states) and all(s in ('cached','skipped','no_accepted_traces') for s in scientific_states),
                      reused_work='cached' in scientific_states)
        has_issues=any(r.get('status') not in ('completed','cached','no_accepted_traces','skipped') or
                      (r.get('stage')=='hdf5_import' and bool(r.get('issues'))) for r in reports)
        status='cancelled' if self.cancelled.is_set() else 'completed_with_issues' if has_issues else 'completed'
        summary={'status':status,'reports':reports,'hdf5_inputs':h5,'run_id':self.run_id}
        atomic_json(self.project.folder/'last_run.json',summary)
        self.emit(f'Run {status.replace("_"," ")}. Results and logs are saved.',type='run_finished',status=status)
        return summary

    def _cached(self,row):
        if not self.config.get('resume',True) or not row or row['status'] not in ('completed','cached'): return False
        try:
            files=json.loads(row['outputs'])
            return bool(files) and all(Path(p).is_file() and Path(p).stat().st_size>0 for p in files) and row.get('output_fingerprint')==signature(files,{})
        except (OSError,ValueError,TypeError):
            return False

    def _process_h5(self,path,stages):
        reports=[]
        stage1=self.config.get('stage1_metadata','')
        filtering=self.config.get('filtering_metadata','')
        result_tables=[]
        if stage1 and 'acceptreject' not in stages:
            result_tables.append(('acceptreject',stage1))
        if filtering and 'filtering' not in stages:
            result_tables.append(('filtering',filtering))
        for stage in ('acceptreject','filtering','sanity'):
            if stage not in stages or self.cancelled.is_set(): continue
            self.emit(f'{Path(path).name}: starting {stage}.',type='stage_started',stage=stage,input=path)
            keys={'python_executable','save_figures','resume','workers'}
            if stage=='acceptreject': keys.add('rscript_executable')
            if stage=='filtering': keys.update({'processed_suffix','rscript_executable'})
            if stage=='sanity': keys.update({'enabled_checks','default_bits','station_catalog','sanity_column_mappings','column_mappings','stations_sheet','stations_delimiter','stations_encoding'})
            cfg={k:self.config[k] for k in keys if k in self.config}
            cfg.update(stage_method_settings(self.config,stage))
            # Stage CSVs are internal checkpoints; one consolidated workbook is
            # published below for this HDF5, including rejected/skipped records.
            cfg.update(input_hdf5=path,cancel_file=str(self.cancel_file),export_stage_workbooks=False)
            if stage=='filtering': cfg['stage1_metadata']=stage1
            if stage=='sanity': cfg['filtering_metadata']=filtering
            inputs=[path]+([stage1] if stage=='filtering' else [filtering] if stage=='sanity' else [])
            engine=APP_ROOT/'engines'/stage
            if stage=='sanity': script=engine/'Sanity_checks_v9_app.py'
            elif stage=='acceptreject': script=engine/'stage1_parallel_v4_ncm.py'
            else: script=engine/'stage2_parallel_v5_ncm.py'
            inputs.extend(engine_inputs(engine))
            if stage in ('acceptreject','filtering'): inputs.append(str(APP_ROOT/'signalqc'/'figure_names_v1.py'))
            if stage=='sanity':
                if cfg.get('station_catalog'): inputs.append(cfg['station_catalog'])
                frame=read_table(filtering)
                for column in ('processed_hdf5','source_hdf5'):
                    if column in frame:
                        inputs.extend(str(p) for p in frame[column].dropna().unique() if str(p).strip())
            settings={k:v for k,v in cfg.items() if k not in ('resume','cancel_file','workers')}
            settings['rscript_executable']=self.config.get('rscript_executable','') if stage!='sanity' else ''
            digest=signature(inputs,settings)
            key,old=self.project.job(stage,path,digest)
            target=self._target(stage,Path(path).stem,digest,old)
            cfg['output_dir']=str(target)
            if self._cached(old):
                outputs=json.loads(old['outputs']); status='cached'
                self.emit(f'{Path(path).name}: {stage} already complete.')
            else:
                target.mkdir(parents=True,exist_ok=True)
                configfile=target/'run_config.json'; atomic_json(configfile,cfg)
                self.project.update_job(key,stage,path,digest,'running',output_dir=target)
                try:
                    outputs,status=self._external(stage,script,configfile,target)
                    if self.cancelled.is_set(): status='cancelled'
                    self.project.update_job(key,stage,path,digest,status,outputs,output_dir=target)
                except Exception as e:
                    # Preserve valid rows and explicit failure status in the
                    # consolidated report even if an external process failed.
                    outputs=self._stage_outputs(target)
                    status='failed'
                    self.project.update_job(key,stage,path,digest,'failed',outputs,message=str(e),output_dir=target)
                    self.emit(f'{Path(path).name}: {stage} failed: {e}',level='error')
            saved_settings=stage_method_settings(cfg,stage)
            if stage=='sanity': saved_settings={'enabled_checks':cfg.get('enabled_checks',[])}
            reports.append({'stage':stage,'input':path,'status':status,'output_files':outputs,
                            'scientific_settings':saved_settings})
            self.emit(f'{stage}: {status.replace("_"," ")}',type='stage_finished',stage=stage,input=path,status=status)
            for candidate in outputs:
                name=Path(candidate).name
                if stage=='acceptreject' and name.endswith('.meta.csv'):
                    result_tables.append(('acceptreject',candidate))
                elif stage=='acceptreject' and name=='method_execution.csv':
                    result_tables.append(('acceptreject_execution',candidate))
                elif stage=='filtering' and name.endswith('filmeta.csv'):
                    result_tables.append(('filtering',candidate))
                elif stage=='filtering' and name=='method_corners.csv':
                    result_tables.append(('method_corners',candidate))
                elif stage=='sanity' and (name.endswith('_sc.csv') or name.endswith('_sc_partial.csv')):
                    result_tables.append(('sanity',candidate))
            if stage=='acceptreject':
                status_table=target/'stage1_status.csv'
                if status_table.is_file(): result_tables.append(('stage1_status',str(status_table)))
            if status=='no_accepted_traces':
                if 'sanity' in stages:
                    reports.append({'stage':'sanity','input':path,'status':'skipped','reason':'No Stage 1 accepted traces were available for filtering; there are no processed traces to check.','output_files':[]})
                break
            if status in ('cancelled','failed'): break
            if stage=='acceptreject':
                stage1=next((p for p in outputs if p.endswith('.meta.csv')), next((p for p in outputs if p.endswith('meta.xlsx')),''))
                if not stage1: raise RuntimeError('Stage 1 produced no usable metadata. Inspect its log.')
            if stage=='filtering':
                filtering=next((p for p in outputs if p.endswith('filmeta.csv')),next((p for p in outputs if p.endswith('filmeta.xlsx')),''))
                if not filtering: raise RuntimeError('Filtering produced no metadata. Inspect its log.')
        if 'intensity' in stages and not self.cancelled.is_set():
            processed=next((p for r in reports if r.get('stage')=='filtering' and r.get('status') in ('completed','cached') for p in r.get('output_files',[]) if p.endswith(('.h5','.hdf5'))),'')
            if 'filtering' in stages and not processed:
                reports.append(dict(stage='intensity',input=path,status='skipped',message='Filtering produced no completed processed HDF5 for intensity measures.',output_files=[]))
            else:
                metadata=filtering if 'filtering' in stages else self.config.get('im_metadata','')
                reports.append(self._intensity_job(processed or path,metadata))
        for stage in stages:
            if stage=='ingestion': continue
            report=next((r for r in reports if r.get('stage')==stage),None)
            if not report or stage=='intensity' or report.get('status')=='skipped':
                self.emit(f'{stage}: '+str(report.get('status') if report else 'not run after earlier stage stopped'),
                          type='stage_finished',stage=stage,input=path,status=report.get('status','skipped') if report else 'not_run')
        if not result_tables:
            return reports
        try:
            report=publish_results(self.project.folder,path,result_tables,[s for s in stages if s!='intensity'],reports)
            reports.append(report)
            # Record the public output separately; never make stage resume
            # depend on the workbook's timestamp or display formatting.
            digest=signature([p for _,p in result_tables]+[path],{'stages':stages,'report_schema':'v2'})
            key,_=self.project.job('results',path,digest)
            self.project.update_job(key,'results',path,digest,report['status'],report['output_files'],output_dir=Path(report['output_files'][0]).parent)
        except Exception as e:
            reports.append({'stage':'results','input':path,'status':'failed','message':str(e),'output_files':[]})
            self.emit(f'{Path(path).name}: consolidated report could not be saved: {e}',level='error')
        return reports

    def _intensity_job(self,path,metadata):
        self.emit('Starting intensity measures.',type='stage_started',stage='intensity',input=path)
        from .intensity_v1 import run_intensity, metadata_file, MEASURES
        metadata=metadata_file(path,metadata)
        engine=APP_ROOT/'engines'/'intensity'
        inputs=[path]+([metadata] if metadata else [])+engine_inputs(engine)+[str(APP_ROOT/'signalqc'/'intensity_v1.py')]
        inputs.extend(str(p) for p in (APP_ROOT/'runtime'/'R-IM-library'/'RCTC').rglob('*') if p.is_file())
        settings={'im_measures':self.config.get('im_measures',list(MEASURES)),'rscript_executable':self.config.get('rscript_executable','')}
        digest=signature(inputs,settings)
        key,old=self.project.job('intensity',path,digest)
        target=self._target('intensity',Path(path).stem,digest,old)
        if self._cached(old):
            outputs=json.loads(old['outputs'])
            self.emit(f'{Path(path).name}: intensity measures already complete.')
            return dict(stage='intensity',input=path,status='cached',output_files=outputs)
        target.mkdir(parents=True,exist_ok=True)
        self.project.update_job(key,'intensity',path,digest,'running',output_dir=target)
        cfg=dict(self.config,input_hdf5=path,im_metadata=metadata,output_dir=str(target),
                 cancel_file=str(self.cancel_file),processing_signature=digest)
        try:
            result=run_intensity(cfg,self.progress,self.cancelled.is_set)
            self.project.update_job(key,'intensity',path,digest,result['status'],result.get('output_files',[]),result.get('message',''),output_dir=target)
            return result
        except Exception as exc:
            self.project.update_job(key,'intensity',path,digest,'failed',message=str(exc),output_dir=target)
            self.emit(f'{Path(path).name}: intensity measures failed: {exc}',level='error')
            return dict(stage='intensity',input=path,status='failed',message=str(exc),output_files=[])

    @staticmethod
    def _stage_outputs(target):
        return sorted(set(str(p.resolve()) for p in walk_files([target])
                          if p.suffix.lower() in ('.csv','.xlsx','.h5','.hdf5','.png')
                          and p.name!='normalized_stage1.csv'
                          and 'checkpoint' not in p.name.lower() and 'checkpoints' not in p.parts))

    def _external(self,stage,script,configfile,target):
        if not script.is_file(): raise FileNotFoundError(f'Engine unavailable: {script}')
        python_entry=stage=='sanity' or script.suffix.lower()=='.py'
        exe=self.config['python_executable'] if python_entry else self.config.get('rscript_executable','')
        if not Path(exe).is_file(): raise ValueError(f'Choose a valid {"Python" if python_entry else "Rscript"} executable in Runtime settings.')
        env=os.environ.copy()
        for inherited in ('PYTHONHOME','PYTHONPATH','R_HOME'):
            env.pop(inherited,None)
        env.update(R_LIBS_USER=str(APP_ROOT/'runtime'/'R-library'),RETICULATE_PYTHON=self.config['python_executable'],
                   PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PYTHONNOUSERSITE='1',
                   OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
        from .runtime_env_v1 import runtime_environment
        env=runtime_environment(env)
        # A deterministic Windows locale avoids warnings inherited from a Unix host.
        env.pop('LC_ALL',None); env.pop('LC_CTYPE',None); env.pop('LANG',None)
        self.emit(f'{stage}: processing {json.loads(configfile.read_text())["input_hdf5"]}')
        log=target/'run.log'
        stdout_summary={}
        with log.open('w',encoding='utf-8') as handle:
            process=subprocess.Popen([exe,str(script),str(configfile)],cwd=script.parent,env=env,
                                     stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            for line in process.stdout:
                handle.write(line); handle.flush()
                clean=line.strip()
                if not clean: continue
                structured=None
                if clean.startswith('SIGNALQC_EVENT '):
                    try: structured=json.loads(clean[len('SIGNALQC_EVENT '):])
                    except ValueError: pass
                elif clean.startswith('{'):
                    try:
                        parsed=json.loads(clean)
                        if isinstance(parsed,dict):
                            structured=parsed
                            if 'status' in parsed and ('output_files' in parsed or 'counts' in parsed): stdout_summary=parsed
                    except ValueError: pass
                if isinstance(structured,dict):
                    structured.setdefault('stage',stage)
                    structured.setdefault('message', f"{stage}: {structured.get('type',structured.get('status','progress'))}")
                    self.progress(structured)
                else: self.emit(clean,stage=stage)
            code=process.wait()
        summary=stdout_summary
        summaryfile=target/('sanity_summary.json' if stage=='sanity' else stage+'_summary.json')
        if summaryfile.exists():
            try:
                summary=json.loads(summaryfile.read_text(encoding='utf-8-sig'))
            except (OSError,ValueError) as exc:
                raise RuntimeError(f'{stage} summary is unreadable: {summaryfile}') from exc
        # Stage 2 explicitly uses exit 3 for per-trace/optional-method issues.
        # Only proceed when its own summary confirms completed usable rows.
        partial=(stage=='filtering' and code==3 and summary.get('status')=='completed_with_issues'
                 and int(summary.get('completed',0))+int(summary.get('rejected',0))>0)
        stage1_counts=summary.get('status_counts') or summary.get('counts') or {}
        partial_stage1=(stage=='acceptreject' and code==2 and summary.get('status')=='completed_with_issues'
                        and int(stage1_counts.get('accepted',0))+int(stage1_counts.get('rejected',0))>0)
        partial=partial or partial_stage1
        if code and not partial and not self.cancelled.is_set():
            raise RuntimeError(f'{stage} failed (exit {code}). See {log}')
        outputs=self._stage_outputs(target)
        if not outputs and not self.cancelled.is_set(): raise RuntimeError(f'{stage} did not produce results. See {log}')
        status='cancelled' if self.cancelled.is_set() else summary.get('status','completed')
        if partial: self.emit(f'{stage}: some traces reported issues. Continuing selected stages for the saved valid rows.',level='warning')
        return outputs,status
